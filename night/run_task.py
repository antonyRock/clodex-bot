#!/usr/bin/env python3
"""Фоновая (ночная) задача агента на сервере.
Гоняет `claude -p` партиями: один запуск = одна партия по заданию в папке задачи.
Статусы — в Telegram: запущено, пауза (лимит), продолжаю, готово, остановлено, ошибка.
Агент: только чтение Claude_AI и источника, запись только в 02_Глубокий контекст, без Bash и интернета.

Запуск (в tmux, окружение из /root/.config/claude-token и tg-bot.env):
  run_task.py <имя> <папка задачи> <доп. чтение> [часов]
Остановить: файл СТОП в папке задачи, или `tmux kill-session -t <имя>`.
"""
import json, os, re, subprocess, sys, time, urllib.parse, urllib.request
from datetime import datetime, timedelta

CLAUDE = "/root/.local/bin/claude"
WORKDIR = "__WORKDIR__"
WRITE_DIR = f"{WORKDIR}/02_Глубокий контекст"
CHAT = os.environ.get("TG_CHAT", "__TG_ID__")
TOKEN = os.environ["TG_TOKEN"]
RUN_TIMEOUT = 45 * 60
PAUSE_DEFAULT = 30 * 60
PROGRESS_EVERY = 10
MAX_FAILS = 3

name, task_dir, extra_read = sys.argv[1], sys.argv[2], sys.argv[3]
hours = float(sys.argv[4]) if len(sys.argv) > 4 else 24
deadline = datetime.now() + timedelta(hours=hours)
LOG = f"/root/night/{name}.log"
TITLE = f"[Фоновая задача: {name}]"
rel = os.path.relpath(task_dir, WORKDIR)
PROMPT = (f"Ты фоновый агент без человека. Выполни ОДНУ партию по заданию `{rel}/00_Задание.md` "
          f"(прочитай его целиком). Вопросов не задавай, решай сам по правилам. "
          f"Последняя строка ответа — STATUS: CONTINUE, STATUS: DONE или STATUS: STOPPED.")


def log(*a):
    with open(LOG, "a") as f:
        f.write(datetime.now().strftime("%Y-%m-%d %H:%M:%S ") + " ".join(str(x) for x in a) + "\n")


def send(text):
    try:
        data = urllib.parse.urlencode({"chat_id": CHAT, "text": f"{TITLE}\n{text}"}).encode()
        urllib.request.urlopen(f"https://api.telegram.org/bot{TOKEN}/sendMessage", data, timeout=30)
    except Exception as e:
        log("telegram fail", e)


def checkpoint():
    try:
        c = json.load(open(f"{task_dir}/01_Контрольная точка.json"))
        return (f"чат {c.get('next_no')} из {c.get('queue_total')}, партий {c.get('batches_done')}, "
                f"чатов {c.get('chats_done')}, прочитано {round((c.get('bytes_done') or 0)/1e6, 1)} МБ")
    except Exception as e:
        return f"контрольная точка не читается: {e}"


def run_once():
    # время партии даёт сервер: у агента нет команды date (без Bash)
    prompt = PROMPT + f" Время запуска партии (МСК): {datetime.now():%Y-%m-%d %H:%M}."
    cmd = [CLAUDE, "-p", prompt, "--output-format", "json", "--permission-mode", "default",
           "--allowedTools", f"Read(/{WORKDIR}/**)", f"Edit(/{WRITE_DIR}/**)",
           f"Read(/{extra_read}/**)", "Glob", "Grep", "TodoWrite",
           "--disallowedTools", "Bash", "WebFetch", "WebSearch"]
    try:
        p = subprocess.run(cmd, cwd=WORKDIR, capture_output=True, text=True, timeout=RUN_TIMEOUT)
    except subprocess.TimeoutExpired:
        return "error", "запуск дольше 45 минут"
    raw = p.stdout + "\n" + p.stderr
    try:
        out = json.loads(p.stdout)
        text = str(out.get("result", ""))
        is_err = out.get("is_error", False)
    except Exception:
        text, is_err = raw, True
    log("run rc", p.returncode, "err", is_err, "cost", locals().get("out", {}).get("total_cost_usd"), "tail:", text[-300:].replace("\n", " "))
    m = re.findall(r"STATUS:\s*(CONTINUE|DONE|STOPPED)", text)
    if m and not is_err:
        return m[-1].lower(), text
    low = (text + raw).lower()
    if re.search(r"usage limit|limit reached|hit your limit|limit will reset|resets? (at|in)|out of (credits|extra usage)|credit balance", low):
        return "limit", text
    if m:
        return m[-1].lower(), text
    return "error", (text or raw)[-500:]


def pause_seconds(text):
    m = re.search(r"\|(\d{10})", text)
    if m:
        s = int(m.group(1)) - time.time() + 120
        if 60 < s < 6 * 3600:
            return int(s)
    return PAUSE_DEFAULT


def main():
    send(f"Запущено. Работаю до {deadline:%d.%m %H:%M}.\nСейчас: {checkpoint()}\n"
         f"Остановить: попроси бота положить файл СТОП в папку {rel}")
    log("start", rel)
    fails = batches = 0
    paused = False
    while datetime.now() < deadline:
        if os.path.exists(f"{task_dir}/СТОП"):
            send(f"Остановлено по файлу СТОП. {checkpoint()}")
            return
        status, text = run_once()
        if status == "limit":
            wait = pause_seconds(text)
            if not paused:
                send(f"Пауза: кончился лимит Claude. Проверю снова через {wait // 60} мин.\n{checkpoint()}")
            paused = True
            time.sleep(wait)
            continue
        if paused:
            send(f"Лимит восстановился, продолжаю. {checkpoint()}")
            paused = False
        if status == "error":
            fails += 1
            log("fail", fails, text)
            if fails >= MAX_FAILS:
                send(f"Остановлено: {MAX_FAILS} ошибки подряд.\nПоследняя: {text[-400:]}\n{checkpoint()}\nЖурнал: {LOG}")
                return
            time.sleep(120)
            continue
        fails = 0
        if status == "done":
            send(f"Готово, очередь кончилась. {checkpoint()}\nИтоги: {rel}/03_Ход разбора.md")
            return
        if status == "stopped":
            send(f"Остановлено агентом (файл СТОП). {checkpoint()}")
            return
        batches += 1
        if batches % PROGRESS_EVERY == 0:
            send(f"Идёт работа: {batches} партий за этот запуск. {checkpoint()}")
    send(f"Время вышло ({hours:g} ч), остановлено. {checkpoint()}\nПродолжить — скажи агенту «запусти разбор ВК дальше».")


if __name__ == "__main__":
    try:
        main()
    except Exception as e:
        log("crash", repr(e))
        send(f"Упало с ошибкой: {e!r}\n{checkpoint()}")
        raise
