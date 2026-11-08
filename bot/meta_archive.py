#!/usr/bin/env python3
"""Серверная часть выгрузки Meta (копия: /root/tg-bot/meta_archive.py). Вызывает meta_watch.mjs с Mac по SSH.
  meta_archive.py unpack <instagram|facebook> <имена ZIP в JSON-списке> <zip на сервере>...  — распаковать в /root/Claude_кэш/<src>/полная_json/,
      посчитать сообщения, при полной >= годовой удалить годовую папку, сообщить в Telegram; печатает JSON-итог.
  meta_archive.py notify <текст в JSON-строке>  — просто сообщение __ИМЯ_Д__.
Без ИИ. Токен бота — из /root/.config/tg-bot.env."""
import json, pathlib, re, shutil, sys, urllib.parse, urllib.request, zipfile
from datetime import datetime

CACHE = pathlib.Path("/root/Claude_кэш")


def env():
    e = {}
    for line in pathlib.Path("/root/.config/tg-bot.env").read_text().splitlines():
        if "=" in line and not line.lstrip().startswith("#"):
            k, v = line.split("=", 1)
            e[k.strip()] = v.strip().strip('"').strip("'")
    return e


def send(text):
    e = env()
    chat = (e.get("TG_ALLOWED") or "__TG_ID__").split(",")[0]
    data = urllib.parse.urlencode({"chat_id": chat, "text": text, "disable_web_page_preview": "true"}).encode()
    urllib.request.urlopen(f"https://api.telegram.org/bot{e['TG_TOKEN']}/sendMessage", data, timeout=30)


def count_json(root):
    chats, msgs, ts = 0, 0, []
    for f in root.rglob("message_*.json"):
        if "inbox" not in f.parts and "e2ee_cutover" not in f.parts:
            continue
        try:
            m = json.loads(f.read_text(encoding="utf-8")).get("messages", [])
        except Exception:
            continue
        msgs += len(m)
        ts += [x.get("timestamp_ms", 0) for x in m if x.get("timestamp_ms")]
        if f.name == "message_1.json":
            chats += 1
    years = (datetime.fromtimestamp(min(ts) / 1000).year, datetime.fromtimestamp(max(ts) / 1000).year) if ts else None
    return chats, msgs, years


def count_year_html(root):
    n = 0
    for f in root.rglob("message_*.html"):
        t = f.read_text(encoding="utf-8", errors="ignore")
        n += t.count('<div class="pam _3-95 _2ph- _a6-g uiBoxWhite noborder">') or t.count("_a6-p")
    return n


def unpack(src, names, zpaths):
    base = CACHE / src
    dst = base / "полная_json"
    if dst.exists():
        shutil.rmtree(dst)
    dst.mkdir(parents=True)
    for zpath in zpaths:
        with zipfile.ZipFile(zpath) as z:
            z.extractall(dst)   # extractall сам отсекает пути с «..» и абсолютные
        pathlib.Path(zpath).unlink()
    chats, msgs, years = count_json(dst)
    year_dir = base / "год-2025-11_html"
    year_msgs = count_year_html(year_dir) if year_dir.exists() else 0
    delete_year = bool(msgs) and msgs >= year_msgs
    if delete_year and year_dir.exists():
        shutil.rmtree(year_dir)
    title = "Instagram" if src == "instagram" else "Facebook"
    span = f"{years[0]}–{years[1]}" if years else "даты не найдены"
    text = (f"📦 {title}: полный архив сообщений скачан и выложен на сервер.\n"
            f"{chats} чатов, {msgs:,} сообщений ({span}).\n".replace(",", " ") +
            f"Сервер: /root/Claude_кэш/{src}/полная_json/\nZIP: 12_Интервью/01_Источники/{', '.join(names)}\n" +
            ("Годовой архив удалён — он внутри полного." if delete_year else
             f"⚠️ В полном меньше сообщений, чем в годовом ({year_msgs}) — годовой оставил, посмотри.") +
            "\nМожно приступать к разбору.")
    send(text)
    return {"chats": chats, "msgs": msgs, "years": years, "year_msgs": year_msgs, "delete_year": delete_year}


if __name__ == "__main__":
    if sys.argv[1] == "notify":
        send(json.loads(sys.argv[2]))
    elif sys.argv[1] == "unpack":
        print(json.dumps(unpack(sys.argv[2], json.loads(sys.argv[3]), sys.argv[4:]), ensure_ascii=False))
