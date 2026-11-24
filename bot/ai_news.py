#!/usr/bin/env python3
"""Новости ИИ в Telegram: каждый день в 11:00 — пять главных новостей мира ИИ за сутки.
Ищет и пишет Claude (sonnet, по подписке) только с инструментами WebSearch и WebFetch: файлов не видит,
команд не запускает. Текст сообщения собирает этот скрипт из JSON ответа: короткий заголовок,
1–2 фразы, ссылка на источник. Ссылки проверяются запросом: битая (404, нет домена) — пункт выбрасывается.
Темы за 7 дней помнит (ai_news_state.json) и передаёт в запрос, чтобы новости не повторялись.
Запуск: ai_news.py — отправить; ai_news.py print — показать, ничего не отправлять и не запоминать;
ai_news.py preview — прислать черновик в Telegram, не запоминать."""
import html, json, os, re, subprocess, sys, urllib.error, urllib.request
from datetime import date, datetime, timedelta

sys.path.insert(0, "/root/tg-bot")
from brief import CLAUDE, DAYS, MONTHS, send  # noqa: E402

STATE = "/root/tg-bot/ai_news_state.json"   # [{"d": дата, "t": заголовок, "u": ссылка}] за 7 дней
SANDBOX = "/root/tg-bot/newscheck"            # пустая папка: Claude не видит файлов
REMEMBER_DAYS = 7
COUNT = 5
DENY = "Bash,Read,Write,Edit,MultiEdit,Glob,Grep,NotebookEdit,TodoWrite,Task"
NUM = ["1️⃣", "2️⃣", "3️⃣", "4️⃣", "5️⃣", "6️⃣", "7️⃣"]
UA = "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/130 Safari/537.36"

PROMPT = """Ты — редактор ежедневного дайджеста новостей об искусственном интеллекте для занятого продакт-менеджера.
Сегодня {today} ({weekday}), 11:00 по Москве. Найди через WebSearch самые важные новости мира ИИ
за последние ~24 часа (с {since}). Нужно ровно {count} новостей, сделай несколько разных поисков
(новые модели и релизы, крупные компании ИИ, сделки и инвестиции, регулирование, исследования, железо для ИИ).

Как отбирать:
- Значимость для индустрии, а не шум: выпуск или крупное обновление модели, продукта; сделки от ~$500 млн;
  законы и решения регуляторов; заметные исследования; чипы и дата-центры; громкие кадровые события.
- Одна новость — одно событие; пять разных тем, не пять новостей одной компании.
- Только факты, которые подтверждает источник. Слухи — только если о них пишут крупные издания, и помечай «по данным <издание>».
- Источник — первоисточник (блог компании, пресс-релиз, документ) или надёжное издание: Reuters, Bloomberg,
  The Verge, TechCrunch, The Information, Ars Technica, Wired, FT, CNBC, VentureBeat. Ссылка — на конкретную статью,
  которую ты видел в результатах поиска или открыл. Не придумывай и не собирай ссылки по шаблону.
- Дайджесты и рассылки (The Neuron, TLDR, Ben's Bites и т. п.), Reddit, X, YouTube, агрегаторы — только как подсказка,
  что искать. Для каждой новости найди отдельным поиском оригинальную статью или первоисточник и дай ссылку на неё.
  У каждой новости своя ссылка; две новости с одной ссылкой нельзя.
- Новость старше 48 часов не бери.
- Не повторяй темы, о которых уже писали за последние дни (если нет важного продолжения — тогда так и напиши, что нового):
{seen}

Пиши по-русски, просто и сухо, без оценок вроде «революционный». Названия компаний и продуктов — как в оригинале.
В тексте — только сама новость: без слов о том, откуда ты её взял, что открывал или не открывал.
Ответ — ТОЛЬКО JSON, без пояснений до и после:
{{"items": [{{"emoji": "один смайлик по теме", "title": "заголовок до 60 знаков: кто что сделал",
"text": "1–2 предложения до 230 знаков: что произошло и почему это важно", "source": "название издания или компании",
"url": "https://..."}}]}}
Порядок — от самой важной новости к менее важной."""


def load_state():
    try:
        rows = json.load(open(STATE, encoding="utf-8"))
    except Exception:
        return []
    cutoff = (date.today() - timedelta(days=REMEMBER_DAYS)).isoformat()
    return [r for r in rows if r.get("d", "") >= cutoff]


