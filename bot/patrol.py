#!/usr/bin/env python3
"""Патруль безопасности сервера — часть сторожа (guard.py вызывает check()).

Только жёсткие правила, без ИИ: собирает факты, сравнивает с «нормой» и сообщает об отклонениях.
Сам ничего не чинит. Журналы читает как данные (текст в них может быть чужим).

  patrol.py init    запомнить текущее состояние как норму (после ручной проверки)
  patrol.py accept  принять изменения как новую норму (то же, что init)
  patrol.py print   показать находки, ничего не отправляя и не запоминая
Из guard.py: check("daily") — только новое и напоминания раз в 7 дней; check("weekly") — всё открытое + сводка.
"""
import glob, hashlib, json, os, re, subprocess, sys
from datetime import date, datetime

STATE_DIR = os.environ.get("PATROL_STATE", "/root/.local/state/patrol")
STATE_FILE = os.path.join(STATE_DIR, "state.json")
FACTS_FILE = os.path.join(STATE_DIR, "facts.json")   # для недельного разбора ИИ: без секретов
MANIFEST = "/root/.config/agent-deploy.json"          # что выложил deploy.py: путь -> sha256
DOMAIN = os.environ.get("PATROL_DOMAIN", "__DOMAIN__")
XRAY_LOG = "/var/log/xray/access.log"
TOTP = "/root/brain-auth/totp.json"                # файл второго фактора входа на сайт
REMIND_DAYS = 7          # как часто напоминать об открытой находке
UPDATE_GRACE_DAYS = 7   # сколько дней обновления безопасности могут ждать до тревоги

# файлы, изменение которых — повод для тревоги (обычные файлы, не ссылки)
WATCH_GLOBS = [
    "/root/.ssh/authorized_keys", "/etc/ssh/sshd_config", "/etc/ssh/sshd_config.d/*",
    "/etc/cron.d/*", "/var/spool/cron/crontabs/*", "/etc/sudoers", "/etc/sudoers.d/*",
    "/etc/nginx/nginx.conf", "/etc/nginx/sites-available/*", "/etc/nginx/conf.d/*",
    "/etc/systemd/system/*.service", "/etc/systemd/system/*.timer", "/etc/systemd/system/*.path",
    "/etc/systemd/system/*.d/*", "/etc/fail2ban/jail.local", "/etc/fail2ban/jail.d/*",
    "/root/tg-bot/*.py", "/root/brain-auth/*.py", "/root/night/*", "/usr/local/bin/*",
    "/usr/local/etc/xray/config.json", "/root/.bashrc", "/root/.profile",
]
# файлы с секретами: права должны быть только у владельца
SECRET_GLOBS = ["/root/.config/*.env*", "/root/.config/agent-settings.json", "/root/brain-auth/*.json",
                "/root/.ssh/id_*"]


def run(cmd, timeout=60):
    try:
        return subprocess.run(cmd, capture_output=True, text=True, timeout=timeout).stdout
    except Exception:
        return ""


def sha(p, full=False):
    try:
        d = hashlib.sha256(open(p, "rb").read()).hexdigest()
    except OSError:
        return None
    return d if full else d[:16]


def today():
    return date.today()


# ---------- сбор фактов ----------

def listeners():
    """Открытые наружу порты: {proto:port:процесс}. Случайные UDP-порты syncthing сводим в один."""
    res = set()
    for line in run(["ss", "-tulnHp"]).splitlines():
        f = line.split()
        if len(f) < 5:
            continue
        addr, _, port = f[4].rpartition(":")
        if addr.startswith(("127.", "[::1]")):
            continue
        m = re.search(r'\(\("([^"]+)"', line)
        proc = m.group(1) if m else "?"
        if f[0] == "udp" and proc == "syncthing":
            port = "*"
        res.add(f"{f[0]}:{port}:{proc}")
    return sorted(res)


def sshd_conf():
    keys = ("passwordauthentication", "kbdinteractiveauthentication", "pubkeyauthentication", "permitrootlogin", "port")
    d = {}
    for l in run(["sshd", "-T"]).splitlines():
        k, _, v = l.partition(" ")
        if k in keys:
            d[k] = v.strip()
    return d


def ufw():
    out = run(["ufw", "status"])
    rules = sorted({re.sub(r"\s+", " ", l.strip()) for l in out.splitlines() if re.search(r"\b(ALLOW|DENY|LIMIT|REJECT)\b", l)})
    return "Status: active" in out, rules


