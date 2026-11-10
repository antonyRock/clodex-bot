#!/usr/bin/env python3
"""Фоновая (ночная) задача агента на сервере.
Гоняет `claude -p` партиями: один запуск = одна партия по заданию в папке задачи.
Статусы — в Telegram: запущено, пауза (лимит), продолжаю, готово, остановлено, ошибка.
Агент: только чтение Claude_AI и источника, запись только в 02_Глубокий контекст, без Bash и интернета.

Запуск (в tmux, окружение из /root/.config/claude-token и tg-bot.env):
  run_task.py <имя> <папка задачи> <доп. чтение> [часов]
Остановить: файл СТОП в папке задачи, или `tmux kill-session -t <имя>`.

Необязательный файл 00_Параметры.json в папке задачи (без него — прежнее поведение: запись только в 02_Глубокий контекст, модель по умолчанию):
  {"model": "claude-opus-5-5", "effort": "medium",
   "write": ["02_Глубокий контекст", "03_Проекты/<проект>", "01_Главный контекст/05_Задачи/02_Рабочие.md"],   # куда агенту можно писать (путь от Claude_AI; папка или файл)
   "stop_five_hour_pct": 50,       # при расходе пятичасового окна подписки >= N % — пауза до сброса окна (процент берётся из rate_limit_event, как у /limits)
   "after_batch": "pults"}         # если партия изменила задачи/реестр/финансы — проверка задач и пересборка пультов (pults.py бота)
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

sys.path.insert(0, "/root/tg-bot")   # pults.py бота: проверка задач и пересборка пультов после партии
name, task_dir, extra_read = sys.argv[1], sys.argv[2], sys.argv[3]
try:
    PARAMS = json.load(open(os.path.join(task_dir, "00_Параметры.json"), encoding="utf-8"))
except (OSError, ValueError):
    PARAMS = {}
STATE_BOT = "/root/tg-bot/state.json"
LAST_UTIL = {}   # последние известные окна лимита: {"five_hour": {"utilization": 0..1, "resetsAt": ts}}
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


def write_rules():
    """Правила записи для агента: из параметров (папки и файлы от Claude_AI) или прежнее — только 02_Глубокий контекст."""
    paths = PARAMS.get("write") or ["02_Глубокий контекст"]
    out = []
    for rel in paths:
        full = f"{WORKDIR}/{rel}"
        out.append(f"Edit(/{full}" + ("" if os.path.splitext(rel)[1] else "/**") + ")")   # у файла есть расширение, у папки нет
    return out


def run_once():
    # время партии даёт сервер: у агента нет команды date (без Bash)
    prompt = PROMPT + f" Время запуска партии (МСК): {datetime.now():%Y-%m-%d %H:%M}."
    cmd = [CLAUDE, "-p", prompt, "--output-format", "stream-json", "--verbose", "--permission-mode", "default",
           "--allowedTools", f"Read(/{WORKDIR}/**)", *write_rules(),
           f"Read(/{extra_read}/**)", "Glob", "Grep", "TodoWrite",
           "--disallowedTools", "Bash", "WebFetch", "WebSearch"]
    if PARAMS.get("model"):
        cmd += ["--model", PARAMS["model"]]
    if PARAMS.get("effort"):
        cmd += ["--effort", PARAMS["effort"]]
    try:
        p = subprocess.run(cmd, cwd=WORKDIR, capture_output=True, text=True, timeout=RUN_TIMEOUT)
    except subprocess.TimeoutExpired:
        return "error", "запуск дольше 45 минут"
    raw = p.stdout + "\n" + p.stderr
    out = None
    for line in p.stdout.splitlines():   # stream-json: итог — type=result, лимиты — rate_limit_event
        try:
            ev = json.loads(line)
        except Exception:
            continue
        if ev.get("type") == "result":
            out = ev
        elif ev.get("type") == "rate_limit_event":
            note_limits(ev.get("rate_limit_info"))
    if out is not None:
        text, is_err = str(out.get("result", "")), out.get("is_error", False)
    else:
        text, is_err = raw, True
    log("run rc", p.returncode, "err", is_err, "cost", (out or {}).get("total_cost_usd"), "five_hour", five_hour(), "tail:", text[-300:].replace("\n", " "))
    m = re.findall(r"STATUS:\s*(CONTINUE|DONE|STOPPED)", text)
    if m and not is_err:
        return m[-1].lower(), text
    low = (text + raw).lower()
    if re.search(r"usage limit|limit reached|hit your limit|limit will reset|resets? (at|in)|out of (credits|extra usage)|credit balance", low):
        return "limit", text
    if m:
        return m[-1].lower(), text
    return "error", (text or raw)[-500:]


def note_limits(info):
    """Запомнить окна лимита из rate_limit_event (то же, что показывает /limits у бота)."""
    if not info:
        return
    wins = info.get("unifiedWindows") or {info.get("rateLimitType", "?"): info}
    for k, w in wins.items():
        if w.get("utilization") is not None:
            LAST_UTIL[k] = {"utilization": w["utilization"], "resetsAt": w.get("resetsAt")}


def five_hour():
    return (LAST_UTIL.get("five_hour") or {}).get("utilization")


def limit_gate():
    """Секунд ждать до сброса пятичасового окна, если расход >= порога (stop_five_hour_pct, %); иначе 0.
    Перед первой партией процент берётся из состояния бота (последний ответ Claude), дальше — из своих партий."""
    pct = PARAMS.get("stop_five_hour_pct")
    if not pct:
        return 0
    w = LAST_UTIL.get("five_hour")
    if w is None:
        try:
            w = ((json.load(open(STATE_BOT, encoding="utf-8")).get("limits") or {}).get("unifiedWindows") or {}).get("five_hour")
        except (OSError, ValueError):
            w = None
    if not w or w.get("utilization") is None or w["utilization"] * 100 < pct:
        return 0
    reset = w.get("resetsAt") or 0
    wait = int(reset - time.time()) + 120
    return wait if 60 < wait < 6 * 3600 else (0 if wait <= 60 else PAUSE_DEFAULT)   # окно уже сброшено — данные устарели, идём дальше


def post_batch(before):
    """После партии: если изменились задачи/реестр/финансы — проверка задач и пересборка пультов."""
    if PARAMS.get("after_batch") != "pults":
        return
    try:
        import pults
        if pults.snapshot() != before:
            ok, report = pults.rebuild()
            log("pults", ok, report)
            if not ok:
                send("\n".join(report))
    except Exception as e:
        log("pults error", repr(e))
        send(f"Пульты не пересобрались: {e!r}")


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
        wait = limit_gate()
        if wait:
            u = five_hour()
            send(f"Пауза: израсходовано {PARAMS.get('stop_five_hour_pct')}%+ пятичасового лимита" + (f" ({round(u * 100)}%)" if u is not None else "")
                 + f". Продолжу через {wait // 60} мин, когда окно сбросится.\n{checkpoint()}")
            LAST_UTIL.pop("five_hour", None)
            time.sleep(wait)
            continue
        before = None
        if PARAMS.get("after_batch") == "pults":
            try:
                import pults
                before = pults.snapshot()
            except Exception as e:
                log("pults snapshot error", repr(e))
        status, text = run_once()
        if before is not None:
            post_batch(before)
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
