#!/usr/bin/env python3
"""Сторож облачного агента. Проверяет оплаты, сроки и работу служб, пишет в Telegram.
Запуск: guard.py daily  — сообщает только о проблемах;
        guard.py weekly — полная сводка (даже если всё хорошо)."""
import json, os, re, shutil, subprocess, sys, urllib.parse, urllib.request
from datetime import date, datetime, timezone

TOKEN = os.environ["TG_TOKEN"]
CHAT = os.environ.get("TG_ALLOWED", "").split(",")[0]
DOMAIN = "__DOMAIN__"
WORKDIR = "__WORKDIR__"
SERVICES = ["brief.timer", "clean-screens.timer", "nginx", "code-server@root", "brain-auth", "tg-bot", "syncthing@root", "fail2ban", "me-publish.path", "xray"]
# Баланс VDSina: известная точка и расход в день. После пополнения обновить (или скажите агенту).
BALANCE = {"date": "__ДАТА_ПОПОЛНЕНИЯ__", "usd": __БАЛАНС_USD__, "per_day": 0.67}
CLAUDE_TOKEN_UNTIL = "__ДАТА_ОКОНЧАНИЯ_КЛЮЧА__"
WARN_DAYS = 30
TASKS_CHECK = os.path.join(WORKDIR, "04_Системные файлы", "__ФАЙЛ_ПРОВЕРКИ_ЗАДАЧ__")  # единая база задач


def run(cmd, timeout=60):
    try:
        return subprocess.run(cmd, capture_output=True, text=True, timeout=timeout).stdout
    except Exception as e:
        return f"ERR {e}"


def days_to(d):
    return (d - date.today()).days


def check():
    ok, bad = [], []

    # оплаты и сроки
    left = BALANCE["usd"] - BALANCE["per_day"] * (date.today() - date.fromisoformat(BALANCE["date"])).days
    days = int(left / BALANCE["per_day"])
    line = f"Сервер VDSina: баланс ≈ ${left:.0f}, хватит на ~{days} дн. (оценка)"
    (bad if days <= 21 else ok).append(line + (" — пора пополнить" if days <= 21 else ""))

    m = re.search(r"paid-till:\s*(\d{4}-\d{2}-\d{2})", run(["whois", DOMAIN]))
    if m:
        d = days_to(date.fromisoformat(m.group(1)))
        (bad if d <= 45 else ok).append(f"Домен {DOMAIN}: оплачен до {m.group(1)} ({d} дн.)" + (" — продлить на reg.ru" if d <= 45 else ""))
    else:
        bad.append(f"Домен {DOMAIN}: не удалось узнать срок оплаты")

    out = run(["openssl", "x509", "-enddate", "-noout", "-in", f"/etc/letsencrypt/live/{DOMAIN}/fullchain.pem"])
    m = re.search(r"notAfter=(.+)", out)
    if m:
        end = datetime.strptime(m.group(1).strip(), "%b %d %H:%M:%S %Y %Z").date()
        d = days_to(end)
        (bad if d <= 14 else ok).append(f"Сертификат https: до {end} ({d} дн.)" + (" — не продлился сам" if d <= 14 else ""))
    else:
        bad.append("Сертификат https: не найден")

    d = days_to(date.fromisoformat(CLAUDE_TOKEN_UNTIL))
    (bad if d <= WARN_DAYS else ok).append(f"Ключ Claude на сервере: до {CLAUDE_TOKEN_UNTIL} ({d} дн.)" + (" — выпустить новый скриптом на Mac" if d <= WARN_DAYS else ""))

    # службы
    down = [s for s in SERVICES if run(["systemctl", "is-active", s]).strip() != "active"]
    if down:
        bad.append("Не работают службы: " + ", ".join(down))
    else:
        ok.append("Службы работают: сайт, VS Code, вход, бот, синхронизация, сводка")

    # личный пульт на сайте (__DOMAIN__/me): свежая ли копия и закрыта ли паролем
    try:
        import filecmp, glob as _g
        src_dir = os.path.join(WORKDIR, "__ПАПКА_АГЕНТА__/20_Личный пульт")
        vers = [(int(m.group(1)), f) for f in _g.glob(os.path.join(src_dir, "Clodex.__ИМЯ__ v*.html"))
                if (m := re.search(r"Clodex\.__ИМЯ__ v(\d+)\.html$", f))]
        dst = "/var/www/me/index.html"
        if not vers or not os.path.exists(dst):
            bad.append("Пульт на сайте: нет файла — запусти me-publish.service")
        else:
            n, src = max(vers)
            age = (datetime.now().timestamp() - os.path.getmtime(src)) / 60
            if not filecmp.cmp(src, dst, shallow=False) and age > 5:
                bad.append(f"Пульт на сайте отстаёт: в папке v{n} от {datetime.fromtimestamp(os.path.getmtime(src)):%d.%m %H:%M}, на сайте старая копия")
            else:
                ok.append(f"Пульт на сайте свежий (v{n})")
        code = run(["curl", "-s", "-o", "/dev/null", "-w", "%{http_code}", f"https://{DOMAIN}/me/"], timeout=20).strip()
        if code == "200":
            bad.append("Пульт на сайте открывается без пароля — проверить nginx")
        elif code != "302":
            bad.append(f"Пульт на сайте не отвечает (код {code})")
    except Exception as e:
        bad.append(f"Пульт на сайте: не удалось проверить ({e!r})"[:200])

    # диск и память
    du = shutil.disk_usage("/")
    pct = du.used * 100 // du.total
    (bad if pct >= 85 else ok).append(f"Диск сервера занят на {pct}% (свободно {du.free // 2**30} ГБ)")

    # синхронизация
    conf = run(["find", WORKDIR, "/root/.claude/projects", "-name", "*sync-conflict*", "-not", "-path", "*/.stversions/*"]).strip().splitlines()
    if conf:
        bad.append(f"Конфликты синхронизации: {len(conf)} файл(ов), например {os.path.basename(conf[0])}")
    else:
        ok.append("Конфликтов синхронизации нет")

    try:
        cfg = open("/root/.local/state/syncthing/config.xml").read()
        key = re.search(r"<apikey>([^<]+)", cfg).group(1)
        req = urllib.request.Request("http://127.0.0.1:8384/rest/stats/device", headers={"X-API-Key": key})
        stats = json.load(urllib.request.urlopen(req, timeout=10))
        mac = re.search(r'<device id="([^"]+)" name="__MAC_DEVICE_NAME__"', cfg).group(1)
        seen = datetime.fromisoformat(stats[mac]["lastSeen"].replace("Z", "+00:00"))
        ago = (datetime.now(timezone.utc) - seen).days
        (bad if ago >= 3 else ok).append(f"Mac последний раз синхронизировался {seen.astimezone():%d.%m %H:%M} МСК" + (" — Mac давно не на связи" if ago >= 3 else ""))
    except Exception as e:
        bad.append(f"Синхронизация: не удалось проверить ({e})")

    return ok, bad


