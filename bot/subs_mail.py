#!/usr/bin/env python3
"""Чеки иностранных подписок из почты → `09_Чеки подписок.md` в финансовой базе (2026-11-08).
Эти подписки не видны в выписках ТБанка:
- Stripe — Gmail, письма «Your receipt from <Сервис> #<номер>»;
- App Store (ChatGPT, Claude, iCloud+ и др.) — ящик Apple ID __APPLE_ID_MAIL__, «Квитанция от Apple» / «Your receipt|invoice from Apple»,
  оплата картой в валюте карты.
Без ИИ: регулярки. Ключ — номер чека (у Apple — ID заказа), дублей нет. Письма не помечает прочитанными.
Пульт берёт отсюда последнее списание и следующую дату. Неудачная оплата ловится почтовым сторожем (mail_watch).
Запуск: из утренней сводки (brief.py) — update(); вручную — subs_mail.py [print]."""
import datetime, email, html, imaplib, os, re, sys, time
from email.header import decode_header, make_header
from cc_statement import FIN

OUT = os.path.join(FIN, "09_Чеки подписок.md")
HEAD = """# Чеки подписок

Списания по подпискам, которых нет в выписках ТБанка: чеки Stripe из Gmail и чеки App Store из ящика Apple ID __APPLE_ID_MAIL__. Заполняет `subs_mail.py` на сервере из утренней сводки, без ИИ, без дублей. Пульт сопоставляет «Сервис» с названием в `07_Подписки.md` (название подписки содержит «Сервис») и берёт отсюда последнее списание и следующую дату (= «Период по»).

| Сервис | Оплачено | Сумма | Валюта | Период по | Карта | Чек | Аккаунт |
|---|---|---|---|---|---|---|---|
"""
EN = ["jan", "feb", "mar", "apr", "may", "jun", "jul", "aug", "sep", "oct", "nov", "dec"]
RU = ["янв", "фев", "мар", "апр", "мая", "июн", "июл", "авг", "сен", "окт", "ноя", "дек"]


def mon(w):
    w = w.lower()[:3]
    if w in EN: return EN.index(w) + 1
    if w == "май": return 5
    return RU.index(w) + 1 if w in RU else None


def day(s):
    """«30 октября 2026 г.», «22 October 2026», «Nov 4, 2026» → ISO."""
    r = re.search(r"(\d{1,2}) ([A-Za-zА-Яа-яё]+)\.? (\d{4})", s) or None
    if r and mon(r.group(2)):
        return datetime.date(int(r.group(3)), mon(r.group(2)), int(r.group(1))).isoformat()
    r = re.search(r"([A-Za-z]{3})\w* (\d{1,2}), (\d{4})", s)
    if r and mon(r.group(1)):
        return datetime.date(int(r.group(3)), mon(r.group(1)), int(r.group(2))).isoformat()
    return ""


def text(msg):
    plain = ""
    for p in msg.walk():
        if p.get_content_type() == "text/html":
            h = p.get_payload(decode=True).decode(p.get_content_charset() or "utf-8", "replace")
            h = re.sub(r"(?is)<(style|script).*?</\1>", " ", h)
            t = html.unescape(re.sub(r"<[^>]+>", "|", h))
            return re.sub(r"\s+", " ", re.sub(r"\|[\s|]*", "|", t))
        if p.get_content_type() == "text/plain" and not plain:
            plain = re.sub(r"\s+", " ", p.get_payload(decode=True).decode(p.get_content_charset() or "utf-8", "replace"))
    return plain


def num(s):
    return f"{float(s.replace(' ', '').replace(' ', '').replace(',', '.')):.2f}"


def stripe(subj, txt):
    s = re.search(r"receipt from (.+?) #([\w-]+)", subj, re.I)
    txt = re.sub(r"\s*\|\s*", " ", txt)
    paid = re.search(r"\$\s?([\d,]+\.\d\d)\s*Paid on ([A-Za-z]+ \d{1,2}, \d{4})", txt)
    if not (s and paid):
        return []
    per = re.search(r"[A-Za-z]{3} \d{1,2}\s*[–-]\s*([A-Za-z]{3} \d{1,2}, \d{4})", txt)
    card = re.search(r"with\W+(\d{4})\b", txt)
    return [{"v": s.group(1).strip(), "d": day(paid.group(2)), "sum": paid.group(1).replace(",", ""), "cur": "USD",
             "to": day(per.group(1)) if per else "", "card": card.group(1) if card else "", "n": s.group(2), "acc": ""}]


