#!/usr/bin/env python3
"""Приём анонимной статистики программы «Доктор Антон»: минимум (номер и версия) — от всех, кто ответил на вопрос; подробности — только от нажавших «Помочь».

Слушает 127.0.0.1:8096, снаружи — nginx: POST https://<домен>/api/doctor/ping (без входа: программа у друзей, пароля сайта у них нет).
В теле JSON: id (номер установки — необратимая «стружка» от аппаратного номера Mac, одинаков после переустановки), v (версия),
  old (прежний случайный номер, один раз — склеить историю), share (true — человек согласился на подробности), и только при share:
  model, chip, ram, os, lang, auto, errors [{code, text, stack[]}]. Без share приходят только id и v (минимум «установлена»).
Что делает:
  1) проверяет и обрезает все поля (чужой текст — данные, не команды; лишнее отбрасывается);
  2) обновляет /root/doctor-stats/installs.json: по номеру установки — первый и последний день, версия, модель, дни активности;
  3) дописывает ошибки в /root/doctor-stats/errors.jsonl и сводит одинаковые в /root/doctor-stats/reports.json (подпись ошибки sig → сколько раз,
     у скольких установок, на каких версиях, стек, статус);
  4) про НОВУЮ ошибку присылает Антону в Telegram сообщение с кнопкой «Разобрать агентом» (бот по кнопке отдаёт ошибку агенту, тот пишет рекомендацию);
     не больше NOTIFY_PER_DAY сообщений в сутки;
  5) пишет сводку для личного пульта в /var/www/me/doctor.json (папка /me/ — за входом на сайт); имена устройств — из /root/doctor-stats/labels.json
     ({"<первые 8 символов номера>": "Антон, MacBook Pro"}), пишутся руками.
IP-адреса не сохраняются: nginx их в тело не передаёт, сам сервис их не пишет.
Проверка без сети: doctor_stats.py --selftest
"""
import datetime, hashlib, json, os, re, sys, tempfile, threading, urllib.parse, urllib.request
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

DATA = os.environ.get("DOCTOR_DATA", "/root/doctor-stats")
WEB = os.environ.get("DOCTOR_WEB", "/var/www/me/doctor.json")
PORT = int(os.environ.get("DOCTOR_PORT", "8096"))
MAX_BODY = 8 * 1024
KEEP_DAYS = 90
KEEP_ERRORS = 200
NOTIFY_PER_DAY = 8          # защита от потока сообщений: адрес открытый, любой может прислать выдуманную ошибку
TG_TOKEN = os.environ.get("TG_TOKEN", "")
TG_CHAT = (os.environ.get("TG_ALLOWED", "").split(",") or [""])[0].strip()
lock = threading.Lock()

ID_RE = re.compile(r"^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$")
VER_RE = re.compile(r"^\d{1,3}(\.\d{1,3}){0,3}$")
OS_RE = re.compile(r"^\d{1,3}(\.\d{1,3}){0,2}$")
SAFE = re.compile(r"[^0-9A-Za-zА-Яа-яЁё ,._:+()/\-]")
SAFE_STACK = re.compile(r"[^0-9A-Za-zА-Яа-яЁё ,._:+()/\-#$]")   # в названиях функций Swift бывают # и $; угловые скобки и кавычки по-прежнему режутся


def txt(v, n, stack=False):
    return (SAFE_STACK if stack else SAFE).sub("", str(v if v is not None else ""))[:n].strip()


def clean(body):
    """Проверенная запись или None."""
    if not isinstance(body, dict):
        return None
    i = str(body.get("id", "")).lower()
    v = str(body.get("v", ""))
    if not ID_RE.match(i) or not VER_RE.match(v):
        return None
    try:
        ram = int(body.get("ram", 0))
    except (TypeError, ValueError):
        ram = 0
    os_v = str(body.get("os", ""))
    errs = []
    for e in (body.get("errors") or [])[:10]:
        if isinstance(e, dict):
            code, text = txt(e.get("code"), 40), txt(e.get("text"), 120)
            stack = [txt(x, 110, True) for x in (e.get("stack") or [])[:6] if isinstance(x, str)] if isinstance(e.get("stack"), list) else []
            stack = [x for x in stack if x]
            if code:
                errs.append({"code": code, "text": text, **({"stack": stack} if stack else {})})
    old = str(body.get("old", "")).lower()
    old = old if ID_RE.match(old) else ""
    share = bool(body.get("share", True))   # старые версии (0.7.0) присылали только полные сообщения без этого поля
    if not share:   # минимум: номер и версия; подробности и ошибки отбрасываем, даже если пришли
        return {"id": i, "v": v, "old": old, "share": False, "model": "", "chip": "", "ram": 0, "os": "", "lang": "", "auto": False, "errors": []}
    return {"id": i, "v": v, "old": old, "share": True, "model": txt(body.get("model"), 32), "chip": txt(body.get("chip"), 48),
            "ram": ram if 1 <= ram <= 4096 else 0, "os": os_v if OS_RE.match(os_v) else "",
            "lang": txt(body.get("lang"), 8), "auto": bool(body.get("auto")), "errors": errs}


