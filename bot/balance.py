#!/usr/bin/env python3
"""Баланс трёх опор: на что за 7 дней ушло время владельца с агентом — работа, личное, система.
Считает только разговоры, начатые человеком: Claude Code и Codex на Mac и сервере, чаты Telegram-бота.
Автоматические запуски (ночные разборы, сводка, сторож, почта) не считаются.
Время сообщения = пауза до следующего сообщения в том же разговоре, не больше 15 минут.
Опора сообщения — по файлам, которые агент трогал в ответ, иначе по словам сообщения
(признаки проектов из реестра + базовый словарь), иначе как у предыдущего сообщения.
Цели — строка «Цели: работа N · личное N · система N» в файле правил (RULES).
Пишет: OUT_JSON (для пульта и сводки) и OUT_MD (агент читает в начале разговора).
Запуск: balance.py — пересчитать и записать; balance.py print — только показать."""
import glob, json, os, re, sys
from collections import defaultdict
from datetime import datetime, timedelta, timezone

WORKDIR = os.environ.get("AGENT_WORKDIR") or "__WORKDIR__"
HOME = os.path.expanduser("~")
RULES = "04_Системные файлы/10_Баланс опор — правила.md"
OUT_JSON = "04_Системные файлы/10_Баланс опор.json"
OUT_MD = "01_Главный контекст/04_Баланс опор.md"
REGISTRY = "03_Проекты/00_Реестр проектов.md"
DAYS = 7
CAP = 15 * 60          # пауза дольше 15 минут — человек отошёл
GAP = 10               # перекос: доля выше цели больше чем на 10 п.п.
MIN_MIN = 60           # меньше часа за неделю — данных мало, подсказок нет
SPH = ["Работа", "Личное", "Система"]
BOT_STAMP = "[Сейчас по Москве:"   # так бот помечает сообщения человека из Telegram

# Пути: (кусок пути, опора). Чтение файлов в начале каждого разговора (главный контекст, реестр, CLAUDE.md) не в счёт.
PATHS = [   # общие; папки проектов владельца — раздел «Папки» в RULES: «- Работа: путь, путь»
    ("tg-bot", "Система"), ("clodex-bot", "Система"), ("/.claude/", "Система"), ("/.codex/", "Система"), ("ssh ", "Система"),
]


def paths():
    out = list(PATHS)
    sec = rd(RULES).split("## Папки", 1)
    if len(sec) == 2:
        for m in re.finditer(r"^- (Работа|Личное|Система):(.+)$", sec[1].split("\n## ", 1)[0], re.M):
            out += [(w.strip(), m.group(1)) for w in m.group(2).split(",") if w.strip()]
    return out
IGNORE = re.compile(r"01_Главный контекст/0[1-4]_|00_Реестр проектов|CLAUDE\.md|AGENTS\.md|\.машина\.md|05_Бэклог|04_Архив")
WORDS = {   # общий словарь; личный (имена, проекты) — раздел «Словарь» в RULES. Через запятую, совпадение с начала слова
    "Работа": "рабоч, работодател, коллег, вендор, пилот, синк, совещани, презентац",
    "Система": "бот, сервер, пульт, дашборд, сторож, сводк, автоматиз, комплект, лендинг, сайт, syncthing, расширени, "
               "скрипт, деплой, служб, таймер, github, репозитор, codex, claude.md, агент",
    "Личное": "дач, семь, жена, жён, сын, дочь, финанс, выписк, подписк, платёж, платеж, фото, документ, паспорт, "
              "книг, колёс, колес, интервью, переписк, здоров, врач, отпуск, поездк, квартир, ремонт",
}
# Финансовый пульт — личное, хотя лежит в папке системы 
FIN = re.compile(r"финанс|выписк|подписк|платеж|трат|кэшбэк|бюджет", re.I)


def rd(rel):
    try:
        return open(os.path.join(WORKDIR, rel), encoding="utf-8").read()
    except OSError:
        return ""


def targets():
    m = re.search(r"Цели:\s*работа\s*(\d+)\D+личное\s*(\d+)\D+система\s*(\d+)", rd(RULES), re.I)
    return dict(zip(SPH, map(int, m.groups()))) if m else {"Работа": 50, "Личное": 25, "Система": 25}


