#!/usr/bin/env python3
"""Статистика VPN (Xray) для личного пульта: трафик по людям и сервисам, сайты, активность, отказы, здоровье сервера.

Запускается таймером vpn-stats.timer раз в 5 минут, без ИИ.
- Байты: счётчики Xray (`xray api statsquery -reset`): user>>><имя>>>traffic — всего по человеку,
  outbound>>>s-<имя>-<сервис>>>traffic — по сервисам (выходы s-* и правила заводятся в конфиге Xray).
  Счётчики обнуляются при перезапуске Xray, поэтому их снимают часто и копят здесь по дням и часам.
- Сайты и время: новые строки журнала Xray (access.log) — число соединений по сайтам, по часам, отказы (-> block).
  Байтов по отдельным сайтам Xray не считает — только по сервисам.
- Адреса, с которых подключаются люди, не хранятся: только число разных сетей за день (по хэшу).
Результат: /root/.local/state/vpn/stats.json (последние KEEP_DAYS дней) и копия за WEB_DAYS дней в /var/www/me/vpn.json —
её подтягивает личный пульт на сайте (вкладка VPN), чтобы данные были свежими без пересборки страницы.
"""
import datetime, fcntl, hashlib, ipaddress, json, os, re, subprocess

STATE = "/root/.local/state/vpn"
OUT = os.path.join(STATE, "stats.json")
POS = os.path.join(STATE, "pos.json")
XRAY = "/usr/local/bin/xray"
API = "127.0.0.1:10085"
LOG = "/var/log/xray/access.log"
ERR = "/var/log/xray/error.log"
WEB = "/var/www/me/vpn.json"   # личный пульт на сайте подтягивает свежие данные отсюда (папка /me/ — за входом)
WEB_DAYS = 31
KEEP_DAYS = 62
TOP_SITES = 80          # сайтов на человека в день
RECENT_BLOCKS = 30

LINE = re.compile(r"^(\d{4})/(\d\d)/(\d\d) (\d\d):(\d\d):(\d\d)\.\d+ from (\S+) accepted (tcp|udp):(.+):(\d+) "
                  r"\[(\S+) (?:->|>>) (\S+)\] email: (\S+)")
SLD = {"co", "com", "net", "org", "gov", "ac", "edu"}


def site(host):
    """Сайт для отчёта: домен второго уровня (m.youtube.com → youtube.com); IP-адрес — «IP-адрес»."""
    host = host.strip("[]").lower().rstrip(".")
    try:
        ipaddress.ip_address(host)
        return "IP-адрес"
    except ValueError:
        pass
    p = host.split(".")
    if len(p) >= 3 and p[-2] in SLD and len(p[-1]) == 2:
        return ".".join(p[-3:])
    return ".".join(p[-2:])


def query(reset):
    cmd = [XRAY, "api", "statsquery", f"--server={API}"] + (["-reset"] if reset else [])
    r = subprocess.run(cmd, capture_output=True, text=True, timeout=20)
    if r.returncode:
        raise RuntimeError(r.stderr.strip()[:200])
    return [(s["name"], int(s.get("value", 0) or 0)) for s in json.loads(r.stdout or "{}").get("stat", [])]


def day_rec(data, user, d):
    u = data["users"].setdefault(user, {"days": {}, "last_seen": None, "blocked_recent": []})
    return u, u["days"].setdefault(d, {"up": 0, "down": 0, "svc": {}, "hb": [0] * 24, "conns": 0, "hc": [0] * 24,
                                       "blocked": 0, "sites": {}, "nets": [], "first": None, "last": None})


def add_bytes(data, stats, now):
    d, hr = now.strftime("%Y-%m-%d"), now.hour
    for name, v in stats:
        if not v:
            continue
        p = name.split(">>>")
        if p[0] == "user" and len(p) == 4:
            _, rec = day_rec(data, p[1], d)
            rec["up" if p[3] == "uplink" else "down"] += v
            rec["hb"][hr] += v
        elif p[0] == "outbound" and p[1].startswith("s-") and p[1].count("-") >= 2:
            user, svc = p[1][2:].rsplit("-", 1)
            _, rec = day_rec(data, user, d)
            rec["svc"][svc] = rec["svc"].get(svc, 0) + v


