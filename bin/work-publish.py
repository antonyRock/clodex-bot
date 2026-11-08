#!/usr/bin/env python3
"""Кладёт рабочие пульты на сайт: свежий Clodex.Work и свежие пульты проектов → /var/www/work/ (та же раскладка папок,
чтобы ссылки между пультами работали); /work/ открывает свежий Clodex.Work. Документы проектов не копируются — только пульты."""
import os, pathlib, re, shutil, urllib.parse
SRC = pathlib.Path("__WORKDIR__/__ПАПКА_РАБОЧЕГО_ПУЛЬТА__").parent   # папка рабочих проектов
DST = pathlib.Path("/var/www/work")
NAME = re.compile(r"(Clodex\.Work|Пульт .+) v(\d+)\.html")

latest = {}   # (папка, имя) → (версия, файл)
for p in list(SRC.glob("*/*.html")):
    m = NAME.fullmatch(p.name)
    if m:
        key = (p.parent.name, m.group(1))
        if int(m.group(2)) > latest.get(key, (0, None))[0]:
            latest[key] = (int(m.group(2)), p)

keep, home = set(), None
for (folder, name), (_, p) in latest.items():
    out = DST / folder / p.name
    out.parent.mkdir(parents=True, exist_ok=True)
    if not out.exists() or out.read_bytes() != p.read_bytes():
        tmp = out.with_suffix(".tmp"); shutil.copyfile(p, tmp); os.chmod(tmp, 0o644); tmp.replace(out)
    keep.add(out)
    if name == "Clodex.Work":
        home = f"{folder}/{p.name}"
for f in DST.glob("*/*.html"):   # старые версии убираем, чтобы по сайту не ходили устаревшие пульты
    if f not in keep:
        f.unlink()
if home:
    url = urllib.parse.quote(home)
    (DST / "index.html").write_text(f'<!doctype html><meta charset="utf-8"><meta http-equiv="refresh" content="0;url={url}">'
                                    f'<a href="{url}">Clodex.Work</a>', encoding="utf-8")
    os.chmod(DST / "index.html", 0o644)
print("published", len(keep), "пультов; главная:", home)
