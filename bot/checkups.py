#!/usr/bin/env python3
"""Регулярные осмотры и дела: напомнить, когда подходит срок, и повторять, пока не подтвердят.
Источник — таблица «### Профилактика» в медкарте (процедура | как часто, мес. | последний раз | источник).
Срок = последний раз + период. За LEAD_DAYS до срока и после него бот после утренней сводки присылает
короткое сообщение с кнопками — по одному пункту не чаще раза в REPEAT_DAYS. «Прошёл» ставит сегодняшнюю
дату в таблицу (дальше новый круг), «Записался» и «Через месяц» — тишина, «Не напоминать» — пометка
в столбце «Источник». Дата «?» — не напоминаем (её спрашивает агент). Без ИИ.
Запуск: checkups.py print — показать, что пора, ничего не отправлять."""
import hashlib, json, os, re, sys, urllib.parse, urllib.request
from datetime import date, timedelta

WORKDIR = "__WORKDIR__"
MED = os.path.join(WORKDIR, "02_Глубокий контекст", "09_Быт_и_здоровье", "03_Здоровье и медицина.md")
STATE = "/root/tg-bot/checkups_state.json"   # {ключ: {"shown": дата, "quiet": до какой даты молчать}}
LEAD_DAYS, REPEAT_DAYS, MAX_PER_DAY = 14, 7, 2
OFF = "не напоминать"
QUIET = {"bk": 21, "mo": 30}                 # «Записался» — 3 недели, «Через месяц» — 30 дней
ICONS = [("стоматолог", "🦷"), ("анализ", "🩸"), ("чекап", "🩸"), ("окулист", "👁"), ("дермат", "🔍"),
         ("прививк", "💉"), ("флюоро", "🫁")]


def key(name):
    return hashlib.md5(name.encode("utf-8")).hexdigest()[:8]


def add_months(d, n):
    y, m = divmod(d.month - 1 + n, 12)
    return date(d.year + y, m + 1, min(d.day, 28))


def _rows(text):
    """Строки таблицы «Профилактика»: (номер строки в файле, ячейки)."""
    lines = text.split("\n")
    try:
        start = next(i for i, l in enumerate(lines) if l.strip() == "### Профилактика")
    except StopIteration:
        return lines, []
    out = []
    for i in range(start + 1, len(lines)):
        l = lines[i]
        if l.startswith("#"):
            break
        c = [x.strip() for x in l.strip().strip("|").split("|")]
        if l.startswith("|") and len(c) >= 4 and not set(c[0]) <= set("-: ") and c[0] != "Процедура":
            out.append((i, c))
    return lines, out


def items(today=None):
    """Все строки с известной датой и периодом: имя, период, последний раз, срок, выключено ли."""
    today = today or date.today()
    _, rows = _rows(open(MED, encoding="utf-8").read())
    out = []
    for _, c in rows:
        if not c[1].isdigit() or not re.fullmatch(r"\d{4}-\d\d-\d\d", c[2]):
            continue
        last = date.fromisoformat(c[2])
        nxt = add_months(last, int(c[1]))
        out.append({"name": c[0], "key": key(c[0]), "months": int(c[1]), "last": last, "next": nxt,
                    "off": OFF in c[3].lower(), "late": (today - nxt).days})
    return out


def load():
    try:
        return json.load(open(STATE, encoding="utf-8"))
    except Exception:
        return {}


def save(st):
    tmp = STATE + ".tmp"
    json.dump(st, open(tmp, "w", encoding="utf-8"), ensure_ascii=False, indent=1)
    os.replace(tmp, STATE)


def due(today=None, st=None):
    """Что показать сегодня: срок близко или прошёл, не выключено, не в тишине, не показывали неделю."""
    today = today or date.today()
    st = load() if st is None else st
    out = []
    for it in items(today):
        s = st.get(it["key"], {})
        if it["off"] or it["next"] - timedelta(days=LEAD_DAYS) > today:
            continue
        if s.get("quiet") and date.fromisoformat(s["quiet"]) > today:
            continue
        if s.get("shown") and (today - date.fromisoformat(s["shown"])).days < REPEAT_DAYS:
            continue
        out.append(it)
    return sorted(out, key=lambda x: -x["late"])[:MAX_PER_DAY]