def login_users():
    res = []
    try:
        for l in open("/etc/passwd"):
            p = l.strip().split(":")
            if len(p) >= 7 and p[6].endswith(("sh",)) and not p[6].endswith("nologin"):
                res.append(p[0])
    except OSError:
        pass
    return sorted(res)


def manifest():
    try:
        return json.load(open(MANIFEST))
    except Exception:
        return {}


def watched_files(man):
    """Файлы вне списка выкладки: путь -> короткий хэш."""
    paths = set()
    for g in WATCH_GLOBS:
        paths |= set(glob.glob(g))
    out = {}
    for p in sorted(paths):
        if ".bak" in p or "__pycache__" in p or os.path.islink(p) or not os.path.isfile(p) or p in man:
            continue
        out[p] = sha(p)
    return out


def loose_secrets():
    bad = []
    for g in SECRET_GLOBS:
        for p in glob.glob(g):
            if os.path.isfile(p) and not p.endswith(".pub") and os.stat(p).st_mode & 0o077:
                bad.append(f"{p} (права {oct(os.stat(p).st_mode & 0o777)[2:]})")
    return sorted(bad)


def updates():
    out = run(["apt", "list", "--upgradable"], timeout=120)
    sec = [l.split("/")[0] for l in out.splitlines() if "-security" in l]
    return sec


def reboot_age_days():
    p = "/var/run/reboot-required"
    return (today() - date.fromtimestamp(os.path.getmtime(p))).days if os.path.exists(p) else None


def ssh_logins(hours):
    """Успешные входы и число попыток подбора за период. Читаем как данные."""
    out = run(["journalctl", "-u", "ssh", "--since", f"-{hours}h", "--no-pager", "-o", "cat"], timeout=90)
    ok = re.findall(r"Accepted (\S+) for (\S+) from (\S+) port \d+ ssh2(?:: (\S+ \S+))?", out)
    tries = len(re.findall(r"Invalid user|Failed password|authentication failure", out))
    return ok, tries


def f2b():
    out = run(["fail2ban-client", "status", "sshd"])
    if "Currently banned" not in out:
        return None
    g = lambda name: int(re.search(name + r":\s*(\d+)", out).group(1))
    return {"banned_now": g("Currently banned"), "banned_total": g("Total banned"), "failed_total": g("Total failed")}


def xray_domains():
    """Адреса, к которым ходили через сервер: {основной домен: дата первого появления в журнале}."""
    seen = {}
    try:
        data = open(XRAY_LOG, errors="ignore").read()[-5_000_000:]
    except OSError:
        return seen
    for m in re.finditer(r"^(\d{4})/(\d\d)/(\d\d) \S+ from \S+ accepted (?:tcp|udp):(\S+?):\d+ ", data, re.M):
        host = m.group(4)
        if not re.fullmatch(r"[\d.]+|\[.*\]", host):
            parts = host.split(".")
            host = ".".join(parts[-2:]) if len(parts) >= 2 else host
        seen.setdefault(host, f"{m.group(1)}-{m.group(2)}-{m.group(3)}")
    return seen


def web_code():
    return run(["curl", "-s", "-o", "/dev/null", "-m", "15", "-w", "%{http_code}", f"https://{DOMAIN}/"], timeout=25).strip()


# ---------- состояние ----------

def load_state():
    try:
        return json.load(open(STATE_FILE))
    except Exception:
        return {}


def save_state(st):
    os.makedirs(STATE_DIR, mode=0o700, exist_ok=True)
    tmp = STATE_FILE + ".tmp"
    with open(os.open(tmp, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600), "w") as f:
        json.dump(st, f, ensure_ascii=False, indent=1)
    os.replace(tmp, STATE_FILE)


def learn(st):
    """Записать текущее состояние как норму."""
    man = manifest()
    active, rules = ufw()
    ok, _ = ssh_logins(168)
    st["baseline"] = {
        "listeners": listeners(), "sshd": sshd_conf(), "ufw": rules, "files": watched_files(man),
        "login_users": login_users(), "totp": os.path.exists(TOTP), "fingerprints": sorted({fp for _, _, _, fp in ok if fp}),
    }
    st["known_ips"] = sorted(set(st.get("known_ips", [])) | {ip for _, _, ip, _ in ok})
    st["known_domains"] = sorted(set(st.get("known_domains", [])) | set(xray_domains()))
    st["findings"] = {}
    st["since"] = {}
    st.setdefault("new_domains", {})
    st.setdefault("new_ips", [])
    return st


