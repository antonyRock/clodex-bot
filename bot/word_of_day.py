#!/usr/bin/env python3
"""Слово дня для утренней сводки: одно слово или выражение из личного словаря владельца со значением.
Источник — файл словаря (разделы «## Слова» и «## Выражения и поговорки»), строки вида «- **слово** — значение».
Без повторов, пока не пройдёт весь список; когда прошёл — круг начинается заново. Новые слова в словаре попадают в очередь сами.
Состояние — json на сервере (какие уже показаны и какое слово выдано сегодня): в тот же день повторный запуск даёт то же слово.
Запуск: word_of_day.py — показать, что будет сегодня (ничего не записывает)."""
import json, os, random, re
from datetime import date

WORKDIR = "__WORKDIR__"
DICT = "02_Глубокий контекст/11_Стиль_речи_и_письма/02_Словарь.md"
STATE = "/root/tg-bot/word_of_day.json"
SECTIONS = ("слова", "выражения и поговорки")
ENTRY = re.compile(r"^- \*\*(.+?)\*\*\s*(.*)$")


def load_words(path=None):
    """[(слово, значение)] из нужных разделов. Продолжение записи (строки с отступом) склеивается в одну."""
    path = path or os.path.join(WORKDIR, DICT)
    try:
        lines = open(path, encoding="utf-8").read().splitlines()
    except OSError:
        return []
    out, section, cur = [], None, None
    for line in lines + ["## конец"]:
        if line.startswith("## "):
            if cur:
                out.append(cur)
            cur, section = None, line[3:].strip().lower()
            continue
        if section not in SECTIONS:
            continue
        m = ENTRY.match(line)
        if m:
            if cur:
                out.append(cur)
            cur = [m.group(1).strip(), m.group(2).strip()]
        elif cur and line.startswith(" ") and line.strip():
            cur[1] += " " + line.strip()
        elif cur and not line.strip():
            out.append(cur)
            cur = None
    return [(w, clean(d)) for w, d in out]


def clean(text):
    text = re.sub(r"^[—–-]\s*", "", text)          # «— значение» → «значение»
    text = re.sub(r"\s*\((?:агент|дополнение агента|окончание — агент[^)]*|дата вымирания — агент|агент; [^)]*)\)\s*$", "", text)   # служебные метки
    return text.strip()


def pick(commit=True):
    """Слово на сегодня: (слово, значение) или None, если словарь пуст. commit=False — не записывать состояние."""
    words = load_words()
    if not words:
        return None
    today = date.today().isoformat()
    try:
        st = json.load(open(STATE, encoding="utf-8"))
    except (OSError, ValueError):
        st = {}
    names = {w for w, _ in words}
    if st.get("today", {}).get("date") == today and st["today"].get("word") in names:
        w = st["today"]["word"]
        return w, dict(words)[w]
    shown = [w for w in st.get("shown", []) if w in names]        # удалённые из словаря слова выпадают сами
    left = [(w, d) for w, d in words if w not in shown]
    if not left:                                                  # круг пройден — начинаем заново
        shown, left = [], words
    w, d = random.choice(left)
    if commit:
        os.makedirs(os.path.dirname(STATE), exist_ok=True)
        json.dump({"shown": shown + [w], "today": {"date": today, "word": w}}, open(STATE, "w", encoding="utf-8"), ensure_ascii=False)
    return w, d


def line(commit=True):
    """Строка для сводки или пустая строка, если слов нет."""
    p = pick(commit)
    if not p:
        return ""
    w, d = p
    sep = "" if not d else " " if d.startswith("(") else " — "   # «Слово (этимология) — значение», «Слово — значение», «Поговорка» без значения
    return f"📖 Слово дня: {w}{sep}{d}"


if __name__ == "__main__":
    print(line(commit=False) or "словарь пуст или не найден")
