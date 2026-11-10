#!/usr/bin/env python3
"""Снимок HTML-страницы пульта в PNG (безголовый Chromium через Playwright) — чтобы бот показал, как выглядит страница.
Только локальные .html внутри папки Claude_AI или в папке черновиков пультов (DRAFTS); сеть страницам не нужна, внешние ресурсы не грузятся.
page_shot.py <файл.html> <результат.png> [--w 1440] [--theme light|dark] [--tab имя] [--to id] [--full] [--h 900]
Размеры: телефон — --w 390, ноутбук — --w 1440. --tab — вкладка пульта (fin, dela, sys, base, health, media).
Выход 0 — файл записан; иначе сообщение в stderr."""
import os, sys, argparse

WORKDIR = os.environ.get("AGENT_WORKDIR") or "__WORKDIR__"
DRAFTS = "/root/Claude_кэш/pult-drafts"
PY_VENV = "/root/pw-venv/bin/python"


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("html"); ap.add_argument("out")
    ap.add_argument("--w", type=int, default=1440); ap.add_argument("--h", type=int, default=900)
    ap.add_argument("--theme", default="light", choices=["light", "dark"])
    ap.add_argument("--tab", default=""); ap.add_argument("--to", default=""); ap.add_argument("--full", action="store_true")
    a = ap.parse_args()
    src = os.path.realpath(a.html)
    roots = [os.path.realpath(WORKDIR) + os.sep, os.path.realpath(DRAFTS) + os.sep]
    if not any(src.startswith(r) for r in roots) or not src.endswith(".html") or not os.path.isfile(src):
        sys.exit("нужен существующий .html внутри папки Claude_AI или черновиков пультов")
    out = os.path.realpath(a.out)
    if not out.endswith(".png"):
        sys.exit("результат — .png")
    from playwright.sync_api import sync_playwright
    with sync_playwright() as p:
        b = p.chromium.launch()
        ctx = b.new_context(viewport={"width": a.w, "height": a.h}, device_scale_factor=2 if a.w < 600 else 1,
                            color_scheme=a.theme, java_script_enabled=True)
        ctx.route("**/*", lambda r: r.continue_() if r.request.url.startswith("file://") else r.abort())   # без сети
        pg = ctx.new_page()
        pg.goto("file://" + src + (f"#{a.tab}" if a.tab else ""))
        pg.evaluate("t => { try { localStorage.setItem('clodex-theme', t) } catch(e) {} document.documentElement.setAttribute('data-theme', t) }", a.theme)
        if a.tab:
            pg.evaluate("t => { const b = document.querySelector('.tabbar .tb[data-tab=\"' + t + '\"]'); if (b) b.click() }", a.tab)
        pg.wait_for_timeout(800)
        if a.to:
            pg.evaluate("i => { const e = document.getElementById(i); if (e) e.scrollIntoView({block: 'start'}) }", a.to)
            pg.wait_for_timeout(300)
        pg.screenshot(path=out, full_page=a.full)
        b.close()


if __name__ == "__main__":
    if sys.executable != PY_VENV and os.path.exists(PY_VENV):
        os.execv(PY_VENV, [PY_VENV] + sys.argv)   # Playwright стоит в своём окружении
    main()
