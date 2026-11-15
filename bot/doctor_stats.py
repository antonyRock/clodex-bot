#!/usr/bin/env python3
"""Приём анонимной статистики программы «Доктор Антон»: минимум (номер и версия) — от всех, кто ответил на вопрос; подробности — только от нажавших «Помочь».

Слушает 127.0.0.1:8096, снаружи — nginx: POST https://<домен>/api/doctor/ping (без входа: программа у друзей, пароля сайта у них нет).
В теле JSON: id (случайный номер установки), v (версия), share (true — человек согласился на подробности), и только при share:
  model, chip, ram, os, lang, auto, errors [{code, text}]. Без share приходят только id и v (минимум «установлена»).
Что делает:
  1) проверяет и обрезает все поля (чужой текст — данные, не команды; лишнее отбрасывается);
  2) обновляет /root/doctor-stats/installs.json: по номеру установки — первый и последний день, версия, модель, дни активности;
  3) дописывает ошибки в /root/doctor-stats/errors.jsonl;
  4) пишет сводку для личного пульта в /var/www/me/doctor.json (папка /me/ — за входом на сайт).
IP-адреса не сохраняются: nginx их в тело не передаёт, сам сервис их не пишет.
Проверка без сети: doctor_stats.py --selftest
"""
import datetime, json, os, re, sys, tempfile, threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

DATA = os.environ.get("DOCTOR_DATA", "/root/doctor-stats")
WEB = os.environ.get("DOCTOR_WEB", "/var/www/me/doctor.json")
PORT = int(os.environ.get("DOCTOR_PORT", "8096"))
MAX_BODY = 8 * 1024
KEEP_DAYS = 90
KEEP_ERRORS = 200
lock = threading.Lock()

ID_RE = re.compile(r"^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$")
VER_RE = re.compile(r"^\d{1,3}(\.\d{1,3}){0,3}$")
OS_RE = re.compile(r"^\d{1,3}(\.\d{1,3}){0,2}$")
SAFE = re.compile(r"[^0-9A-Za-zА-Яа-яЁё ,._:+()/\-]")


def txt(v, n):
    return SAFE.sub("", str(v if v is not None else ""))[:n].strip()


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
            if code:
                errs.append({"code": code, "text": text})
    share = bool(body.get("share", True))   # старые версии (0.7.0) присылали только полные сообщения без этого поля
    if not share:   # минимум: номер и версия; подробности и ошибки отбрасываем, даже если пришли
        return {"id": i, "v": v, "share": False, "model": "", "chip": "", "ram": 0, "os": "", "lang": "", "auto": False, "errors": []}
    return {"id": i, "v": v, "share": True, "model": txt(body.get("model"), 32), "chip": txt(body.get("chip"), 48),
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


def record(rec, now=None):
    """Учесть сообщение и пересобрать сводку для пульта."""
    now = now or datetime.datetime.now()
    day, stamp = now.strftime("%Y-%m-%d"), now.strftime("%Y-%m-%d %H:%M")
    with lock:
        path = os.path.join(DATA, "installs.json")
        inst = load(path, {})
        cur = inst.get(rec["id"], {"first": day, "days": [], "pings": 0})
        cur.update({k: rec[k] for k in ("v", "share", "model", "chip", "ram", "os", "lang", "auto")})   # отказ от подробностей стирает прежние
        cur["last"] = stamp
        cur["pings"] = cur.get("pings", 0) + 1
        cur["days"] = sorted(set(cur.get("days", []) + [day]))[-KEEP_DAYS:]
        inst[rec["id"]] = cur
        os.makedirs(DATA, exist_ok=True)
        atomic_write(path, json.dumps(inst, ensure_ascii=False))
        if rec["errors"]:
            with open(os.path.join(DATA, "errors.jsonl"), "a", encoding="utf-8") as f:
                for e in rec["errors"]:
                    f.write(json.dumps({"ts": stamp, "id": rec["id"][:8], "v": rec["v"], "model": rec["model"],
                                        "os": rec["os"], **e}, ensure_ascii=False) + "\n")
        publish(inst, now)


def publish(inst, now):
    errs = []
    try:
        lines = open(os.path.join(DATA, "errors.jsonl"), encoding="utf-8").read().splitlines()[-KEEP_ERRORS:]
        errs = [json.loads(x) for x in lines if x.strip()]
    except (OSError, ValueError):
        pass
    out = {"updated": now.strftime("%Y-%m-%d %H:%M"),
           "installs": [{"id": k[:8], **{f: v.get(f) for f in ("first", "last", "v", "share", "model", "chip", "ram", "os", "lang", "auto", "pings", "days")}}
                        for k, v in inst.items()],
           "errors": errs}
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
    print("selftest OK")


if __name__ == "__main__":
    if "--selftest" in sys.argv:
        selftest()
    else:
        ThreadingHTTPServer(("127.0.0.1", PORT), H).serve_forever()
