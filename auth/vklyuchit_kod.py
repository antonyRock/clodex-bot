#!/usr/bin/env python3
"""Включить второй фактор входа на сайт: код из приложения-аутентификатора (Яндекс Ключ, Google Authenticator).
Запускает сам владелец в своём терминале: секрет и запасные коды видит только он, в журналы и чат не попадают.
Запуск: python3 vklyuchit_kod.py           — включить (или заменить ключ)
        python3 vklyuchit_kod.py --off     — выключить второй фактор
После запуска все прежние входы сброшены."""
import base64, hashlib, json, os, secrets, sys, urllib.parse

BASE = "/root/brain-auth"
TOTP_FILE = f"{BASE}/totp.json"
SESS_FILE = f"{BASE}/sessions.json"


def write(path, text):
    fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
    with os.fdopen(fd, "w") as f:
        f.write(text)


if "--off" in sys.argv:
    if os.path.exists(TOTP_FILE):
        os.remove(TOTP_FILE)
    write(SESS_FILE, "{}")
    sys.exit("Второй фактор выключен, все входы сброшены")

login = json.load(open(f"{BASE}/auth.json")).get("login", "user")
secret = base64.b32encode(secrets.token_bytes(20)).decode().rstrip("=")
recovery = ["%s-%s" % (secrets.token_hex(2), secrets.token_hex(2)) for _ in range(8)]
write(TOTP_FILE, json.dumps({"secret": secret,
                             "recovery": [hashlib.sha256(c.replace("-", "").encode()).hexdigest() for c in recovery]}))
write(SESS_FILE, "{}")
uri = "otpauth://totp/%s?secret=%s&issuer=%s" % (urllib.parse.quote("Clodex:" + login), secret, "Clodex")
print("\nДобавьте ключ в приложение-аутентификатор: «Добавить» → «Ввести вручную»")
print("  Название: Clodex")
print("  Ключ:     " + " ".join(secret[i:i + 4] for i in range(0, len(secret), 4)))
try:
    import qrcode  # если библиотека есть — QR прямо в терминале
    q = qrcode.QRCode(border=1); q.add_data(uri); q.print_ascii(invert=True)
except ImportError:
    pass
print("\nЗапасные коды (каждый — один раз, если нет телефона). Сохраните вне сервера и этой папки:")
for c in recovery:
    print("  " + c)
print("\nВсе входы сброшены. Войдите заново: логин, пароль и 6 цифр из приложения.")
