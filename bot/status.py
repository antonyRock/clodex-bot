#!/usr/bin/env python3
"""Статус системы для бота, без ИИ (2026-11-08).
Работает и тогда, когда у Claude кончился лимит.

  status.py             — JSON {"text": ..., "buttons": [[{text, callback_data}]]} для бота
  status.py print       — тот же текст в консоль
  status.py run_chatgpt — запустить разбор ChatGPT (только по кнопке пользователя)
  status.py run_vk      — запустить разбор ВК (только по кнопке пользователя)
  status.py conflicts   — убрать конфликтные копии синхронизации в архив (не удаляет)

Сам ничего не запускает: разборы стартуют только по нажатию кнопки."""
import json, os, re, shutil, subprocess, sys
from datetime import datetime

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import guard  # проверки сторожа: службы, сроки, пульт, диск, синхронизация

WORKDIR = guard.WORKDIR
INTERVIEW = os.path.join(WORKDIR, "02_Глубокий контекст/12_Интервью")
CHATGPT_DIR = os.path.join(INTERVIEW, "04_Полный разбор ChatGPT")
VK_DIR = os.path.join(INTERVIEW, "03_Полный разбор ВК")
VK_LOG = "/root/night/vk.log"
VK_SOURCE = "/root/Claude_кэш/vk/text"
CONFLICT_ARCHIVE = "/root/Claude_кэш/sync-conflicts"
WEEKDAYS = ["пн", "вт", "ср", "чт", "пт", "сб", "вс"]

TIMERS = [  # (название, таймер, служба)
    ("Утренняя сводка", "brief.timer", "brief.service"),
    ("Вопрос дня", "nudge.timer", "nudge.service"),
    ("Проверка почты", "mail-watch.timer", "mail-watch.service"),
]


def sh(cmd, timeout=30):
    try:
        return subprocess.run(cmd, capture_output=True, text=True, timeout=timeout).stdout
    except Exception as e:
        return f"ERR {e}"


def when(dt):
    """Сегодня — «14:05», завтра — «завтра 08:00», иначе — «пн 09.11 08:00»."""
    if not dt:
        return "—"
    d = (dt.date() - datetime.now().date()).days
    if d == 0:
        return f"{dt:%H:%M}"
    if d == 1:
        return f"завтра {dt:%H:%M}"
    if d == -1:
        return f"вчера {dt:%H:%M}"
    return f"{WEEKDAYS[dt.weekday()]} {dt:%d.%m %H:%M}"


def sd_time(s):
    """«Sun 2026-11-08 08:00:14 MSK» → datetime."""
    m = re.search(r"(\d{4}-\d{2}-\d{2} \d{2}:\d{2}:\d{2})", s or "")
    return datetime.strptime(m.group(1), "%Y-%m-%d %H:%M:%S") if m else None


def props(unit, *names):
    out = sh(["systemctl", "show", unit] + [f"-p{n}" for n in names])
    return dict(l.split("=", 1) for l in out.splitlines() if "=" in l)


def schedule_lines():
    lines = []
    for title, timer, service in TIMERS:
        t = props(timer, "ActiveState", "LastTriggerUSec", "NextElapseUSecRealtime")
        s = props(service, "Result", "ActiveState")
        last, nxt = sd_time(t.get("LastTriggerUSec")), sd_time(t.get("NextElapseUSecRealtime"))
        if t.get("ActiveState") != "active":
            lines.append(f"❌ {title} — расписание выключено")
        elif s.get("Result") not in ("success", None) and s.get("ActiveState") != "activating":
            lines.append(f"❌ {title} — последний запуск {when(last)} с ошибкой, следующий {when(nxt)}")
        else:
            lines.append(f"✅ {title} — была {when(last)}, следующая {when(nxt)}")
    return lines


def read_json(path):
    try:
        return json.load(open(path))
    except Exception:
        return {}


def short_reason(text):
    low = (text or "").lower()
    if "monthly spend limit" in low:
        return "месячный лимит расходов Claude"
    if "limit" in low or "quota" in low or "credits" in low or "лимит" in low:
        return "лимит"
    return (text or "причина не записана").strip()[:80]


def chatgpt_line():
    c = read_json(os.path.join(CHATGPT_DIR, "01_Контрольная точка.json"))
    if not c:
        return "❔ Разбор ChatGPT — контрольная точка не читается", None
    done, total = c.get("segments_done", 0), c.get("queue_total") or 1
    prog = f"{done} из {total} фрагментов ({done * 100 // total}%)"
    running = sh(["systemctl", "is-active", "chatgpt-review"]).strip() == "active"
    st = c.get("status")
    if running:
        return f"▶️ Разбор ChatGPT — идёт, {prog}", None
    if st == "complete":
        return "✅ Разбор ChatGPT — закончен", None
    since = when(datetime.fromisoformat(c["updated_msk"]).replace(tzinfo=None)) if c.get("updated_msk") else "—"
    why = "лимит Codex" if st == "quota_stopped" else short_reason(c.get("stop_reason") or st)
    return (f"❌ Разбор ChatGPT — стоит с {since} ({why}), сделано {prog}",
            {"text": "▶️ Запустить разбор ChatGPT", "callback_data": "st:run_chatgpt"})


