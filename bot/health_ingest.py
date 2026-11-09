#!/usr/bin/env python3
"""Приём данных «Здоровья» от iPhone-приложения Health Auto Export (автоматизация REST API).

Слушает 127.0.0.1:8095, снаружи доступен через nginx: POST https://<домен>/api/health/ingest
с заголовком «Authorization: Bearer <ключ>». Ключ — в /root/.config/health-ingest.token.

Что делает с присланным:
  1) кладёт как есть (сжатым) в /root/health/raw/ — чтобы можно было пересобрать всё заново;
  2) раскладывает показатели по дням в /root/health/daily.json;
  3) пишет таблицу последних 60 дней в досье (файл SUMMARY ниже) — её читает агент.

В приложении нужно «Группировать по: дню» (Aggregate: Day) — тогда на каждый день одна цифра,
и повторная отправка того же дня просто её заменяет.
Пересобрать сводку из сохранённого: health_ingest.py --rebuild
"""
import gzip, hmac, json, os, sys, threading, time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

WORKDIR = os.environ.get("AGENT_WORKDIR") or "__WORKDIR__"
SUMMARY = os.path.join(WORKDIR, "02_Глубокий контекст/09_Быт_и_здоровье/05_Часы и весы — по дням.md")
BASE = "/root/health"
RAW, DAILY = f"{BASE}/raw", f"{BASE}/daily.json"
TOKEN_FILE = "/root/.config/health-ingest.token"
PORT, MAX_BODY = 8095, 64 * 1024 * 1024
lock = threading.Lock()

# имя показателя в приложении → (колонка, как брать значение)
COLS = [
    ("step_count", "Шаги", "qty", 0),
    ("sleep_analysis", "Сон, ч", "totalSleep", 1),
    ("sleep_analysis", "Глубокий, ч", "deep", 1),
    ("sleep_analysis", "REM, ч", "rem", 1),
    ("resting_heart_rate", "Пульс покоя", "qty", 0),
    ("heart_rate_variability", "HRV, мс", "qty", 0),
    ("heart_rate", "Пульс ср.", "Avg", 0),
    ("active_energy", "Актив. ккал", "qty", 0),
    ("apple_exercise_time", "Упражн., мин", "qty", 0),
    ("vo2_max", "VO2max", "qty", 1),
    ("weight_body_mass", "Вес, кг", "qty", 1),
    ("body_fat_percentage", "Жир, %", "qty", 1),
]


def token():
    try:
        return open(TOKEN_FILE).read().strip()
    except FileNotFoundError:
        return ""


def load_daily():
    try:
        return json.load(open(DAILY))
    except (FileNotFoundError, json.JSONDecodeError):
        return {}


def save_json(path, data):
    tmp = path + ".tmp"
    with open(tmp, "w") as f:
        json.dump(data, f, ensure_ascii=False)
    os.replace(tmp, path)


def merge(daily, payload):
    """Разложить один пакет приложения по дням. Возвращает число записанных значений."""
    data = payload.get("data", payload)
    n = 0
    for m in data.get("metrics", []) or []:
        name, units = m.get("name"), m.get("units", "")
        for p in m.get("data", []) or []:
            day = str(p.get("date", ""))[:10]
            if len(day) != 10:
                continue
            vals = {k: v for k, v in p.items() if k not in ("date", "source") and isinstance(v, (int, float))}
            if not vals:
                continue
            if name == "sleep_analysis":                 # когда лёг и встал — строки «2026-11-09 05:55:04 +0300»
                for k in ("sleepStart", "sleepEnd"):
                    if isinstance(p.get(k), str):
                        vals[k] = p[k]
            if units == "kJ" and "qty" in vals:          # энергия — всегда в ккал
                vals["qty"] = vals["qty"] / 4.184
            if units == "%" and vals.get("qty", 100) <= 1:  # доля → проценты
                vals["qty"] *= 100
            daily.setdefault(day, {})[name] = vals
            n += 1
    for w in data.get("workouts", []) or []:
        start = str(w.get("start", ""))
        day = start[:10]
        if len(day) != 10:
            continue
        def num(x):
            return x.get("qty") if isinstance(x, dict) else x
        item = {"name": w.get("name", "?"), "start": start,
                "min": round((num(w.get("duration")) or 0) / 60),
                "kcal": round(num(w.get("activeEnergyBurned")) or num(w.get("activeEnergy")) or 0)}
        wl = daily.setdefault(day, {}).setdefault("_workouts", [])
        wl[:] = [x for x in wl if x.get("start") != start] + [item]
        n += 1
    return n