def read_log(data, pos):
    try:
        st = os.stat(LOG)
    except FileNotFoundError:
        return pos
    off = pos.get("off", 0) if pos.get("ino") == st.st_ino and pos.get("off", 0) <= st.st_size else 0
    with open(LOG, "rb") as f:
        f.seek(off)
        chunk = f.read()
    end = chunk.rfind(b"\n") + 1          # недописанную строку оставляем на следующий раз
    for raw in chunk[:end].decode("utf-8", "replace").splitlines():
        m = LINE.match(raw)
        if not m:
            continue
        y, mo, dd, hh, mi, ss, src, _proto, host, _port, _inb, outb, user = m.groups()
        src_ip = src.rsplit(":", 1)[0].strip("[]")
        if src_ip.startswith("127.") or src_ip == "::1":
            continue                        # проверки с самого сервера
        d, t = f"{y}-{mo}-{dd}", f"{hh}:{mi}"
        u, rec = day_rec(data, user, d)
        h = int(hh)
        rec["conns"] += 1
        rec["hc"][h] += 1
        s = site(host)
        rec["sites"][s] = rec["sites"].get(s, 0) + 1
        net = hashlib.sha1((".".join(src_ip.split(".")[:2]) if "." in src_ip else src_ip[:9]).encode()).hexdigest()[:8]
        if net not in rec["nets"]:
            rec["nets"].append(net)
        rec["first"] = rec["first"] or t
        rec["last"] = t
        ts = f"{d} {hh}:{mi}:{ss}"
        if not u["last_seen"] or ts > u["last_seen"]:
            u["last_seen"] = ts
        if outb == "block":
            rec["blocked"] += 1
            u["blocked_recent"] = (u["blocked_recent"] + [[ts, host.strip("[]")]])[-RECENT_BLOCKS:]
    return {"ino": st.st_ino, "off": off + end}


def health(now):
    h = {"checked": now.strftime("%Y-%m-%d %H:%M")}
    r = subprocess.run(["systemctl", "show", "xray", "-p", "ActiveState,ActiveEnterTimestamp,NRestarts"],
                       capture_output=True, text=True).stdout
    kv = dict(l.split("=", 1) for l in r.splitlines() if "=" in l)
    m = re.search(r"(\d{4}-\d\d-\d\d \d\d:\d\d)", kv.get("ActiveEnterTimestamp", ""))
    h.update({"active": kv.get("ActiveState") == "active", "since": m.group(1) if m else "",
              "restarts": int(kv.get("NRestarts", 0) or 0)})
    ss = subprocess.run(["ss", "-ltn"], capture_output=True, text=True).stdout
    ports = sorted({int(x) for x in re.findall(r"[\]\*:](\d+)\s", ss)})
    conf = {}
    try:
        conf = json.load(open("/usr/local/etc/xray/config.json"))
        port = conf["inbounds"][0]["port"]
    except Exception:
        port = None
    h["port"] = port
    h["listening"] = port in ports if port else False
    h["users"] = [c.get("email") for c in (conf.get("inbounds") or [{}])[0].get("settings", {}).get("clients", []) if c.get("email")]
    try:
        h["version"] = subprocess.run([XRAY, "version"], capture_output=True, text=True).stdout.split()[1]
    except Exception:
        h["version"] = ""
    errs, last = 0, ""
    since = (now - datetime.timedelta(hours=24)).strftime("%Y/%m/%d %H:%M:%S")
    try:
        for l in open(ERR, encoding="utf-8", errors="replace"):
            if l[:19] >= since and "[Error]" in l:
                errs, last = errs + 1, l.strip()[:200]
    except FileNotFoundError:
        pass
    h["errors_24h"], h["last_error"] = errs, last
    try:
        h["log_mb"] = round(os.path.getsize(LOG) / 1e6, 1)
    except OSError:
        h["log_mb"] = None
    return h


def main():
    os.makedirs(STATE, mode=0o700, exist_ok=True)
    lock = open(os.path.join(STATE, ".lock"), "w")
    fcntl.flock(lock, fcntl.LOCK_EX)
    now = datetime.datetime.now()
    try:
        data = json.load(open(OUT))
    except (FileNotFoundError, ValueError):
        data = {"users": {}, "split_since": now.strftime("%Y-%m-%d %H:%M")}
    try:
        pos = json.load(open(POS))
    except (FileNotFoundError, ValueError):
        pos = {}
    err = None
    try:
        add_bytes(data, query(reset=True), now)
    except Exception as e:                      # Xray не отвечает — журнал и здоровье всё равно записываем
        err = f"счётчики Xray: {e}"
    pos = read_log(data, pos)
    cut = (now - datetime.timedelta(days=KEEP_DAYS)).strftime("%Y-%m-%d")
    for u in data["users"].values():
        for d in [d for d in u["days"] if d < cut]:
            del u["days"][d]
        for rec in u["days"].values():
            if len(rec["sites"]) > TOP_SITES:
                rec["sites"] = dict(sorted(rec["sites"].items(), key=lambda x: -x[1])[:TOP_SITES])
    data["server"] = health(now)
    data["server"]["collector_error"] = err
    data["updated"] = now.strftime("%Y-%m-%d %H:%M")
    files = [(OUT, data, 0o600), (POS, pos, 0o600)]
    if os.path.isdir(os.path.dirname(WEB)):      # копия для личного пульта на сайте (за входом): последние WEB_DAYS дней
        cut_w = (now - datetime.timedelta(days=WEB_DAYS)).strftime("%Y-%m-%d")
        web = dict(data, users={k: dict(u, days={d: r for d, r in u["days"].items() if d >= cut_w})
                                 for k, u in data["users"].items()})
        files.append((WEB, web, 0o644))
    for path, obj, mode in files:
        tmp = path + ".tmp"
        with open(tmp, "w") as f:
            json.dump(obj, f, ensure_ascii=False, separators=(",", ":"))
        os.chmod(tmp, mode)
        os.replace(tmp, path)


if __name__ == "__main__":
    main()
