#!/usr/bin/env python3
"""Выписки по кредитке ТБанка из Gmail → календарь платежей.
Банк присылает письмо раз в месяц (~17-го): период, «оплатить до», минимальный платёж, платёж без процентов, долг.
Операций в письме нет. Скрипт без ИИ: разбирает письмо регулярками, пишет строку в «Ближайшие»
`04_Календарь платежей.md`, а оплату сверяет по `02_Операции.csv` (пополнения кредитки после конца периода).
Письма не помечает прочитанными. Запуск: из утренней сводки (brief.py) — update(); вручную — cc_statement.py [print]."""
import csv, email, email.utils, imaplib, os, re, subprocess, sys
from datetime import date, datetime, timedelta
from email.header import decode_header, make_header

WORKDIR = "__WORKDIR__"
FIN = os.path.join(WORKDIR, "02_Глубокий контекст/07_Деньги_и_бизнес/02_Финансы")
CAL = os.path.join(FIN, "04_Календарь платежей.md")
OPS = os.path.join(FIN, "02_Операции.csv")
PULT = os.path.join(WORKDIR, "__ПАПКА_АГЕНТА__/20_Личный пульт/сборка/make_personal.py")
CONTRACT = "__НОМЕР_ДОГОВОРА_КРЕДИТКИ__"   # кредитка ТБанк •__КАРТА_КРЕДИТКИ__ (01_Счета и карты.md)
CARD = "кредитка •__КАРТА_КРЕДИТКИ__ (ТБанк)"
SENDER = "inform@emails.tinkoff.ru"
MARK = "Кредитка ТБанк, выписка "


def num(s):
    return float(s.replace(" ", "").replace("\xa0", "").replace(",", "."))


def money(x):
    s = f"{x:,.2f}".replace(",", " ").replace(".", ",")
    return s[:-3] if s.endswith(",00") else s


def html_text(msg):
    for p in msg.walk():
        if p.get_content_type() == "text/html":
            h = p.get_payload(decode=True).decode(p.get_content_charset() or "utf-8", "replace")
            h = re.sub(r"(?is)<(style|script).*?</\1>", " ", h)
            t = re.sub(r"<[^>]+>", "|", h)
            t = re.sub(r"&nbsp;|\xa0", " ", t)
            t = re.sub(r"\|[\s|]*", "|", t)
            return re.sub(r"\s+", " ", t)
    return ""


def parse(text, sent):
    """Письмо → dict. В письме сумма стоит ПЕРЕД своей подписью: «600 руб. | Минимальный платеж»."""
    m = re.search(r"За период \|(\d\d\.\d\d\.\d{4}) — (\d\d\.\d\d\.\d{4})\|Оплатить до (\d\d\.\d\d\.\d{4})", text)
    if not m:
        return None
    d = lambda s: datetime.strptime(s, "%d.%m.%Y").date()
    st = {"start": d(m.group(1)), "end": d(m.group(2)), "due": d(m.group(3)), "sent": sent}
    for val, label in re.findall(r"\|([\d ]+(?:,\d+)?) руб\. \|([^|]+)", text[m.end():]):
        label = label.strip().lower()
        key = ("free" if "беспроцент" in label else "min_inst" if "рассрочк" in label else
               "min" if label.startswith("минимальный") else "debt" if label.startswith("задолженность") else None)
        if key and key not in st:
            st[key] = num(val)
    return st


def fetch(days=75):
    m = imaplib.IMAP4_SSL("imap.gmail.com", timeout=60)
    m.login(os.environ["MAIL_USER"], os.environ["MAIL_PASS"])
    m.select("INBOX", readonly=True)
    _, d = m.uid("search", None, "X-GM-RAW", f'"from:{SENDER} newer_than:{days}d"')
    out = []
    for u in d[0].split():
        _, x = m.uid("fetch", u, "(BODY.PEEK[HEADER.FIELDS (SUBJECT DATE)])")
        h = email.message_from_bytes(x[0][1])
        if "кредитной карте" not in str(make_header(decode_header(h.get("Subject", "")))):
            continue
        _, x = m.uid("fetch", u, "(BODY.PEEK[])")
        msg = email.message_from_bytes(x[0][1])
        sent = email.utils.parsedate_to_datetime(msg.get("Date")).date()
        st = parse(html_text(msg), sent)
        if st:
            out.append(st)
    m.logout()
    return out