def norm(prod):
    p = re.sub(r"\s*\((?:Monthly|ежемесячный|1 month|1 мес\.?)\)|\s*-\s*Monthly", "", prod).strip()
    p = re.sub(r"iCloud\+ with (\d+) ?TB.*", r"iCloud+ \1 ТБ", p)
    return re.sub(r"ЛитРес: Подписка.*", "ЛитРес", p)


def apple(subj, txt):
    if not re.search(r"Квитанция от Apple|(receipt|invoice) from Apple", subj, re.I):
        return []
    order = re.search(r"(?:ID заказа|Order ID|ORDER ID):?\|([A-Z0-9]+)", txt)
    acc = re.search(r"(?:Аккаунт Apple|Apple Account|APPLE ACCOUNT):?\|([^|]+)", txt)
    dt = re.search(r"\|(?:Счет|Invoice)\|([^|]+\d{4}[^|]*)\|", txt) or re.search(r"INVOICE DATE\|([^|]+)", txt)
    card = re.search(r"(?:Visa|Mastercard|MasterCard)\s*[•.]+\s*(\d{4})", txt)
    out = []
    for prod, ren, amt in re.findall(r"\|([^|]{3,90}?)\|(?:Возобновится|Renews) ([^|]+)\|(?:[^|]*\([^|]*\)\|)?(₸\s?[\d\s ]+,\d\d|[\d\s ]+,\d\d\s?₸)\|", txt):
        out.append({"v": norm(prod), "d": day(dt.group(1)) if dt else "", "sum": num(amt.replace("₸", "")), "cur": "KZT",
                    "to": day(ren), "card": card.group(1) if card else "", "n": order.group(1) if order else "",
                    "acc": acc.group(1).strip() if acc else ""})
    return [o for o in out if o["n"] and o["d"]]


def login(host, user, pw):
    for attempt in range(3):  # Яндекс иногда рвёт первое соединение
        try:
            m = imaplib.IMAP4_SSL(host, timeout=60)
            m.login(user, pw)
            return m
        except Exception:
            if attempt == 2:
                raise
            time.sleep(10)


def scan(m, uids, parser):
    out = []
    for u in uids:
        _, x = m.uid("fetch", u, "(BODY.PEEK[])")
        msg = email.message_from_bytes(x[0][1])
        out += parser(str(make_header(decode_header(msg.get("Subject", "")))), text(msg))
    return out


def fetch(days=400):
    out = []
    m = login("imap.gmail.com", os.environ["MAIL_USER"], os.environ["MAIL_PASS"])
    if m.select('"[Gmail]/All Mail"', readonly=True)[0] != "OK":   # у русского интерфейса папка может зваться иначе
        m.select("INBOX", readonly=True)
    _, d = m.uid("search", None, "X-GM-RAW", f'"from:stripe.com subject:receipt newer_than:{days}d"')
    out += scan(m, d[0].split(), stripe)
    m.logout()
    if os.environ.get("APPLE_USER"):
        m = login("imap.yandex.ru", os.environ["APPLE_USER"], os.environ["APPLE_PASS"])
        m.select("INBOX", readonly=True)
        since = (datetime.date.today() - datetime.timedelta(days=days)).strftime("%d-%b-%Y")
        _, d = m.uid("search", None, f'(SINCE {since} FROM "apple.com")')
        out += scan(m, d[0].split(), apple)
        m.logout()
    return out


def load():
    rows = {}
    if os.path.exists(OUT):
        for l in open(OUT, encoding="utf-8"):
            c = [x.strip() for x in l.strip().strip("|").split("|")]
            if l.startswith("| ") and len(c) == 8 and re.match(r"\d{4}-\d\d-\d\d$", c[1]):
                rows[(c[6], c[0])] = c
    return rows


def update(dry=False):
    rows, new = load(), []
    for r in fetch():
        k = (r["n"], r["v"])
        if k in rows:
            continue
        rows[k] = [r["v"], r["d"], r["sum"], r["cur"], r["to"], r["card"], r["n"], r["acc"]]
        nxt = f", следующее ~{r['to'][8:]}.{r['to'][5:7]}" if r["to"] else ""
        cur = "$" if r["cur"] == "USD" else "₸"
        amt = f"{float(r['sum']):,.0f}".replace(",", " ")
        new.append(f"{r['v']}: списано {amt} {cur} {r['d'][8:]}.{r['d'][5:7]}{nxt}")
    if new and not dry:
        body = "".join("| " + " | ".join(c) + " |\n" for c in sorted(rows.values(), key=lambda c: (c[1], c[0])))
        tmp = OUT + ".tmp"
        open(tmp, "w", encoding="utf-8").write(HEAD + body)
        os.replace(tmp, OUT)
    return new


if __name__ == "__main__":
    print("\n".join(update(dry="print" in sys.argv)) or "новых чеков нет")
