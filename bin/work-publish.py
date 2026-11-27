#!/usr/bin/env python3
"""Кладёт рабочие пульты на сайт: свежий Clodex.Work и свежие пульты проектов → /var/www/work/ (та же раскладка папок,
чтобы ссылки между пультами работали); /work/ сам показывает свежий Clodex.Work (без перехода на адрес с версией). Документы проектов не копируются — только пульты."""
import os, pathlib, re, shutil
KEEP_VERSIONS = 20   # сколько последних версий каждого пульта хранить для отката
SRC = pathlib.Path("__WORKDIR__/__ПАПКА_РАБОЧЕГО_ПУЛЬТА__").parent   # папка рабочих проектов
DST = pathlib.Path("/var/www/work")
NAME = re.compile(r"(Clodex\.Work|Пульт .+) v(\d+)\.html")
# Короткие постоянные адреса: /work/<адрес>/ всегда показывает свежую vN этой страницы — адрес не меняется
# с каждой новой версией (просьба владельца, 2026-11-27). Глубина та же, что у папки проекта, поэтому ссылки "../" работают.
ALIASES = {"Пульт Payment Events": "payment-events"}

latest = {}   # (папка, имя) → (версия, файл)
allv = {}     # (папка, имя) → [(версия, файл)] — для архива версий
for p in list(SRC.glob("*/*.html")):
    m = NAME.fullmatch(p.name)
    if m:
        key = (p.parent.name, m.group(1))
        allv.setdefault(key, []).append((int(m.group(2)), p))
        if int(m.group(2)) > latest.get(key, (0, None))[0]:
            latest[key] = (int(m.group(2)), p)

def put(src, out):
    out.parent.mkdir(parents=True, exist_ok=True)
    if not out.exists() or out.read_bytes() != src.read_bytes():
        tmp = out.with_suffix(".tmp"); shutil.copyfile(src, tmp); os.chmod(tmp, 0o644); tmp.replace(out)

keep, home = set(), None
for (folder, name), (_, p) in latest.items():
    out = DST / folder / p.name
    put(p, out)
    keep.add(out)
    if name == "Clodex.Work":
        home = f"{folder}/{p.name}"
    if name in ALIASES:
        out = DST / ALIASES[name] / "index.html"
        put(p, out)
        keep.add(out)
for f in DST.glob("*/*.html"):   # старые версии убираем, чтобы по сайту не ходили устаревшие пульты
    if f not in keep:
        f.unlink()
# Архив версий: последние KEEP_VERSIONS каждого пульта лежат в /work/versions/<папка>/ — для отката
arch = set()
for (folder, name), vs in allv.items():
    for _, p in sorted(vs, key=lambda x: -x[0])[:KEEP_VERSIONS]:
        out = DST / "versions" / folder / p.name
        put(p, out)
        arch.add(out)
for f in (DST / "versions").glob("*/*.html"):
    if f not in arch:
        f.unlink()
if home:
    # /work/ показывает сам свежий Clodex.Work, без перехода на адрес с версией: адрес в строке браузера
    # и у значка на iPhone всегда /work/ (просьба владельца, 2026-11-16). Пульт лежит в папке на уровень ниже,
    # поэтому ссылки "../Папка/…" в копии становятся "Папка/…" — относительно /work/.
    html = (DST / home).read_text(encoding="utf-8").replace('"../', '"')
    tmp = DST / "index.html.tmp"
    tmp.write_text(html, encoding="utf-8"); os.chmod(tmp, 0o644); tmp.replace(DST / "index.html")
print("published", len(keep), "пультов, в архиве", len(arch), "версий; главная:", home)