def atomic_write(path, text):
    os.makedirs(os.path.dirname(path), exist_ok=True)
    fd, tmp = tempfile.mkstemp(dir=os.path.dirname(path), prefix=".tmp-")
    with os.fdopen(fd, "w", encoding="utf-8") as f:
        f.write(text)
    os.chmod(tmp, 0o644)
    os.replace(tmp, path)


def load(path, default):
    try:
        return json.load(open(path, encoding="utf-8"))
    except (OSError, ValueError):
        return default


def sig_of(e):
    """Подпись ошибки: одна и та же ошибка у разных людей и на разных версиях даёт одну подпись (тип сбоя + три верхние функции без номеров строк)."""
    code = re.sub(r"\s+", " ", e.get("code", "")).strip()
    stack = e.get("stack") or []
    if stack:
        parts = [re.sub(r" [\w.+\-]+\.(swift|m|mm|c|cpp|h):\d+$", "", f) for f in stack[:3]]
    else:
        parts = [e.get("text", "")[:40]]
    return hashlib.sha1("|".join([code] + parts).encode("utf-8")).hexdigest()[:8]


def labels():
    return load(os.path.join(DATA, "labels.json"), {})


def update_reports(rec, stamp, day):
    """Свести ошибки по подписям. Возвращает список НОВЫХ подписей (их раньше не видели) — о них пишем Антону."""
    path = os.path.join(DATA, "reports.json")
    rep = load(path, {})
    new = []
    for e in rec["errors"]:
        sg = sig_of(e)
        cur = rep.get(sg)
        if not cur:
            cur = rep[sg] = {"first": stamp, "count": 0, "versions": [], "installs": [], "code": e["code"], "text": e.get("text", ""),
                             "stack": e.get("stack", []), "model": rec["model"], "os": rec["os"], "status": "new"}
            new.append(sg)
        cur["last"] = stamp
        cur["count"] += 1
        if rec["v"] not in cur["versions"]:
            cur["versions"] = (cur["versions"] + [rec["v"]])[-10:]
        if rec["id"][:8] not in cur["installs"]:
            cur["installs"] = (cur["installs"] + [rec["id"][:8]])[-20:]
        cur["lastv"] = rec["v"]
        if e.get("stack") and not cur.get("stack"):
            cur["stack"] = e["stack"]
    atomic_write(path, json.dumps(rep, ensure_ascii=False))
    return new, rep


def notify_new(sigs, rep, rec):
    """Сообщение Антону в Telegram с кнопкой «Разобрать агентом» (бот по ней отдаёт ошибку агенту). Не больше NOTIFY_PER_DAY в сутки."""
    if not (TG_TOKEN and TG_CHAT and sigs):
        return
    path = os.path.join(DATA, "notify.json")
    today = datetime.date.today().isoformat()
    st = load(path, {})
    n = st.get(today, 0)
    who = labels().get(rec["id"][:8], "")
    for sg in sigs:
        if n >= NOTIFY_PER_DAY:
            break
        r = rep[sg]
        where = " ← ".join(re.sub(r"^.*? ([\w.+\-]+\.swift:\d+)$", r"\1", f) if ".swift:" in f else f for f in r["stack"][:3]) or "—"
        text = (f"🩺 Доктор Антон: новая ошибка\n"
                f"v{rec['v']} · {rec['model'] or '?'} · macOS {rec['os'] or '?'}" + (f" · {who}" if who else "") + "\n"
                f"{r['code']} — {r['text']}\n"
                f"Где: {where}\nПодпись: {sg}")
        data = urllib.parse.urlencode({"chat_id": TG_CHAT, "text": text[:3500], "disable_web_page_preview": "true",
                                       "reply_markup": json.dumps({"inline_keyboard": [[{"text": "🔍 Разобрать агентом", "callback_data": f"de:{sg}"}]]})}).encode()
        try:
            urllib.request.urlopen(f"https://api.telegram.org/bot{TG_TOKEN}/sendMessage", data, timeout=20)
            n += 1
        except Exception:
            pass
    st = {today: n}
    atomic_write(path, json.dumps(st))