# ---------- проверки ----------

def collect(mode_hours):
    """Возвращает (находки {ключ: текст}, сведения для сводки)."""
    st = load_state()
    base = st.get("baseline")
    if not base:
        return {"no-baseline": "норма не записана — выполнить на сервере: python3 /root/tg-bot/patrol.py init"}, {}, st
    f = {}
    info = {}

    cur = set(listeners())
    for x in sorted(cur - set(base["listeners"])):
        f[f"port:{x}"] = f"новый открытый порт {x} (проверить, что это ожидаемая служба)"
    for x in sorted(set(base["listeners"]) - cur):
        f[f"portgone:{x}"] = f"пропал порт {x} — служба не слушает"
    info["listeners"] = sorted(cur)

    ssh = sshd_conf()
    if not ssh:
        f["sshd-read"] = "не удалось прочитать настройки SSH"
    for k, want in (("passwordauthentication", "no"), ("kbdinteractiveauthentication", "no"), ("pubkeyauthentication", "yes")):
        if ssh and ssh.get(k) != want:
            f[f"sshd:{k}"] = f"SSH: {k} = {ssh.get(k)} (должно быть {want})"
    for k in ("permitrootlogin", "port"):
        if ssh and ssh.get(k) != base["sshd"].get(k):
            f[f"sshd:{k}"] = f"SSH: {k} изменилось {base['sshd'].get(k)} → {ssh.get(k)}"
    info["sshd"] = ssh

    active, rules = ufw()
    if not active:
        f["ufw-off"] = "файрвол ufw выключен"
    for r in sorted(set(rules) - set(base["ufw"])):
        f[f"ufw+:{r}"] = f"ufw: добавлено правило «{r}»"
    for r in sorted(set(base["ufw"]) - set(rules)):
        f[f"ufw-:{r}"] = f"ufw: убрано правило «{r}»"
    info["ufw_rules"] = rules

    nu = login_users()
    for u in sorted(set(nu) - set(base["login_users"])):
        f[f"user:{u}"] = f"появился пользователь с входом в систему: {u}"

    man = manifest()
    now = watched_files(man)
    for p, h in now.items():
        if p not in base["files"]:
            f[f"file:{p}"] = f"новый файл под наблюдением: {p}"
        elif base["files"][p] != h:
            f[f"file:{p}"] = f"изменён файл: {p}"
    for p in base["files"]:
        if p not in now and not os.path.exists(p):
            f[f"file:{p}"] = f"удалён файл: {p}"
    for p, h in man.items():
        if os.path.isfile(p) and sha(p, True) != h:
            f[f"drift:{p}"] = f"файл изменён в обход деплоя: {p}"

    loose = loose_secrets()
    if loose:
        f["secrets-perms"] = "файлы с секретами доступны не только владельцу: " + "; ".join(loose[:5])

    if base.get("totp", True) and not os.path.exists(TOTP):   # тревога, только если фактор был включён и пропал
        f["totp-off"] = "второй фактор на входе в VS Code выключен (пропал totp.json)"

    code = web_code()
    if code not in ("301", "302", "303", "307", "401", "403"):
        f["web-open"] = f"главная страница сайта отвечает кодом {code} (ожидается переход на вход)"

    sec = updates()
    since = st.get("since", {})
    if sec:
        d0 = date.fromisoformat(since.get("updates", today().isoformat()))
        if (today() - d0).days >= UPDATE_GRACE_DAYS:
            f["updates"] = f"ждут обновления безопасности ({len(sec)} шт.) уже {(today() - d0).days} дн."
    ra = reboot_age_days()
    if ra is not None and ra >= UPDATE_GRACE_DAYS:
        f["reboot"] = f"нужна перезагрузка сервера уже {ra} дн. (после обновлений)"
    info["updates"] = len(sec)
    info["reboot_days"] = ra

    stats = f2b()
    if stats is None:
        f["fail2ban"] = "fail2ban: защита SSH (sshd) не работает"
    info["fail2ban"] = stats

    logins, tries = ssh_logins(mode_hours)
    for method, user, ip, fp in logins:
        if user != "root":
            f[f"ssh-user:{user}"] = f"вход по SSH под пользователем {user} с {ip}"
        if method != "publickey":
            f[f"ssh-method:{method}"] = f"вход по SSH способом {method} (ожидается только ключ)"
        if fp and fp not in base["fingerprints"]:
            f[f"ssh-key:{fp}"] = f"вход по SSH с неизвестным ключом {fp} (адрес {ip})"
    info["ssh_logins"] = len(logins)
    info["ssh_tries"] = tries
    info["new_ips"] = sorted({ip for _, _, ip, _ in logins} - set(st.get("known_ips", [])))

    dom = xray_domains()
    info["new_domains"] = {d: dt for d, dt in dom.items() if d not in st.get("known_domains", [])}
    return f, info, st


