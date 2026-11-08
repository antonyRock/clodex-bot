#!/usr/bin/env python3
"""Задать логин и пароль входа на сайт. Пароль хранится только как хэш PBKDF2-SHA256.
Запуск: python3 smenit_parol.py [--login __LOGIN__] [--stdin]
--stdin: пароль одной строкой из входного потока (без вывода на экран)."""
import getpass, hashlib, json, os, secrets, sys

AUTH_FILE = "/root/brain-auth/auth.json"
ITER = 300_000

login = "__LOGIN__"
if "--login" in sys.argv:
    login = sys.argv[sys.argv.index("--login") + 1]
if "--stdin" in sys.argv:
    pw = sys.stdin.readline().rstrip("\n")
else:
    pw = getpass.getpass("Новый пароль: ")
    if pw != getpass.getpass("Ещё раз: "):
        sys.exit("Пароли не совпадают")
if len(pw) < 12:
    sys.exit("Пароль короче 12 символов")
salt = secrets.token_bytes(16)
h = hashlib.pbkdf2_hmac("sha256", pw.encode(), salt, ITER)
fd = os.open(AUTH_FILE, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
with os.fdopen(fd, "w") as f:
    json.dump({"login": login.lower(), "salt": salt.hex(), "iter": ITER, "hash": h.hex()}, f)
# после смены пароля все прежние входы недействительны
sfd = os.open("/root/brain-auth/sessions.json", os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
with os.fdopen(sfd, "w") as f:
    f.write("{}")
print("Пароль сохранён, все входы сброшены — войдите заново")