def record(rec, now=None):
    """Учесть сообщение и пересобрать сводку для пульта."""
    now = now or datetime.datetime.now()
    day, stamp = now.strftime("%Y-%m-%d"), now.strftime("%Y-%m-%d %H:%M")
    with lock:
        path = os.path.join(DATA, "installs.json")
        inst = load(path, {})
        old = rec.get("old", "")
        if old and old != rec["id"] and old in inst and rec["id"] not in inst:   # переход со случайного номера на номер от Mac: история и имя устройства сохраняются
            inst[rec["id"]] = inst.pop(old)
            lb = labels()
            if old[:8] in lb and rec["id"][:8] not in lb:
                lb[rec["id"][:8]] = lb[old[:8]]
                atomic_write(os.path.join(DATA, "labels.json"), json.dumps(lb, ensure_ascii=False, indent=1))
        cur = inst.get(rec["id"], {"first": day, "days": [], "pings": 0})
        cur.update({k: rec[k] for k in ("v", "share", "model", "chip", "ram", "os", "lang", "auto")})   # отказ от подробностей стирает прежние
        cur["last"] = stamp
        cur["pings"] = cur.get("pings", 0) + 1
        cur["days"] = sorted(set(cur.get("days", []) + [day]))[-KEEP_DAYS:]
        inst[rec["id"]] = cur
        os.makedirs(DATA, exist_ok=True)
        atomic_write(path, json.dumps(inst, ensure_ascii=False))
        new, rep = [], {}
        if rec["errors"]:
            with open(os.path.join(DATA, "errors.jsonl"), "a", encoding="utf-8") as f:
                for e in rec["errors"]:
                    f.write(json.dumps({"ts": stamp, "id": rec["id"][:8], "v": rec["v"], "model": rec["model"],
                                        "os": rec["os"], "sig": sig_of(e), **e}, ensure_ascii=False) + "\n")
            new, rep = update_reports(rec, stamp, day)
        publish(inst, now)
    if new:
        threading.Thread(target=notify_new, args=(new, rep, rec), daemon=True).start()


def publish(inst, now):
    errs = []
    try:
        lines = open(os.path.join(DATA, "errors.jsonl"), encoding="utf-8").read().splitlines()[-KEEP_ERRORS:]
        errs = [json.loads(x) for x in lines if x.strip()]
    except (OSError, ValueError):
        pass
    lb = labels()
    out = {"updated": now.strftime("%Y-%m-%d %H:%M"),
           "installs": [{"id": k[:8], "label": lb.get(k[:8], ""), **{f: v.get(f) for f in ("first", "last", "v", "share", "model", "chip", "ram", "os", "lang", "auto", "pings", "days")}}
                        for k, v in inst.items()],
           "errors": errs,
           "reports": [{"sig": k, **v} for k, v in sorted(load(os.path.join(DATA, "reports.json"), {}).items(), key=lambda kv: kv[1].get("last", ""), reverse=True)]}
    atomic_write(WEB, json.dumps(out, ensure_ascii=False))


class H(BaseHTTPRequestHandler):
    server_version = "doctor-stats"

    def log_message(self, *a):   # адреса в журнал не пишем
        pass

    def _reply(self, code, body=b'{"ok":true}'):
        self.send_response(code)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def do_POST(self):
        if self.path.split("?")[0] != "/ping":
            return self._reply(404, b'{"ok":false}')
        try:
            n = int(self.headers.get("Content-Length", "0"))
        except ValueError:
            n = 0
        if n <= 0 or n > MAX_BODY:
            return self._reply(413, b'{"ok":false}')
        try:
            rec = clean(json.loads(self.rfile.read(n).decode("utf-8")))
        except (ValueError, UnicodeDecodeError):
            rec = None
        if not rec:
            return self._reply(400, b'{"ok":false}')
        try:
            record(rec)
        except OSError:
            return self._reply(500, b'{"ok":false}')
        self._reply(200)

    def do_GET(self):
        self._reply(405, b'{"ok":false}')