def ask(seen):
    today = date.today()
    seen_txt = "\n".join(f"  - {r['d']}: {r['t']}" for r in seen) or "  (пока ничего)"
    prompt = PROMPT.format(today=today.isoformat(), weekday=DAYS[today.weekday()], count=COUNT,
                           since=(datetime.now() - timedelta(hours=24)).strftime("%Y-%m-%d %H:%M"), seen=seen_txt)
    env = {k: v for k, v in os.environ.items() if k not in ("TG_TOKEN", "MAIL_PASS", "YA_PASS")}
    os.makedirs(SANDBOX, exist_ok=True)
    p = subprocess.run([CLAUDE, "-p", "--model", "sonnet", "--output-format", "json",
                        "--allowedTools", "WebSearch,WebFetch", "--disallowedTools", DENY,
                        "--no-session-persistence"],
                       input=prompt, cwd=SANDBOX, env=env, capture_output=True, text=True, timeout=840)
    if p.returncode:
        raise RuntimeError(f"claude вышел с кодом {p.returncode}: {(p.stderr or p.stdout)[-300:]}")
    res = json.loads(p.stdout)["result"]
    return json.loads(re.search(r"\{.*\}", res, re.S).group(0))["items"]


def link_ok(url):
    """Битая ссылка — False. Сайты, которые не пускают роботов (401/403/429, тайм-аут), считаем живыми."""
    if not re.match(r"https?://[^/\s]+\.[^/\s]+", url or ""):
        return False
    req = urllib.request.Request(url, headers={"User-Agent": UA})
    try:
        urllib.request.urlopen(req, timeout=15).close()
        return True
    except urllib.error.HTTPError as e:
        return e.code not in (404, 410)
    except urllib.error.URLError as e:
        return "timed out" in str(e.reason)
    except Exception:
        return True


AGGREGATORS = re.compile(r"theneuron\.ai|tldr\.tech|bensbites|reddit\.com|(^|//)(www\.)?(x|twitter)\.com|"
                         r"youtube\.com|news\.google\.|news\.ycombinator", re.I)


def clean(items):
    out, urls = [], set()
    for it in items:
        if not isinstance(it, dict) or not it.get("title") or not it.get("url"):
            continue
        u = it["url"].strip()
        if u in urls or AGGREGATORS.search(u):
            print("повтор ссылки или агрегатор, пункт убран:", u)
            continue
        urls.add(u)
        if not link_ok(it["url"]):
            print("битая ссылка, пункт убран:", it["url"])
            continue
        out.append(it)
    return out[:COUNT]


def render(items):
    today = date.today()
    e = html.escape
    lines = [f"🤖 <b>ИИ: главное за сутки</b>",
             f"<i>{DAYS[today.weekday()].capitalize()}, {today.day} {MONTHS[today.month - 1]}</i>", ""]
    for i, it in enumerate(items):
        lines.append(f"{NUM[i]} {e(it.get('emoji', '').strip())} <b>{e(it['title'].strip())}</b>".replace("  ", " "))
        if it.get("text"):
            lines.append(e(it["text"].strip()))
        lines.append(f'🔗 <a href="{e(it["url"], quote=True)}">{e((it.get("source") or "Источник").strip())}</a>')
        lines.append("")
    return "\n".join(lines).rstrip()


if __name__ == "__main__":
    mode = sys.argv[1] if len(sys.argv) > 1 else "send"
    seen = load_state()
    items, err = [], ""
    for attempt in range(2):
        try:
            items = clean(ask(seen))
            if len(items) >= 3:
                break
            err = f"нашлось только {len(items)} новостей с живыми ссылками"
        except Exception as ex:   # ответ не JSON, тайм-аут, сбой claude
            err = f"{type(ex).__name__}: {ex}"[:300]
        print("попытка", attempt + 1, "не удалась:", err)
    if not items:
        if mode == "print":
            print("не получилось:", err)
        else:
            send("⚠️ Новости ИИ сегодня не собрались — поиск не ответил. Завтра в 11:00 попробую снова.", html_mode=False)
        sys.exit(1)
    text = render(items)
    if mode == "print":
        print(text)
        sys.exit()
    if mode == "preview":
        text = "🧪 <i>Черновик — так будет приходить каждый день в 11:00</i>\n\n" + text
    print("отправлено:", send(text).get("ok"))
    if mode == "send":
        seen += [{"d": date.today().isoformat(), "t": it["title"], "u": it["url"]} for it in items]
        json.dump(seen, open(STATE, "w", encoding="utf-8"), ensure_ascii=False, indent=1)