def icon(name):
    n = name.lower()
    return next((i for w, i in ICONS if w in n), "🩺")


def text_of(it):
    when = (f"срок был {it['next']:%d.%m.%Y} — прошло {it['late']} дн." if it["late"] > 0 else
            "срок — сегодня" if it["late"] == 0 else f"срок — {it['next']:%d.%m.%Y}")
    return (f"{icon(it['name'])} Пора: {it['name']}\n"
            f"Последний раз — {it['last']:%d.%m.%Y}, повтор раз в {it['months']} мес.: {when}.\n"
            "Нажмите, когда пройдёте; до этого напомню раз в неделю.")


def buttons(it):
    k = it["key"]
    return {"inline_keyboard": [
        [{"text": "✅ Прошёл", "callback_data": f"pf:ok:{k}"},
         {"text": "📅 Записался", "callback_data": f"pf:bk:{k}"}],
        [{"text": "⏭ Через месяц", "callback_data": f"pf:mo:{k}"},
         {"text": "🚫 Не напоминать", "callback_data": f"pf:off:{k}"}]]}


def _edit_row(k, fn):
    """Поменять строку таблицы с этим ключом; fn(ячейки) → ячейки. Возвращает имя или None."""
    text = open(MED, encoding="utf-8").read()
    lines, rows = _rows(text)
    for i, c in rows:
        if key(c[0]) == k:
            c = fn(list(c))
            lines[i] = "| " + " | ".join(c) + " |"
            tmp = MED + ".tmp"
            open(tmp, "w", encoding="utf-8").write("\n".join(lines))
            os.replace(tmp, MED)
            return c[0]
    return None


def press(action, k, today=None):
    """Нажатие кнопки. Возвращает текст итога."""
    today = today or date.today()
    st = load()
    s = st.setdefault(k, {})
    if action == "ok":
        def mark(c):
            c[2] = today.isoformat()
            c[3] = f"отметка в боте {today:%d.%m.%Y}"
            return c
        name = _edit_row(k, mark)
        if not name:
            return "Не нашёл эту строку в таблице — скажите агенту, когда прошли."
        st.pop(k, None)
        save(st)
        nxt = next((x["next"] for x in items(today) if x["key"] == k), None)
        return f"✅ Записал: {name} — {today:%d.%m.%Y}." + (f" Следующий раз — {nxt:%m.%Y}." if nxt else "") + \
            " Если были в другой день — скажите агенту дату."
    if action in QUIET:
        s["quiet"] = (today + timedelta(days=QUIET[action])).isoformat()
        save(st)
        return ("📅 Хорошо, не напоминаю 3 недели, потом спрошу, прошли ли." if action == "bk" else
                f"⏭ Напомню после {date.fromisoformat(s['quiet']):%d.%m}.")
    if action == "off":
        def off(c):
            c[3] = f"{OFF} (кнопка {today:%d.%m.%Y})" + (f"; {c[3]}" if c[3] and c[3] != "—" else "")
            return c
        name = _edit_row(k, off)
        return f"🚫 Больше не напоминаю: {name}. Вернуть — скажите агенту." if name else "Строка не найдена."
    return "Не понял кнопку."


def notify(token, chat, today=None):
    """После утренней сводки: прислать то, что пора. Возвращает число отправленных."""
    today = today or date.today()
    st, n = load(), 0
    for it in due(today, st):
        data = urllib.parse.urlencode({"chat_id": chat, "text": text_of(it),
                                       "reply_markup": json.dumps(buttons(it), ensure_ascii=False)}).encode()
        urllib.request.urlopen(f"https://api.telegram.org/bot{token}/sendMessage", data, timeout=30)
        st.setdefault(it["key"], {})["shown"] = today.isoformat()
        save(st)
        n += 1
    return n


if __name__ == "__main__":
    t = date.fromisoformat(sys.argv[2]) if len(sys.argv) > 2 else date.today()
    for it in items(t):
        print(f"{it['name']}: был {it['last']}, срок {it['next']}, {'выкл' if it['off'] else 'вкл'}")
    print("--- пора сегодня:")
    for it in due(t, {}):
        print(text_of(it), "\n")
