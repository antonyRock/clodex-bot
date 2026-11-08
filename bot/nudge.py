#!/usr/bin/env python3
"""Вопрос дня в Telegram: раз в день в случайное время 11:00–18:00
бот присылает одну нерешённую задачу __ИМЯ_Р__ из единой базы и просит ответить.
Берёт открытые задачи без срока, где делает __ИМЯ__ (задачи со сроком и так есть в утренней сводке).
Выходные — только личные. Одну и ту же задачу не повторяет 14 дней. Без ИИ — токены не тратит.
Ответ на сообщение бот разносит по базе (bot.py, with_nudge; данные — nudge_last.json).
Запуск: nudge.py — отправить; nudge.py print — показать, ничего не отправлять и не запоминать."""
import json, os, random, sys, urllib.parse, urllib.request
from datetime import date, datetime, timedelta

sys.path.insert(0, "/root/tg-bot")
from brief import registry, send  # noqa: E402

LAST = "/root/tg-bot/nudge_last.json"
STATE = "/root/tg-bot/nudge_state.json"   # {ID задачи: дата последнего вопроса}
REPEAT_DAYS = 14


def pick():
    today = date.today()
    weekend = today.weekday() >= 5
    try:
        asked = json.load(open(STATE, encoding="utf-8"))
    except Exception:
        asked = {}
    pool = []
    for t in registry().load_tasks():
        if t["status"] not in " /!?" or t["due"] or "__ИМЯ__" not in t["who"]:
            continue
        if weekend and not t["id"].startswith("Л"):
            continue
        pool.append(t)
    if not pool:
        return None, asked
    cutoff = (today - timedelta(days=REPEAT_DAYS)).isoformat()
    fresh = [t for t in pool if asked.get(t["id"], "") < cutoff]
    never = [t for t in fresh if t["id"] not in asked]
    if never:
        # сначала проект, потом задача: иначе 20 мелких дел по даче забивают всё остальное
        groups = {}
        for t in never:
            groups.setdefault(t["project"] or "—", []).append(t)
        return random.choice(random.choice(list(groups.values()))), asked
    if fresh:
        return min(fresh, key=lambda t: asked[t["id"]]), asked
    return min(pool, key=lambda t: asked.get(t["id"], "")), asked


def text_of(t):
    names = {c: p["name"] for c, p in registry().load_projects().items()} if t["project"] else {}
    lines = ["🤔 Вопрос дня", "", t["title"]]
    if t["project"]:
        lines.append(f"Проект: {names.get(t['project'], t['project'])}")
    for k in ("зачем", "дальше", "заметка"):
        if t["extra"].get(k):
            lines.append(f"{k.capitalize()}: {t['extra'][k]}")
    lines += ["", "Ответь на это сообщение (можно голосом): «сделано», «перенеси на пятницу», "
              "«не актуально» или просто расскажи, что знаешь, — запишу."]
    return "\n".join(lines)


if __name__ == "__main__":
    t, asked = pick()
    if not t:
        print("нет подходящих задач")
        sys.exit()
    msg = text_of(t)
    if len(sys.argv) > 1 and sys.argv[1] == "print":
        print(msg)
        sys.exit()
    r = send(msg)
    asked[t["id"]] = date.today().isoformat()
    json.dump(asked, open(STATE, "w"), ensure_ascii=False)
    json.dump({"date": date.today().isoformat(), "sent": datetime.now().isoformat(timespec="minutes"),
               "message_id": r["result"]["message_id"], "id": t["id"], "t": t["title"], "src": t["file"]},
              open(LAST, "w"), ensure_ascii=False)
    print("отправлено:", t["id"])
