#!/usr/bin/env python3
"""Подставить личные настройки в код из репозитория.

render.py <settings.json> <папка-результат>
Берёт файлы репозитория, заменяет метки __…__ значениями из settings.json
и складывает в папку-результат с той же структурой. Если в коде осталась
метка без значения — останавливается и ничего не выкладывает.
"""
import json, os, re, shutil, sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
PARTS = ["bot", "auth", "night", "bin", "nginx", "systemd", "clodex-mcp"]
MARK = re.compile(r"__[A-ZА-ЯЁ0-9_]+__")


def main():
    if len(sys.argv) != 3:
        sys.exit(__doc__)
    values = json.load(open(sys.argv[1], encoding="utf-8"))
    out = sys.argv[2]
    left = {}
    for part in PARTS:
        for dp, _, files in os.walk(os.path.join(ROOT, part)):
            for f in files:
                if f.endswith(".pyc"):
                    continue
                src = os.path.join(dp, f)
                text = open(src, encoding="utf-8").read()
                text = MARK.sub(lambda m: values.get(m.group(0), m.group(0)), text)
                rest = set(MARK.findall(text))
                if rest:
                    left[os.path.relpath(src, ROOT)] = sorted(rest)
                dst = os.path.join(out, os.path.relpath(src, ROOT))
                os.makedirs(os.path.dirname(dst), exist_ok=True)
                open(dst, "w", encoding="utf-8").write(text)
                shutil.copymode(src, dst)
    if left:
        for f, marks in left.items():
            print(f"нет значения: {f}: {', '.join(marks)}", file=sys.stderr)
        sys.exit(1)


if __name__ == "__main__":
    main()