def project_words():
    """Признаки проектов из реестра: слова → опора (сфера проекта)."""
    out = defaultdict(set)
    weak = {"банк", "договор", "договоры", "поставки", "реестр", "оборудования", "wechat", "tech", "work", "экран."}
    sph = {"Рабочий": "Работа", "Личный": "Личное", "Система": "Система"}
    for row in rd(REGISTRY).splitlines():
        c = [x.strip() for x in row.strip("|").split("|")]
        if len(c) < 6 or c[2] not in sph:
            continue
        s = sph[c[2]]
        m = re.search(r"Признаки:(.*?)(?:\.\s|$)", c[5])
        for w in re.split(r"[,;/()«»]", m.group(1) if m else ""):
            w = w.strip().lower()
            w = w.strip(". ")
            if len(w) >= 4 and w not in weak:
                out[s].add(w)
    for s, ws in WORDS.items():
        out[s].update(w.strip() for w in ws.split(","))
    sec = rd(RULES).split("## Словарь", 1)
    sec = sec[1].split("\n## ", 1)[0] if len(sec) == 2 else ""
    for s in SPH:   # личный словарь из правил: строка «- Работа: слово, слово»
        for m in re.finditer(rf"^- {s}:(.+)$", sec, re.M):
            out[s].update(w.strip().lower() for w in m.group(1).split(",") if w.strip())
    return {s: re.compile("|".join(r"(?<!\w)" + re.escape(w) for w in sorted(ws, key=len, reverse=True)), re.I) for s, ws in out.items()}


KW = None
PT = PATHS


def by_words(text):
    sc = {s: len(KW[s].findall(text)) if s in KW else 0 for s in SPH}
    return max(sc, key=sc.get) if max(sc.values()) else None


def by_paths(blob, text):
    sc = dict.fromkeys(SPH, 0)
    for line in blob:
        if IGNORE.search(line):
            continue
        for p, s in PT:
            if p in line:
                if "20_Личный пульт" in line and FIN.search(text):
                    s = "Личное"
                sc[s] += 1
                break
    return max(sc, key=sc.get) if max(sc.values()) else None


def clean(t):
    t = re.sub(r"<(system-reminder|ide_selection|ide_opened_file|environment_context)>.*?</\1>", " ", t, flags=re.S)
    t = re.sub(r"</?pasted_content[^>]*>", " ", t)
    return t.strip()


def ts(s):
    try:
        return datetime.fromisoformat(s.replace("Z", "+00:00"))
    except Exception:
        return None


def claude_sessions():
    for f in glob.glob(os.path.join(HOME, ".claude/projects/*/*.jsonl")):
        msgs, cur, auto, first, last_t = [], None, False, True, None
        try:
            lines = open(f, encoding="utf-8", errors="ignore")
            for line in lines:
                try:
                    o = json.loads(line)
                except ValueError:
                    continue
                if o.get("isSidechain"):
                    continue
                t = ts(o.get("timestamp", ""))
                last_t = t or last_t
                if o.get("cwd") and not o["cwd"].startswith(WORKDIR):
                    auto = True   # разговор не в папке агента
                    break
                m = o.get("message") or {}
                if o.get("type") == "user":
                    c = m.get("content")
                    if isinstance(c, list):
                        if any(isinstance(x, dict) and x.get("type") == "tool_result" for x in c):
                            continue
                        c = " ".join(x.get("text", "") for x in c if isinstance(x, dict) and x.get("type") == "text")
                    if not isinstance(c, str) or not t:
                        continue
                    if first:
                        first = False
                        if o.get("entrypoint") == "sdk-cli" and not c.startswith(BOT_STAMP):
                            auto = True   # фоновый запуск без человека
                            break
                    c = clean(c)
                    if not c or c.startswith(("<command-", "<local-command", "<task-notification", "[Request interrupted", "Caveat:")):
                        continue
                    cur = {"t": t, "text": c, "tools": []}
                    msgs.append(cur)
                elif o.get("type") == "assistant" and cur:
                    for x in m.get("content") or []:
                        if isinstance(x, dict) and x.get("type") == "tool_use":
                            inp = x.get("input") or {}
                            cur["tools"] += [str(v) for k, v in inp.items() if k in ("file_path", "path", "command", "pattern", "notebook_path")]
        except OSError:
            continue
        if not auto and msgs:
            yield msgs, last_t


def codex_sessions():
    for f in glob.glob(os.path.join(HOME, ".codex/sessions/*/*/*/*.jsonl")):
        msgs, cur, ok, last_t, exe = [], None, True, None, False
        try:
            for line in open(f, encoding="utf-8", errors="ignore"):
                try:
                    o = json.loads(line)
                except ValueError:
                    continue
                p = o.get("payload") or {}
                t = ts(o.get("timestamp", ""))
                last_t = t or last_t
                if o.get("type") == "session_meta":
                    if p.get("parent_thread_id") or not str(p.get("cwd", "")).startswith(WORKDIR):
                        ok = False
                        break
                    exe = p.get("originator") == "codex_exec"
                if o.get("type") != "response_item":
                    continue
                if p.get("type") == "message" and p.get("role") == "user":
                    c = " ".join(x.get("text", "") for x in p.get("content") or [] if isinstance(x, dict))
                    if exe:   # бот: текст человека после метки, иначе фоновый запуск
                        if "[Сообщение пользователя]" not in c:
                            continue
                        c = c.split("[Сообщение пользователя]", 1)[1]
                    c = clean(c)
                    if not c or c.startswith(("# AGENTS.md", "<")):
                        continue
                    cur = {"t": t, "text": c, "tools": []}
                    msgs.append(cur)
                elif p.get("type") in ("function_call", "custom_tool_call") and cur:
                    cur["tools"].append(str(p.get("arguments") or p.get("input") or "")[:2000])
        except OSError:
            continue
        if ok and msgs:
            yield msgs, last_t


