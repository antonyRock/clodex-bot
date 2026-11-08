#!/usr/bin/env python3
"""Напоминание прислать выписки банка.
Зарплата приходит в дни из таблицы «Регулярные поступления» календаря платежей. Через 2 дня после
зарплаты (запас на выходные), вечером, бот просит прислать свежие выписки — но только если в финансовой базе
ещё нет данных после этой зарплаты (журнал загрузок, личный счёт). Не прислал — напомнит снова на следующий
день, всего не больше 3 раз на одну зарплату. Без ИИ.
Запуск: statement_nudge.py — проверить и при необходимости отправить; statement_nudge.py print — только показать."""
import json, os, re, sys, calendar
from datetime import date, timedelta

sys.path.insert(0, "/root/tg-bot")
from brief import WORKDIR, send  # noqa: E402

FIN = os.path.join(WORKDIR, "02_Глубокий контекст", "07_Деньги_и_бизнес", "02_Финансы")
JOURNAL = os.path.join(FIN, "05_Журнал загрузок.md")
CAL = os.path.join(FIN, "04_Календарь платежей.md")
STATE = "/root/tg-bot/statement_nudge_state.json"   # {дата зарплаты: сколько раз напомнили}
PERSONAL = "__НОМЕР_ДОГОВОРА_СЧЁТА__"
LAG_DAYS, MAX_TIMES, GIVE_UP_DAYS = 2, 3, 10


def salary_days():
    """Дни зарплаты из календаря: строки «Зарплата — 1-я/2-я часть»."""
    days = []
    for line in open(CAL, encoding="utf-8"):
        c = [x.strip() for x in line.strip().strip("|").split("|")]
        if len(c) > 1 and c[0].startswith("Зарплата") and "часть" in c[0] and c[1].isdigit():
            days.append(int(c[1]))
    return sorted(days) or [3, 17]


def last_salary(today):
    """Последняя дата зарплаты, после которой прошло не меньше LAG_DAYS."""
    cands = []
    for back in (0, 1):
        y, m = (today.year, today.month - back) if today.month - back >= 1 else (today.year - 1, 12)
        for d in salary_days():
            dt = date(y, m, min(d, calendar.monthrange(y, m)[1]))
            if dt + timedelta(days=LAG_DAYS) <= today:
                cands.append(dt)
    return max(cands) if cands else None


def covered_until():
    """До какой даты загружены выписки личного счёта (журнал загрузок)."""
    best = None
    for line in open(JOURNAL, encoding="utf-8"):
        c = [x.strip() for x in line.strip().strip("|").split("|")]
        if len(c) > 2 and c[1] == PERSONAL:
            m = re.search(r"(\d{4}-\d{2}-\d{2})\s*$", c[2])
            if m and (best is None or m.group(1) > best):
                best = m.group(1)
    return date.fromisoformat(best) if best else None


def build(today):
    s = last_salary(today)
    cov = covered_until()
    if not s or (cov and cov >= s) or (today - s).days > GIVE_UP_DAYS:
        return None, s
    since = (cov + timedelta(days=1)).strftime("%d.%m") if cov else "начала месяца"
    text = (f"Пора обновить финансы: зарплата пришла {s.strftime('%d.%m')}, а выписки загружены только по "
            f"{cov.strftime('%d.%m') if cov else '—'}.\n\n"
            f"Пришли сюда PDF-выписки Т-Банка с {since} по сегодня:\n"
            "• личный счёт\n• совместный\n• кредитка\n• накопительный\n\n"
            "В приложении: счёт → «Выписка» (справка о движении средств) → PDF → поделиться в этот чат. "
            "Бот положит файлы в загрузки, потом скажи агенту «разбери загрузки» — пульт обновится.")
    return text, s


if __name__ == "__main__":
    today = date.today()
    text, s = build(today)
    if len(sys.argv) > 1 and sys.argv[1] == "print":
        print(text or f"не нужно: последняя зарплата {s}, выписки до {covered_until()}")
        sys.exit()
    if not text:
        print("не нужно"); sys.exit()
    try:
        st = json.load(open(STATE, encoding="utf-8"))
    except Exception:
        st = {}
    k = s.isoformat()
    if st.get(k, {}).get("n", 0) >= MAX_TIMES or st.get(k, {}).get("last") == today.isoformat():
        print("уже напоминали"); sys.exit()
    send(text)
    st[k] = {"n": st.get(k, {}).get("n", 0) + 1, "last": today.isoformat()}
    json.dump(st, open(STATE, "w"), ensure_ascii=False)
    print("отправлено", k, st[k])