def fmt(v, digits):
    if v is None:
        return ""
    return f"{v:.{digits}f}".replace(".", ",") if digits else f"{round(v):,}".replace(",", " ")


def write_summary(daily):
    days = sorted(daily)[-60:][::-1]
    head = ["Дата"] + [c[1] for c in COLS] + ["Тренировки", "Лёг", "Встал"]
    lines = [
        "# Часы и весы — по дням",
        "",
        "Карта: [[01_Как организовать]] · Медкарта: [[03_Здоровье и медицина]]",
        "",
        "Собирается из «Здоровья» iPhone: автоотправка (Health Auto Export) или разовые выгрузки. Не править руками — перезапишется.",
        "Источник: Apple Watch и весы PICOOC через «Здоровье». Пустая клетка — показатель не пришёл.",
        f"Обновлено: {time.strftime('%Y-%m-%d %H:%M')}. Всего дней в базе: {len(daily)}; здесь — последние 60.",
        "",
        "| " + " | ".join(head) + " |",
        "|" + "---|" * len(head),
    ]
    for d in days:
        row = [d]
        for name, _, key, digits in COLS:
            row.append(fmt((daily[d].get(name) or {}).get(key), digits))
        ws = daily[d].get("_workouts", [])
        row.append("; ".join(f"{w['name']} {w['min']} мин" for w in ws))
        sl = daily[d].get("sleep_analysis") or {}
        row += [str(sl.get(k) or "")[11:16] for k in ("sleepStart", "sleepEnd")]   # ЧЧ:ММ по времени часов
        lines.append("| " + " | ".join(row) + " |")
    try:                                   # раздел «Заметки» пишет агент — сохранить его
        old = open(SUMMARY).read()
        i = old.find("\n## Заметки")
        tail = old[i:] if i >= 0 else ""
    except FileNotFoundError:
        tail = ""
    os.makedirs(os.path.dirname(SUMMARY), exist_ok=True)
    tmp = SUMMARY + ".tmp"
    open(tmp, "w").write("\n".join(lines) + "\n" + tail)
    os.replace(tmp, SUMMARY)


class H(BaseHTTPRequestHandler):
    def log_message(self, *a):
        pass

    def reply(self, code, obj):
        body = json.dumps(obj, ensure_ascii=False).encode()
        self.send_response(code)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def do_GET(self):
        self.reply(200, {"ok": True}) if self.path == "/ping" else self.reply(404, {"error": "нет"})

    def do_POST(self):
        if self.path != "/ingest":
            return self.reply(404, {"error": "нет"})
        t = token()
        got = self.headers.get("Authorization", "").removeprefix("Bearer ").strip()
        if not t or not hmac.compare_digest(got, t):
            return self.reply(401, {"error": "ключ"})
        size = int(self.headers.get("Content-Length") or 0)
        if not 0 < size <= MAX_BODY:
            return self.reply(413, {"error": "размер"})
        raw = self.rfile.read(size)
        try:
            payload = json.loads(raw)
        except ValueError:
            return self.reply(400, {"error": "не JSON"})
        with lock:
            name = time.strftime("%Y-%m-%d_%H%M%S") + f"_{time.time_ns() % 10**6:06d}.json.gz"
            with gzip.open(os.path.join(RAW, name), "wb") as f:
                f.write(raw)
            daily = load_daily()
            n = merge(daily, payload)
            save_json(DAILY, daily)
            write_summary(daily)
        print(f"{name}: {n} значений", flush=True)
        self.reply(200, {"ok": True, "values": n})


def rebuild():
    daily = {}
    for f in sorted(os.listdir(RAW)):
        with gzip.open(os.path.join(RAW, f)) as g:
            merge(daily, json.load(g))
    save_json(DAILY, daily)
    write_summary(daily)
    print(f"Пересобрано: {len(daily)} дней")


if __name__ == "__main__":
    os.makedirs(RAW, mode=0o700, exist_ok=True)
    if "--rebuild" in sys.argv:
        rebuild()
    else:
        ThreadingHTTPServer(("127.0.0.1", PORT), H).serve_forever()