def compute(now=None):
    global KW, PT
    KW, PT = project_words(), paths()
    now = now or datetime.now(timezone.utc)
    since = now - timedelta(days=DAYS)
    tot = dict.fromkeys(SPH, 0.0)
    unk = 0.0
    days = defaultdict(lambda: dict.fromkeys(SPH, 0.0))
    for msgs, last_t in list(claude_sessions()) + list(codex_sessions()):
        prev = None
        for i, m in enumerate(msgs):
            nxt = msgs[i + 1]["t"] if i + 1 < len(msgs) else (last_t or m["t"]) + timedelta(minutes=1)
            sec = max(60, min(CAP, (nxt - m["t"]).total_seconds()))
            s = by_paths(m["tools"], m["text"]) or by_words(m["text"]) or prev
            prev = s or prev
            if m["t"] < since:
                continue
            if not s:
                unk += sec
                continue
            tot[s] += sec
            days[m["t"].astimezone().date().isoformat()][s] += sec
    allsec = sum(tot.values())
    tg = targets()
    share = {s: round(tot[s] / allsec * 100) if allsec else 0 for s in SPH}
    over = max(SPH, key=lambda s: share[s] - tg[s])
    under = min(SPH, key=lambda s: share[s] - tg[s])
    skew = allsec / 60 >= MIN_MIN and share[over] - tg[over] > GAP
    if allsec / 60 < MIN_MIN:
        verdict = "ℹ️ Мало данных за неделю — подсказок нет."
    elif skew:
        verdict = f"⚠️ Перекос: {over.lower()} {share[over]}% при цели {tg[over]}. Отстаёт {under.lower()}: {share[under]}% при цели {tg[under]}."
    else:
        verdict = "✅ Баланс в пределах цели (±10 п.п.)."
    return {
        "built": datetime.now().strftime("%Y-%m-%d %H:%M"),
        "period": [since.astimezone().date().isoformat(), now.astimezone().date().isoformat()],
        "targets": tg, "share": share, "hours": {s: round(tot[s] / 3600, 1) for s in SPH},
        "unknown_hours": round(unk / 3600, 1), "skew": skew, "over": over if skew else None, "under": under if skew else None,
        "verdict": verdict,
        "days": {d: {s: round(v[s] / 60) for s in SPH} for d, v in sorted(days.items())},
    }


def line(b):
    """Строка для сводки и бота."""
    p = " · ".join(f"{s.lower()} {b['share'][s]}% ({b['targets'][s]})" for s in SPH)
    return f"⚖️ Неделя с агентом: {p}{' ⚠️' if b['skew'] else ''}"


def md(b):
    sh, tg, h = b["share"], b["targets"], b["hours"]
    rows = "\n".join(f"| {s} | {sh[s]}% | {tg[s]}% | {sh[s] - tg[s]:+d} | {h[s]} |" for s in SPH)
    return f"""# Баланс опор — замер

Файл пишет скрипт `balance.py` на сервере раз в час; руками не править. Правила и цели — `{RULES}`.
Обновлено: {b['built']} · период {b['period'][0]} — {b['period'][1]} · только разговоры с агентом, начатые владельцем.

{b['verdict']}

| Опора | Доля | Цель | Разница, п.п. | Часы ≈ |
|---|---|---|---|---|
{rows}

Не определено: {b['unknown_hours']} ч (в доли не входит).
"""


def write(b):
    for rel, text in ((OUT_JSON, json.dumps(b, ensure_ascii=False, indent=1)), (OUT_MD, md(b))):
        path = os.path.join(WORKDIR, rel)
        old = open(path, encoding="utf-8").read() if os.path.exists(path) else None
        if rel == OUT_JSON and old:
            try:   # без изменений долей — не трогаем файлы: пульт не пересобирается зря
                o = json.loads(old)
                if all(o.get(k) == b[k] for k in ("share", "targets", "verdict", "days")):
                    return False
            except ValueError:
                pass
        tmp = path + ".tmp"
        open(tmp, "w", encoding="utf-8").write(text)
        os.replace(tmp, path)
    return True


if __name__ == "__main__":
    b = compute()
    if len(sys.argv) > 1 and sys.argv[1] == "print":
        print(md(b))
        print(line(b))
        sys.exit()
    changed = write(b)
    print(line(b), "· записано" if changed else "· без изменений")
