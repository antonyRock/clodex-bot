"""MCP Clodex — проверка путей. Всё, что приложение (Claude/ChatGPT) читает и пишет, проходит здесь.

Правила (см. «01_План и ТЗ.md», п. 4):
- читать можно папку Claude_AI и (только читать) кэш; писать — только Claude_AI;
- закрыты: доступы, сканы документов, служебные папки с точкой, файлы, похожие на ключи;
- записывать нельзя корневые правила агента, настройки синхронизации и исполняемые скрипты:
  сервер сам запускает проверку базы и сборки пультов, подменённый скрипт стал бы дырой.
"""
import os
import unicodedata

WORKDIR = os.path.realpath(os.environ.get("CLODEX_WORKDIR") or "__WORKDIR__")
CACHE = os.path.realpath(os.environ.get("CLODEX_CACHE") or "/root/Claude_кэш")
BACKUP = os.path.join(CACHE, "mcp-backup")

BLOCKED_PARTS = {"10_Документы", "06_Доступы"}            # любой уровень вложенности
BLOCKED_NAMES = {"Доступы.md"}
SECRET_HINTS = (".env", ".pem", ".key", "secret", "token", "id_rsa", "password", "пароль")
PROTECTED_ROOT = {"CLAUDE.md", "AGENTS.md", ".машина.md", ".stignore", ".stfolder"}   # писать нельзя
NO_WRITE_EXT = {".py", ".sh", ".bash", ".zsh", ".command", ".service", ".timer", ".plist",
                ".pkl", ".so", ".dylib", ".app", ".exe", ".bat", ".cmd", ".ps1", ".pyc"}
MAX_WRITE = 1_000_000


class GuardError(Exception):
    """Понятное сообщение для модели: что не так и что можно."""


def _inside(path, root):
    return path == root or path.startswith(root + os.sep)


def resolve(p, write=False):
    """Путь от модели (относительный к Claude_AI или абсолютный) → реальный путь, либо GuardError."""
    if not isinstance(p, str) or not p.strip():
        raise GuardError("Пустой путь.")
    if "\x00" in p:
        raise GuardError("Недопустимый символ в пути.")
    p = unicodedata.normalize("NFC", p.strip())
    if ".." in p.replace("\\", "/").split("/"):
        raise GuardError("«..» в пути нельзя: укажите путь от корня Claude_AI.")
    real = os.path.realpath(p if os.path.isabs(p) else os.path.join(WORKDIR, p))
    if _inside(real, WORKDIR):
        root = WORKDIR
    elif not write and _inside(real, CACHE):
        root = CACHE
    elif write and _inside(real, CACHE):
        raise GuardError("Кэш доступен только для чтения.")
    else:
        raise GuardError("Путь вне папки Claude_AI (и кэша): так нельзя.")
    rel = os.path.relpath(real, root)
    if rel == ".":
        return real
    comps = rel.split(os.sep)
    if any(c.startswith(".") for c in comps[:-1]):
        raise GuardError("Служебные папки (с точкой в начале) закрыты.")
    name = comps[-1]
    if BLOCKED_PARTS & set(comps) or name in BLOCKED_NAMES:
        raise GuardError("Эта папка или файл закрыты для приложений (доступы, сканы документов).")
    low = name.lower()
    if any(h in low for h in SECRET_HINTS):
        raise GuardError("Файл похож на ключ или пароль — закрыт для приложений.")
    if write:
        if root == WORKDIR and len(comps) == 1 and name in PROTECTED_ROOT:
            raise GuardError(f"{name} меняется только через агента в VS Code (правила и настройки).")
        if os.path.splitext(low)[1] in NO_WRITE_EXT:
            raise GuardError("Скрипты и службы через приложение не пишутся: их меняет агент в VS Code.")
        if os.path.isdir(real):
            raise GuardError("Это папка, а не файл.")
        top = os.path.join(WORKDIR, comps[0])
        if len(comps) == 1 and not os.path.exists(real):
            raise GuardError("Новые файлы в корне Claude_AI не создаются: разовое — в 06_Загрузки/01_От агента/, "
                             "остальное — внутрь 01_–06_.")
        if len(comps) > 1 and not os.path.isdir(top):
            raise GuardError("Новые папки в корне Claude_AI создавать нельзя (правило структуры): "
                             "раскладывайте внутрь 01_–06_.")
    return real


def rel(path):
    """Путь для показа: от Claude_AI, для кэша — со значком."""
    if _inside(path, WORKDIR):
        return os.path.relpath(path, WORKDIR)
    if _inside(path, CACHE):
        return "[кэш] " + os.path.relpath(path, CACHE)
    return path


def listed(path):
    """Можно ли показывать запись в списках и поиске (без исключений)."""
    try:
        resolve(path)
        return True
    except GuardError:
        return False