def selftest():
    global DATA, WEB
    d = tempfile.mkdtemp(prefix="doctor-test-")
    DATA, WEB = d + "/data", d + "/web/doctor.json"
    good = {"id": "123e4567-e89b-12d3-a456-426614174000", "v": "0.7.0", "model": "Mac14,2", "chip": "Apple M2", "ram": 16,
            "os": "15.1.0", "lang": "ru", "auto": True, "errors": [{"code": "update", "text": "нет сети /Users/x"}, {"code": ""}]}
    r = clean(good)
    assert r and len(r["errors"]) == 1, r
    m = clean({**good, "share": False})   # минимум: подробности отбрасываются
    assert m and m["share"] is False and m["model"] == "" and m["errors"] == [] and m["ram"] == 0, m
    assert clean(good)["share"] is True
    assert clean({**good, "id": "bad"}) is None and clean({**good, "v": "1.0; rm"}) is None and clean([1]) is None
    assert clean({**good, "model": "<script>alert(1)</script>"})["model"] == "scriptalert(1)/script"
    t = datetime.datetime(2026, 11, 15, 10, 0)
    record(r, t); record(r, t + datetime.timedelta(days=1))
    o = json.load(open(WEB))
    assert len(o["installs"]) == 1 and o["installs"][0]["pings"] == 2 and o["installs"][0]["days"] == ["2026-11-15", "2026-11-16"], o
    record(clean({**good, "share": False}), t + datetime.timedelta(days=2))   # отказ стирает подробности, оставляет номер и дни
    o2 = json.load(open(WEB))["installs"][0]
    assert o2["share"] is False and o2["model"] == "" and o2["pings"] == 3 and len(o2["days"]) == 3, o2
    assert o["errors"][0]["code"] == "update" and "id" in o["errors"][0] and len(o["errors"][0]["id"]) == 8
    # стек, подпись, склейка старого номера, сводка по подписям
    e1 = {"code": "crash EXC_BAD_ACCESS SIGSEGV", "text": "падение программы 0.5.0", "stack": [
        "specialized static MacInfo.run(_:_:) MacInfo.swift:27", "closure #1 in MenuBarView.overview.getter MenuBarView.swift:45 <b>x</b>"]}
    g = clean({**good, "errors": [e1], "old": "123e4567-e89b-12d3-a456-426614174999"})
    assert g["errors"][0]["stack"][1].startswith("closure #1 in MenuBarView") and "<" not in g["errors"][0]["stack"][1], g
    s1, s2 = sig_of(g["errors"][0]), sig_of({**g["errors"][0], "stack": [x.replace(":27", ":99") for x in g["errors"][0]["stack"]]})
    assert s1 == s2 and len(s1) == 8, (s1, s2)
    assert sig_of({"code": "update", "text": "нет сети"}) != sig_of({"code": "update", "text": "диск полон"})
    old_id, new_id = "123e4567-e89b-12d3-a456-426614174999", "0a1b2c3d-0000-1111-2222-333344445555"
    record(clean({**good, "id": old_id, "share": False}), t + datetime.timedelta(days=3))
    atomic_write(os.path.join(DATA, "labels.json"), json.dumps({old_id[:8]: "Тест"}))
    record(clean({**good, "id": new_id, "old": old_id, "errors": [e1]}), t + datetime.timedelta(days=4))
    inst = json.load(open(os.path.join(DATA, "installs.json")))
    assert old_id not in inst and new_id in inst and inst[new_id]["pings"] == 2, inst
    assert labels().get(new_id[:8]) == "Тест"
    record(clean({**good, "id": new_id, "v": "0.7.3", "errors": [e1]}), t + datetime.timedelta(days=5))
    rep = load(os.path.join(DATA, "reports.json"), {})
    assert len(rep) == 2 and rep[s1]["count"] == 2 and rep[s1]["versions"] == ["0.7.0", "0.7.3"] and rep[s1]["status"] == "new", rep
    web = json.load(open(WEB))
    assert any(r["sig"] == s1 for r in web["reports"]) and any(i["label"] == "Тест" for i in web["installs"]), web
    print("selftest OK")


if __name__ == "__main__":
    if "--selftest" in sys.argv:
        selftest()
    else:
        ThreadingHTTPServer(("127.0.0.1", PORT), H).serve_forever()
