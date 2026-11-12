#!/usr/bin/env python3
"""Ежемесячная чистка скриншотов переписки (шаг 18 архитектуры) и отчёт о месте на диске сервера.
Удаляет tg_*.jpg|jpeg|png|webp из 06_Загрузки/ и 06_Загрузки/02_Переписанные скриншоты/, если имя файла есть в журнале переписки
в строке с «переписан» и файлу больше 30 дней. Заодно удаляет их версии Syncthing
в .stversions/06_Загрузки/ (и в подпапке). Сводка — в Telegram.
Запуск: clean_screens.py            — боевой запуск + сводка в Telegram;
        clean_screens.py --dry-run  — только показать, ничего не удалять и не отправлять."""
import glob, json, os, re, shutil, subprocess, sys, time, urllib.parse, urllib.request

WORKDIR = "__WORKDIR__"
UPLOADS = os.path.join(WORKDIR, "06_Загрузки")
SCREENS = "02_Переписанные скриншоты"  # подпапка загрузок для переписанных скриншотов (правило 2026-11-06)
SCAN_DIRS = [UPLOADS, os.path.join(UPLOADS, SCREENS)]
VERSIONS = os.path.join(WORKDIR, ".stversions", "06_Загрузки")
VERSION_DIRS = [VERSIONS, os.path.join(VERSIONS, SCREENS)]
JOURNALS = ["03_Проекты/**/01_Переписка*/*.md", "02_Глубокий контекст/**/*Переписка Китай*/*.md"]
MAX_AGE_DAYS = 30
VOICE_DIR = "/root/tg-bot/voice"      # голосовые: бот удаляет после расшифровки; здесь — остатки неудачных, старше VOICE_MAX_DAYS
VOICE_MAX_DAYS = 7
DISK_STATE = "/root/tg-bot/disk_report.json"   # размеры папок прошлого запуска — для строки «рост за месяц»
DISK_DIRS = ["/root/tg-bot", "/root/.claude", "/root/Claude_кэш", "/root/.local", "/root/.cache", "/root/.codex",
             "/root/.npm", "/var/log", "/root/agent-backup", os.path.join(WORKDIR, ".stversions")]
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


def clean_voice(dry):
    """Удаляет остатки голосовых старше VOICE_MAX_DAYS; возвращает (штук, байт)."""
    n = size = 0
    for path in glob.glob(os.path.join(VOICE_DIR, "*")):
        if os.path.isfile(path) and time.time() - os.path.getmtime(path) > VOICE_MAX_DAYS * 86400:
            size += os.path.getsize(path); n += 1
            if not dry:
                try:
                    os.remove(path)
                except OSError:
                    n -= 1
    return n, size


def folder_mb(path):
    try:
        out = subprocess.run(["du", "-sk", path], capture_output=True, text=True, timeout=300).stdout.split()
        return int(out[0]) / 1024
    except (OSError, ValueError, IndexError, subprocess.TimeoutExpired):
        return None


def disk_report(dry):
    """Строки отчёта: место на диске, папки по размеру и рост с прошлого запуска."""
    total, _, free = shutil.disk_usage("/")
    sizes = {d: folder_mb(d) for d in DISK_DIRS if os.path.exists(d)}
    try:
        prev = json.load(open(DISK_STATE, encoding="utf-8"))
    except (OSError, ValueError):
        prev = {}
    lines = [f"💾 Диск: свободно {free / 2**30:.0f} ГБ из {total / 2**30:.0f} ГБ", "Папки (рост с прошлого запуска):"]
    for d, mb in sorted(((d, m) for d, m in sizes.items() if m is not None), key=lambda x: -x[1]):
        name = d.replace("/root/", "~/").replace(WORKDIR, "Claude_AI")
        grow = f", {mb - prev[d]:+.0f} МБ" if d in prev else ""
        lines.append(f"• {name}: {mb / 1024:.1f} ГБ{grow}" if mb >= 1024 else f"• {name}: {mb:.0f} МБ{grow}")
    if not dry:
        try:
            json.dump({d: m for d, m in sizes.items() if m is not None}, open(DISK_STATE, "w", encoding="utf-8"))
        except OSError:
            pass
    return lines


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
    vn, vsize = clean_voice(dry)
    if vn:
        lines.append(f"🎙 Остатки голосовых старше {VOICE_MAX_DAYS} дн.: {'к удалению' if dry else 'удалено'} {vn} ({vsize / 2**20:.1f} МБ)")
    lines += [""] + disk_report(dry)
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
