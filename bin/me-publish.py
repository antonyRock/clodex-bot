#!/usr/bin/env python3
"""Кладёт свежую версию личного пульта (Clodex.__ИМЯ__ vN.html с наибольшим N) в /var/www/me/index.html."""
import pathlib, re, shutil, os
SRC = pathlib.Path("__WORKDIR__/__ПАПКА_АГЕНТА__/20_Личный пульт")
DST = pathlib.Path("/var/www/me/index.html")
files = [(int(m.group(1)), p) for p in SRC.glob("Clodex.__ИМЯ__ v*.html") if (m := re.fullmatch(r"Clodex\.__ИМЯ__ v(\d+)\.html", p.name))]
if files:
    _, src = max(files)
    tmp = DST.with_suffix(".tmp")
    shutil.copyfile(src, tmp); os.chmod(tmp, 0o644); tmp.replace(DST)
    print("published", src.name)
