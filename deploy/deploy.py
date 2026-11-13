#!/usr/bin/env python3
"""Выложить код из репозитория на сервер.

deploy.py            — забрать свежий код с GitHub, подставить настройки, разложить по местам
deploy.py --check    — только показать, что изменится (ничего не трогает)
deploy.py --no-pull  — без git pull (выложить то, что сейчас в папке репозитория)
deploy.py --force    — выложить, даже если файл на сервере правили в обход репозитория

Настройки (имя, почта, пути) — в /root/.config/agent-settings.json, не в репозитории.
Перед заменой старые файлы копируются в /root/agent-backup/<время>/.
Откат: git -C <репозиторий> checkout <коммит> && deploy.py --no-pull, затем git checkout main.
"""
import hashlib, json, os, shutil, subprocess, sys, tempfile, time

REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
SETTINGS = os.environ.get("AGENT_SETTINGS", "/root/.config/agent-settings.json")
BACKUP = "/root/agent-backup"
MANIFEST = "/root/.config/agent-deploy.json"   # что выложено в прошлый раз: путь → хэш
TARGETS = {                                      # папка репозитория → место на сервере
    "bot": "/root/tg-bot",
    "auth": "/root/brain-auth",
    "night": "/root/night",
    "bin": "/usr/local/bin",
    "clodex-mcp": "/root/clodex-mcp",
    "nginx": "/etc/nginx/sites-available",
    "systemd": "/etc/systemd/system",
}
RENAME = {"nginx/brain.conf": "brain"}


def h(path):
    return hashlib.sha256(open(path, "rb").read()).hexdigest() if os.path.exists(path) else None


def run(*cmd):
    print("$", " ".join(cmd))
    subprocess.run(cmd, check=True)


def setup_mcp(reinstall):
    """MCP Clodex: окружение Python и запуск службы. Служба стартует только когда есть файл ключей —
    его создаёт человек (GitHub OAuth App и секреты), код этого не делает."""
    venv = "/root/clodex-mcp/venv"
    if not os.path.exists(venv + "/bin/python"):
        run(sys.executable, "-m", "venv", venv)
        reinstall = True
    if reinstall:
        run(venv + "/bin/pip", "install", "--quiet", "-r", "/root/clodex-mcp/requirements.txt")
    if os.path.exists("/root/.config/clodex-mcp.env"):
        run("systemctl", "enable", "clodex-mcp")
        run("systemctl", "restart", "clodex-mcp")
    else:
        print("MCP: нет /root/.config/clodex-mcp.env — служба clodex-mcp не запущена (ждёт ключей).")


def main():
    args = set(sys.argv[1:])
    if "--no-pull" not in args and "--check" not in args:
        me = os.path.abspath(__file__)
        before = h(me)
        run("git", "-C", REPO, "pull", "--ff-only")
        if h(me) != before:   # pull обновил сам deploy.py — дальше выкладывает новая версия, иначе её новые шаги пропустятся
            print("deploy.py обновился — продолжаю новой версией")
            os.execv(sys.executable, [sys.executable, me, "--no-pull"] + sys.argv[1:])
    commit = subprocess.run(["git", "-C", REPO, "rev-parse", "--short", "HEAD"],
                            capture_output=True, text=True).stdout.strip()
    out = tempfile.mkdtemp(prefix="agent-render-")
    run(sys.executable, os.path.join(REPO, "deploy", "render.py"), SETTINGS, out)

    old = json.load(open(MANIFEST)) if os.path.exists(MANIFEST) else {}
    changes, drift = [], []
    for part, target in TARGETS.items():
        for dp, _, files in os.walk(os.path.join(out, part)):
            for f in files:
                rel = os.path.relpath(os.path.join(dp, f), out)
                inner = RENAME.get(rel, os.path.relpath(rel, part))
                dst = os.path.join(target, inner)
                new_h, cur_h = h(os.path.join(out, rel)), h(dst)
                if new_h == cur_h:
                    continue
                if dst in old and cur_h is not None and cur_h != old[dst]:
                    drift.append(dst)
                changes.append((rel, dst))

    if not changes:
        print(f"Нечего выкладывать: на сервере уже {commit}.")
        return
    print(f"Изменится файлов: {len(changes)} (коммит {commit})")
    for rel, dst in changes:
        print("  ", dst)
    if drift and "--force" not in args:
        print("\nСТОП: эти файлы правили на сервере в обход репозитория — перенесите правку в репозиторий:")
        for d in drift:
            print("  ", d)
        sys.exit(2)
    if "--check" in args:
        return

    bk = os.path.join(BACKUP, time.strftime("%Y-%m-%d_%H%M%S"))
    for rel, dst in changes:
        if os.path.exists(dst):
            os.makedirs(os.path.dirname(bk + dst), exist_ok=True)
            shutil.copy2(dst, bk + dst)
        os.makedirs(os.path.dirname(dst), exist_ok=True)
        shutil.copy2(os.path.join(out, rel), dst)
        old[dst] = h(dst)
    os.makedirs(os.path.dirname(MANIFEST), exist_ok=True)
    json.dump(old, open(MANIFEST, "w"), indent=1)
    print("Старые версии:", bk)

    parts = {rel.split("/")[0] for rel, _ in changes}
    if "systemd" in parts:
        run("systemctl", "daemon-reload")
    if "nginx" in parts:
        run("nginx", "-t")
        run("systemctl", "reload", "nginx")
    if "auth" in parts:
        run("systemctl", "restart", "brain-auth")
    if "clodex-mcp" in parts or any(rel.endswith("clodex-mcp.service") for rel, _ in changes):
        setup_mcp(any(rel.startswith("clodex-mcp/requirements") for rel, _ in changes))
    if any(rel.endswith(("health_ingest.py", "health-ingest.service")) for rel, _ in changes):
        run("systemctl", "enable", "health-ingest")
        run("systemctl", "restart", "health-ingest")
    if any(rel.endswith(("vpn_stats.py", "vpn-stats.timer", "vpn-stats.service")) for rel, _ in changes) \
            and os.path.exists("/usr/local/bin/xray"):
        run("systemctl", "enable", "--now", "vpn-stats.timer")   # статистика VPN — только если стоит Xray
    if "bot" in parts or "systemd" in parts:
        run("systemctl", "restart", "tg-bot")   # остальные скрипты запускаются таймерами — подхватят сами
    time.sleep(3)
    state = subprocess.run(["systemctl", "is-active", "tg-bot"], capture_output=True, text=True).stdout.strip()
    print(f"Готово: {commit}. tg-bot — {state}.")
    if state != "active":
        sys.exit(1)


if __name__ == "__main__":
    main()
