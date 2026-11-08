#!/usr/bin/env python3
"""Пересборка пультов после ответа агента в боте.

Агенту в боте запускать программы нельзя (нет Bash), поэтому пульты пересобирает сам бот:
до задачи запоминает файлы базы задач, после — сравнивает; изменились → проверка задач
(она же пересобирает личный пульт), затем сборки рабочих пультов, затем публикация на сайт.
"""
import hashlib, json, os, subprocess, sys

WORKDIR = "__WORKDIR__"
WATCH = [os.path.join(WORKDIR, "01_Главный контекст", "05_Задачи"),
         os.path.join(WORKDIR, "03_Проекты", "00_Реестр проектов.md")]
CHECK = os.path.join(WORKDIR, "04_Системные файлы", "__ФАЙЛ_ПРОВЕРКИ_ЗАДАЧ__")
BUILDS = [os.path.join(WORKDIR, p) for p in json.loads('__СБОРКИ_ПУЛЬТОВ__')]   # по порядку
PY = "/usr/bin/python3"   # как у сводки и сторожа
PUBLISH = "/usr/local/bin/work-publish.py"


def snapshot():
    """Хэши файлов базы задач и реестра проектов: путь → sha256."""
    out = {}
    for w in WATCH:
        files = [w] if os.path.isfile(w) else [os.path.join(w, f) for f in sorted(os.listdir(w)) if f.endswith(".md")] if os.path.isdir(w) else []
        for f in files:
            try:
                out[f] = hashlib.sha256(open(f, "rb").read()).hexdigest()
            except OSError:
                pass
    return out


def run(cmd, timeout=120):
    p = subprocess.run(cmd, capture_output=True, text=True, timeout=timeout, cwd=os.path.dirname(cmd[-1]) if cmd[-1].endswith(".py") else None)
    return p.returncode, (p.stdout + p.stderr).strip()


def rebuild():
    """Проверка задач + все пульты. Возвращает (ok, строки отчёта)."""
    report, ok = [], True
    code, out = run([PY, CHECK])
    errors = [l for l in out.splitlines() if l.startswith("ОШИБКА")]
    if code != 0 or errors:
        ok = False
        report.append("⚠️ Проверка задач: " + (f"ошибок {len(errors)}" if errors else f"сбой ({out[-200:]})"))
        report += ["   " + e.replace("ОШИБКА", "").strip()[:150] for e in errors[:3]]
    else:
        report.append("✅ Личный пульт пересобирается")
    for b in BUILDS:
        code, out = run([PY, b])
        if code != 0:
            ok = False
            report.append(f"❌ {os.path.basename(b)}: {out[-200:]}")
    if BUILDS and ok:
        report.append("✅ Рабочие пульты пересобраны")
    if os.path.exists(PUBLISH):
        code, out = run([PY, PUBLISH])
        if code != 0:
            ok = False
            report.append(f"❌ Публикация рабочего пульта: {out[-200:]}")
    return ok, report


if __name__ == "__main__":   # ручной запуск на сервере: python3 pults.py
    print("\n".join(rebuild()[1]))
