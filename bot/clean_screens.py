#!/usr/bin/env python3
"""Ежемесячная чистка скриншотов переписки (шаг 18 архитектуры).
Удаляет tg_*.jpg|jpeg|png|webp из 06_Загрузки/ и 06_Загрузки/02_Переписанные скриншоты/, если имя файла есть в журнале переписки
в строке с «переписан» и файлу больше 30 дней. Заодно удаляет их версии Syncthing
в .stversions/06_Загрузки/ (и в подпапке). Сводка — в Telegram.
Запуск: clean_screens.py            — боевой запуск + сводка в Telegram;
        clean_screens.py --dry-run  — только показать, ничего не удалять и не отправлять."""
import glob, os, re, sys, time, urllib.parse, urllib.request

WORKDIR = "__WORKDIR__"
UPLOADS = os.path.join(WORKDIR, "06_Загрузки")
SCREENS = "02_Переписанные скриншоты"  # подпапка загрузок для переписанных скриншотов (правило 2026-11-06)
SCAN_DIRS = [UPLOADS, os.path.join(UPLOADS, SCREENS)]
VERSIONS = os.path.join(WORKDIR, ".stversions", "06_Загрузки")
VERSION_DIRS = [VERSIONS, os.path.join(VERSIONS, SCREENS)]
JOURNALS = ["03_Проекты/**/01_Переписка*/*.md", "02_Глубокий контекст/**/*Переписка Китай*/*.md"]
MAX_AGE_DAYS = 30
NAME_RE = re.compile(r"^tg_.+\.(jpe?g|png|webp)$", re.I)


def marked_names():
    """Имена файлов, упомянутые в журналах переписки в строке с «переписан»."""
    names = set()
    for pat in JOURNALS:
        for path in glob.glob(os.path.join(WORKDIR, pat), recursive=True):
            try:
                text = open(path, encoding="utf-8").read()
            except OSError:
                continue
            for line in text.splitlines():
                if "переписан" in line.lower():
                    names.update(m.group(0) for m in re.finditer(r"tg_[\w.-]+?\.(?:jpe?g|png|webp)", line, re.I))
    return names


def version_copies(name):
    """Версии Syncthing: имя~ГГГГММДД-ЧЧММСС.ext."""
    stem, ext = os.path.splitext(name)
    found = []
    for d in VERSION_DIRS:
        found += glob.glob(os.path.join(d, glob.escape(stem) + "~*" + glob.escape(ext)))
    return found


def send(text):
    token = os.environ["TG_TOKEN"]
    chat = os.environ.get("TG_ALLOWED", "").split(",")[0]
    data = urllib.parse.urlencode({"chat_id": chat, "text": text}).encode()
    urllib.request.urlopen(f"https://api.telegram.org/bot{token}/sendMessage", data, timeout=30)


def main():
    dry = "--dry-run" in sys.argv
    marked = marked_names()
    now = time.time()
    removed, removed_bytes, young, unmarked, errors = [], 0, [], [], []

    files = [(n, os.path.join(d, n)) for d in SCAN_DIRS if os.path.isdir(d) for n in os.listdir(d)]
    for name, path in sorted(files):
        if not os.path.isfile(path) or not NAME_RE.match(name):
            continue
        if name not in marked:
            unmarked.append(name)
            continue
        age = (now - os.path.getmtime(path)) / 86400
        if age < MAX_AGE_DAYS:
            young.append(f"{name} ({age:.0f} дн.)")
            continue
        targets = [path] + version_copies(name)
        for t in targets:
            size = os.path.getsize(t)
            if not dry:
                try:
                    os.remove(t)
                except OSError as e:
                    errors.append(f"{os.path.basename(t)}: {e}")
                    continue
            removed.append(os.path.relpath(t, WORKDIR))
            removed_bytes += size

    mb = removed_bytes / 2**20
    lines = [
        "🧹 Чистка скриншотов переписки" + (" (пробный запуск)" if dry else ""),
        f"Удалено: {len(removed)} файл(ов), {mb:.1f} МБ" if not dry else f"К удалению: {len(removed)} файл(ов), {mb:.1f} МБ",
        f"Переписаны, но моложе {MAX_AGE_DAYS} дн. (ждут): {len(young)}",
        f"Без пометки «переписан» (не разобраны): {len(unmarked)}",
    ]
    if errors:
        lines.append("⚠️ Ошибки: " + "; ".join(errors[:5]))
    if dry:
        lines += ["", "К удалению:"] + (removed or ["—"])
        lines += ["Ждут:"] + (young or ["—"])
        lines += ["Без пометки:"] + (unmarked or ["—"])
        print("\n".join(lines))
    else:
        if unmarked:
            lines.append("Например: " + ", ".join(unmarked[:3]))
        send("\n".join(lines))


if __name__ == "__main__":
    main()
