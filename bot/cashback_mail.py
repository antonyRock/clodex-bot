#!/usr/bin/env python3
"""Кэшбэк ТБанка из Gmail → `08_Кэшбэк.md` в финансовой базе.
Банк раз в месяц присылает письмо «Кэшбэк по счету …»: сумма и период («1234.5 рублей Ваш кэшбэк за 28.09.2026 — 27.10.2026»).
Без ИИ: регулярки. Одна строка на счёт, период и сумму — повторный запуск дублей не создаёт. Письма не помечает прочитанными.
Пульт берёт кэшбэк из выписок, а из этого файла — только месяцы, которых в выписках ещё нет (двойного счёта нет).
Запуск: из утренней сводки (brief.py) — update(); вручную — cashback_mail.py [print]."""
import email, email.utils, imaplib, os, re, sys
from email.header import decode_header, make_header
from cc_statement import html_text, WORKDIR, FIN, SENDER

OUT = os.path.join(FIN, "08_Кэшбэк.md")
HEAD = """# Кэшбэк

Кэшбэк, который ТБанк сообщил в письмах «Кэшбэк по счету …» (заполняет `cashback_mail.py` на сервере из утренней сводки).
Это начисленный и выплаченный за период кэшбэк. Тот же кэшбэк потом виден в выписке операциями «Кэшбэк за …» — пульт не складывает их дважды:
месяц, который уже есть в выписках, берётся из выписок, из этого файла — только более новые месяцы.

| Счёт | Период с | Период по | Сумма, ₽ | Письмо от |
|---|---|---|---|---|
"""


def fetch(days=400):
    m = imaplib.IMAP4_SSL("imap.gmail.com", timeout=60)
    m.login(os.environ["MAIL_USER"], os.environ["MAIL_PASS"])
    m.select("INBOX", readonly=True)
    _, d = m.uid("search", None, "X-GM-RAW", f'"from:{SENDER} newer_than:{days}d"')
    out = []
    for u in d[0].split():
        _, x = m.uid("fetch", u, "(BODY.PEEK[HEADER.FIELDS (SUBJECT)])")
        subj = str(make_header(decode_header(email.message_from_bytes(x[0][1]).get("Subject", ""))))
        if not subj.lower().startswith("кэшбэк по счету"):
            continue
        _, x = m.uid("fetch", u, "(BODY.PEEK[])")
        msg = email.message_from_bytes(x[0][1])
        acc = subj.split("счету", 1)[1].strip() or "—"
        txt = html_text(msg)
        r = re.search(r"([\d\s]+(?:[.,]\d+)?)\s*рубл\w*[|\s]*Ваш кэшбэк за[|\s]*(\d\d\.\d\d\.\d{4})\s*[—-]\s*(\d\d\.\d\d\.\d{4})", txt)
        if not r:
            continue
        iso = lambda s: f"{s[6:]}-{s[3:5]}-{s[:2]}"
        out.append({"acc": acc, "from": iso(r.group(2)), "to": iso(r.group(3)),
                    "sum": float(r.group(1).replace(" ", "").replace(",", ".")),
                    "sent": email.utils.parsedate_to_datetime(msg.get("Date")).date().isoformat()})
    m.logout()
    return out


def load():
    rows = {}
    if os.path.exists(OUT):
        for l in open(OUT, encoding="utf-8"):
            c = [x.strip() for x in l.strip().strip("|").split("|")]
            if l.startswith("| ") and len(c) == 5 and re.match(r"\d{4}-\d\d-\d\d", c[1]):
                rows[(c[0], c[1], c[3])] = c
    return rows


def update(dry=False):
    rows, new = load(), []
    for r in fetch():
        s = f"{r['sum']:.2f}".replace(".", ",")
        k = (r["acc"], r["from"], s)   # у человека может быть несколько счетов — в одном периоде бывает два письма с разной суммой
        if k in rows:
            continue
        rows[k] = [r["acc"], r["from"], r["to"], s, r["sent"]]
        amt = f"{r['sum']:,.2f}".replace(",", " ").replace(".", ",")
        new.append(f"Кэшбэк {r['acc']} за {r['from'][8:]}.{r['from'][5:7]}–{r['to'][8:]}.{r['to'][5:7]}: {amt} ₽")
    if new and not dry:
        body = "".join("| " + " | ".join(c) + " |\n" for c in sorted(rows.values(), key=lambda c: c[1]))
        tmp = OUT + ".tmp"
        open(tmp, "w", encoding="utf-8").write(HEAD + body)
        os.replace(tmp, OUT)
    return new


if __name__ == "__main__":
    print("\n".join(update(dry="print" in sys.argv)) or "новых писем о кэшбэке нет")