def pending_tasks():
    """Открытые задачи системы и задачи, которые делает агент, из единой базы. Возвращает [(срок, суть)]."""
    try:
        import importlib.util
        spec = importlib.util.spec_from_file_location("taskreg", TASKS_CHECK)
        mod = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(mod)
        all_t = mod.load_tasks()
    except Exception:
        return None
    return [(t["due"] or "", t["title"]) for t in all_t
            if t["status"] not in "x-~" and (t["id"].startswith("С-") or "агент" in t["who"].lower())]


def tasks_lines(full):
    tasks = pending_tasks()
    if tasks is None:
        return ["⚠️ Не удалось прочитать базу задач (01_Главный контекст/05_Задачи)"]
    if not tasks:
        return []
    dates = sorted(d for d, _ in tasks if d)
    oldest = f", самая старая с {dates[0][8:10]}.{dates[0][5:7]}" if dates else ""
    lines = [f"📋 Задач системы и агента: {len(tasks)}{oldest.replace('самая старая с', 'ближайший срок')}"]
    if full:
        lines += [f"  • {w[:100]}" for _, w in tasks[:10]]
        if len(tasks) > 10:
            lines.append(f"  … и ещё {len(tasks) - 10}")
    return lines


def send(text):
    data = urllib.parse.urlencode({"chat_id": CHAT, "text": text}).encode()
    urllib.request.urlopen(f"https://api.telegram.org/bot{TOKEN}/sendMessage", data, timeout=30)


if __name__ == "__main__":
    mode = sys.argv[1] if len(sys.argv) > 1 else "daily"
    ok, bad = check()
    # патруль безопасности (patrol.py): в daily — только новое, в weekly — всё открытое + сводка
    try:
        import patrol
        pok, pbad, pnotes, pcommit = patrol.check(mode if mode in ("weekly", "print") else "daily")
    except Exception as e:
        pok, pbad, pnotes, pcommit = [], [f"Патруль безопасности: не удалось проверить ({e!r})"[:200]], [], None
    bad += pbad
    ok += pok
    if mode == "weekly":
        head = "🛡 Сторож: недельная сводка" + (" — есть проблемы" if bad else " — всё в порядке")
        tl = tasks_lines(True)
        send("\n".join([head, ""] + [f"⚠️ {x}" for x in bad] + [f"✅ {x}" for x in ok] + pnotes + ([""] + tl if tl else [])))
    elif mode == "print":
        print("\n".join([f"⚠️ {x}" for x in bad] + [f"✅ {x}" for x in ok] + pnotes + [""] + tasks_lines(True)))
    else:
        tl = tasks_lines(False)
        parts = []
        if bad or pnotes:
            parts += ["🛡 Сторож: нужно внимание", ""] + [f"⚠️ {x}" for x in bad] + pnotes
        if tl:
            parts += ([""] if parts else []) + tl
        if parts:
            send("\n".join(parts))
    if pcommit and mode in ("daily", "weekly"):
        pcommit()