def check(mode):
    """Для guard.py. Возвращает (ok, bad, notes, commit). commit() вызвать после успешной отправки."""
    weekly = mode in ("weekly", "print")
    f, info, st = collect(192 if weekly else 26)
    prev = st.get("findings", {})
    t = today()

    if weekly:
        send = dict(f)
    else:
        send = {}
        for k, txt in f.items():
            p = prev.get(k)
            if not p:
                send[k] = txt
            elif (t - date.fromisoformat(p["last_sent"])).days >= REMIND_DAYS:
                send[k] = txt + " (напоминание)"
    resolved = [prev[k]["text"] for k in prev if k not in f]

    bad = ["🔐 " + x for x in send.values()]
    notes = [f"🔐 Устранено: {x}" for x in resolved]
    ok = []
    if weekly:
        if "no-baseline" not in f:
            if not f:
                ok.append(f"🔐 Патруль: отклонений от нормы нет (открытых наружу портов {len(info.get('listeners', []))}, SSH только по ключу, файрвол включён)")
            fb = info.get("fail2ban")
            if fb:
                notes.append(f"🔐 SSH за неделю: {info['ssh_logins']} успешных входов, {info['ssh_tries']} попыток подбора; fail2ban заблокировал всего {fb['banned_total']}")
            if info.get("updates"):
                notes.append(f"🔐 Ждут обновлений безопасности: {info['updates']} шт.")
            if info.get("new_ips"):
                notes.append("🔐 Новые адреса входа по SSH: " + ", ".join(info["new_ips"][:10]))
            nd = info.get("new_domains")
            if nd:
                names = sorted(nd)
                notes.append(f"🔐 Новые адреса через сервер (VPN), их нет в списке известных ({len(names)}): " + ", ".join(names[:30]) + (" …" if len(names) > 30 else ""))

    def commit():
        if mode == "print" or "no-baseline" in f:
            return
        st2 = load_state()
        fresh = {}
        for k, txt in f.items():
            old = prev.get(k, {})
            fresh[k] = {"text": txt, "first": old.get("first", t.isoformat()),
                        "last_sent": t.isoformat() if k in send else old.get("last_sent", t.isoformat())}
        st2["findings"] = fresh
        since2 = st2.setdefault("since", {})
        if info.get("updates"):
            since2.setdefault("updates", t.isoformat())
        else:
            since2.pop("updates", None)
        st2["known_ips"] = sorted(set(st2.get("known_ips", [])) | set(info.get("new_ips", [])))
        if weekly:   # новые адреса сообщили в сводке — теперь они известные
            st2["known_domains"] = sorted(set(st2.get("known_domains", [])) | set(info.get("new_domains", {})))
        facts = {"time": datetime.now().isoformat(timespec="minutes"), "findings": list(f.values()), "info": info}
        os.makedirs(STATE_DIR, mode=0o700, exist_ok=True)
        with open(os.open(FACTS_FILE, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600), "w") as fh:
            json.dump(facts, fh, ensure_ascii=False, indent=1)
        save_state(st2)

    return ok, bad, notes, commit


if __name__ == "__main__":
    cmd = sys.argv[1] if len(sys.argv) > 1 else "print"
    if cmd in ("init", "accept"):
        st = learn(load_state())
        save_state(st)
        b = st["baseline"]
        print("Норма записана.")
        print("Порты наружу:", ", ".join(b["listeners"]))
        print("SSH:", b["sshd"])
        print("Правил ufw:", len(b["ufw"]), "| файлов под наблюдением:", len(b["files"]), "| ключей SSH:", len(b["fingerprints"]))
        print("Известных адресов входа:", len(st["known_ips"]), "| известных доменов через сервер:", len(st["known_domains"]))
    else:
        ok, bad, notes, _ = check("print")
        print("\n".join([f"⚠️ {x}" for x in bad] + [f"✅ {x}" for x in ok] + notes) or "Ничего.")