def vk_line():
    c = read_json(os.path.join(VK_DIR, "01_Контрольная точка.json"))
    done, total = c.get("chats_done", 0), c.get("queue_total") or 1
    prog = f"{done} из {total} чатов"
    if subprocess.run(["tmux", "has-session", "-t", "vk"], capture_output=True).returncode == 0:
        return f"▶️ Разбор ВК — идёт, {prog}", None
    if str(c.get("status", "")).lower() in ("готово", "done", "complete"):
        return "✅ Разбор ВК — закончен", None
    last, reason = None, ""
    try:
        for line in open(VK_LOG, encoding="utf-8", errors="replace"):
            last = line
        since = when(datetime.strptime(last[:19], "%Y-%m-%d %H:%M:%S"))
        if " fail " in last or "err True" in last:
            reason = short_reason(last)
        elif "STOP" in last or "stop" in last:
            reason = "остановлен"
        else:
            reason = "процесс завершён"
    except Exception:
        since, reason = "—", "журнал не читается"
    return (f"❌ Разбор ВК — стоит с {since} ({reason}), сделано {prog}",
            {"text": "▶️ Запустить разбор ВК", "callback_data": "st:run_vk"})


def conflicts():
    out = sh(["find", WORKDIR, "/root/.claude/projects", "-name", "*sync-conflict*", "-not", "-path", "*/.stversions/*"]).strip()
    return [p for p in out.splitlines() if p and not p.startswith("ERR")]


def build():
    ok, bad = guard.check()
    # конфликты покажем отдельно, с кнопкой
    bad = [b for b in bad if not b.startswith("Конфликты синхронизации")]
    ok = [o for o in ok if not o.startswith("Конфликтов синхронизации")]
    server = [f"❌ {b}" for b in bad] + [f"✅ {o}" for o in ok]
    sched = schedule_lines()
    gpt, gpt_btn = chatgpt_line()
    vk, vk_btn = vk_line()
    conf = conflicts()
    buttons = [b for b in (gpt_btn, vk_btn) if b]
    extra = []
    if conf:
        names = ", ".join(os.path.basename(p) for p in conf[:2])
        extra.append(f"⚠️ Конфликтных копий синхронизации: {len(conf)} ({names})")
        buttons.append({"text": "🧹 Убрать копии в архив", "callback_data": "st:conflicts"})

    problems = any(l.startswith("❌") for l in server + sched + [gpt, vk]) or bool(conf)
    head = f"{'🟡' if problems else '🟢'} Статус системы · {datetime.now():%d.%m %H:%M}\nПроверено скриптом, без ИИ"
    parts = [head, "", "Сервер"] + server + ["", "По расписанию"] + sched + ["", "Фоновые задачи", gpt, vk]
    if extra:
        parts += ["", "Мелочи"] + extra
    if buttons:
        parts += ["", "👉 Нужно ваше решение — кнопки ниже. Сам ничего не запускаю."]
    return {"text": "\n".join(parts), "buttons": [[b] for b in buttons]}


def run_chatgpt():
    if sh(["systemctl", "is-active", "chatgpt-review"]).strip() == "active":
        return "Разбор ChatGPT уже идёт."
    if os.path.exists(os.path.join(CHATGPT_DIR, "СТОП")):
        return "В папке разбора ChatGPT лежит файл СТОП — не запускаю. Уберите его, если нужно продолжить."
    subprocess.run(["systemctl", "start", "--no-block", "chatgpt-review.service"], timeout=30)
    return "▶️ Разбор ChatGPT запущен с места остановки. При лимите остановится сам."


def run_vk():
    if subprocess.run(["tmux", "has-session", "-t", "vk"], capture_output=True).returncode == 0:
        return "Разбор ВК уже идёт."
    if os.path.exists(os.path.join(VK_DIR, "СТОП")):
        return "В папке разбора ВК лежит файл СТОП — не запускаю. Уберите его, если нужно продолжить."
    cmd = ("set -a; . /root/.config/claude-token; . /root/.config/tg-bot.env; set +a; "
           f"/usr/bin/python3 /root/night/run_task.py vk '{VK_DIR}' '{VK_SOURCE}' 24")
    r = subprocess.run(["tmux", "new-session", "-d", "-s", "vk", "bash", "-c", cmd], capture_output=True, text=True)
    if r.returncode:
        return f"Не смог запустить разбор ВК: {r.stderr.strip()[:200]}"
    return "▶️ Разбор ВК запущен с места остановки, до 24 часов. Статусы пришлёт сам."


def move_conflicts():
    conf = conflicts()
    if not conf:
        return "Конфликтных копий нет."
    dest = os.path.join(CONFLICT_ARCHIVE, f"{datetime.now():%Y-%m-%d_%H%M}")
    for p in conf:
        rel = os.path.relpath(p, "/")
        os.makedirs(os.path.dirname(os.path.join(dest, rel)), exist_ok=True)
        shutil.move(p, os.path.join(dest, rel))
    return f"🧹 Убрал {len(conf)} конфликтн. копий в архив на сервере ({dest}). Основные файлы не трогал."


if __name__ == "__main__":
    mode = sys.argv[1] if len(sys.argv) > 1 else "json"
    if mode == "print":
        print(build()["text"])
    elif mode == "run_chatgpt":
        print(run_chatgpt())
    elif mode == "run_vk":
        print(run_vk())
    elif mode == "conflicts":
        print(move_conflicts())
    else:
        print(json.dumps(build(), ensure_ascii=False))