def paid_sum(st):
    """Пополнения кредитки после конца периода и до срока (включительно) — по базе операций."""
    total = 0.0
    try:
        with open(OPS, encoding="utf-8") as f:
            for r in csv.DictReader(f, delimiter=";"):
                if r["договор"] != CONTRACT or r["категория"] != "Перевод между своими":
                    continue
                dt = date.fromisoformat(r["дата"])
                s = float(r["сумма"])
                if st["end"] < dt <= st["due"] and s > 0:
                    total += s
    except Exception as e:
        print("операции:", e)
    return total


def row(st):
    need = st.get("free", st.get("min_inst", st.get("min", 0.0)))
    what = "платёж без процентов" if "free" in st else "платёж"
    extra = []
    if "min" in st:
        extra.append(f"минимальный — {money(st['min'])}")
    if "debt" in st:
        extra.append(f"долг на {st['end']:%d.%m} — {money(st['debt'])}")
    paid = paid_sum(st)
    status = "оплачено" if need <= 0 or paid + 0.005 >= need else ("проверить" if st["due"] < date.today() else "ждёт")
    if 0 < paid < need:
        extra.append(f"уже внесено {money(paid)}")
    desc = f"{MARK}{st['start']:%d.%m}–{st['end']:%d.%m}: {what}" + (f" ({'; '.join(extra)})" if extra else "")
    return f"| {st['due'].isoformat()} | {money(need)} | {desc} | {CARD} | письмо банка {st['sent']:%d.%m} (автоматически) | {status} |", need, status


def update(dry=False):
    """Возвращает строки для утренней сводки о новых выписках. Меняет календарь и пересобирает пульт, если что-то изменилось."""
    sts = [s for s in fetch() if s["due"] >= date.today() - timedelta(days=3)]
    lines = open(CAL, encoding="utf-8").read().split("\n")
    try:
        start = lines.index("## Ближайшие")
    except ValueError:
        return ["Календарь платежей: нет раздела «Ближайшие» — выписку по кредитке не записал"]
    rows = [i for i in range(start + 1, len(lines)) if lines[i].startswith("| ")]
    if len(rows) < 2:
        return ["Календарь платежей: нет таблицы в «Ближайших» — выписку по кредитке не записал"]
    news, changed = [], False
    for st in sorted(sts, key=lambda s: s["due"]):
        new, need, status = row(st)
        key = f"{MARK}{st['start']:%d.%m}–{st['end']:%d.%m}"
        have = [i for i in rows if key in lines[i]]
        if have:
            i = have[0]
            old_status = lines[i].rstrip(" |").rsplit("|", 1)[-1].strip()
            if old_status != status and old_status not in ("оплачено",):   # ручную отметку «оплачено» не трогаем
                lines[i] = new
                changed = True
            continue
        # новая выписка — вставить по дате; rows[0] — заголовок (разделитель «|---» в rows не попадает), дальше строки платежей
        body = rows[1:]
        later = [i for i in body if re.match(r"\| \d{4}-\d\d-\d\d", lines[i]) and lines[i][2:12] > st["due"].isoformat()]
        pos = later[0] if later else ((body[-1] + 1) if body else rows[0] + 2)
        lines.insert(pos, new)
        rows = [i for i in range(start + 1, len(lines)) if lines[i].startswith("| ")]
        changed = True
        if status != "оплачено":
            news.append(f"💳 Кредитка: до {st['due']:%d.%m} внести {money(need)} ₽ без процентов"
                        + (f" (минимум {money(st['min'])})" if "min" in st else ""))
    if changed and not dry:
        tmp = CAL + ".tmp"
        open(tmp, "w", encoding="utf-8").write("\n".join(lines))
        os.replace(tmp, CAL)
        subprocess.run([sys.executable, PULT], capture_output=True, timeout=120)
    if dry:
        print("\n".join(l for l in lines if MARK in l) or "строк нет")
    return news


if __name__ == "__main__":
    print("\n".join(update(dry=len(sys.argv) > 1 and sys.argv[1] == "print")) or "новых выписок к оплате нет")
