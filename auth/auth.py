#!/usr/bin/env python3
"""Сервис входа на сайт: /login, /auth/login, /auth/check, /auth/logout. Слушает 127.0.0.1:8090."""
import hashlib, hmac, html, json, os, secrets, threading, time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from http.cookies import SimpleCookie
from urllib.parse import parse_qs, urlparse

BASE = "/root/brain-auth"
AUTH_FILE = f"{BASE}/auth.json"
SESS_FILE = f"{BASE}/sessions.json"
COOKIE = "brain_session"
TTL = 7 * 24 * 3600
MAX_FAILS, WINDOW = 8, 15 * 60
lock = threading.Lock()
fails = {}


def load(path, default):
    try:
        with open(path) as f:
            return json.load(f)
    except (FileNotFoundError, json.JSONDecodeError):
        return default


def save(path, data):
    tmp = path + ".tmp"
    fd = os.open(tmp, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
    with os.fdopen(fd, "w") as f:
        json.dump(data, f)
    os.replace(tmp, path)


def check_password(login, password):
    a = load(AUTH_FILE, {})
    if not a or not hmac.compare_digest(login.strip().lower(), a.get("login", "")):
        return False
    h = hashlib.pbkdf2_hmac("sha256", password.encode(), bytes.fromhex(a["salt"]), a["iter"])
    return hmac.compare_digest(h.hex(), a["hash"])


def session_valid(token):
    if not token:
        return False
    with lock:
        s = load(SESS_FILE, {})
        exp = s.get(hashlib.sha256(token.encode()).hexdigest())
        return bool(exp and exp > time.time())


def safe_next(n):
    if not n or not n.startswith("/") or n.startswith("//") or "\\" in n:
        return "/code/"
    return n


PAGE = """<!doctype html><html lang="ru"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<meta name="apple-mobile-web-app-capable" content="yes">
<title>Вход</title><style>
:root{color-scheme:dark}*{box-sizing:border-box}
body{margin:0;min-height:100vh;display:flex;align-items:center;justify-content:center;
background:#16181d;color:#e8e8ea;font:16px/1.4 -apple-system,system-ui,sans-serif;padding:16px}
form{width:100%;max-width:360px;background:#1f2229;border:1px solid #2c3039;border-radius:14px;padding:28px}
h1{font-size:22px;margin:0 0 20px}label{display:block;font-size:14px;color:#a4a8b3;margin:14px 0 6px}
input{width:100%;font-size:16px;padding:12px;border-radius:9px;border:1px solid #3a3f4b;background:#16181d;color:#fff}
button{margin-top:22px;width:100%;font-size:16px;padding:13px;border:0;border-radius:9px;background:#ffdd2d;color:#111;font-weight:600}
.err{color:#ff7b72;margin-top:14px;font-size:14px}
</style></head><body><form method="post" action="/api/auth/login">
<h1>Вход</h1>
<input type="hidden" name="next" value="{next}">
<label for="l">Логин</label><input id="l" name="login" autocomplete="username" autocapitalize="none" required>
<label for="p">Пароль</label><input id="p" name="password" type="password" autocomplete="current-password" required>
<button type="submit">Войти</button>{err}
</form></body></html>"""


class H(BaseHTTPRequestHandler):
    def log_message(self, *a):
        pass

    def ip(self):
        return self.headers.get("X-Real-IP") or self.client_address[0]

    def token(self):
        c = SimpleCookie(self.headers.get("Cookie", ""))
        return c[COOKIE].value if COOKIE in c else ""

    def send(self, code, body=b"", headers=()):
        self.send_response(code)
        for k, v in headers:
            self.send_header(k, v)
        if body:
            self.send_header("Content-Type", "text/html; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "no-store")
        self.end_headers()
        if body:
            self.wfile.write(body)

    def login_page(self, nxt, err=""):
        e = f'<div class="err">{html.escape(err)}</div>' if err else ""
        return PAGE.replace("{next}", html.escape(safe_next(nxt))).replace("{err}", e).encode()

    def do_GET(self):
        u = urlparse(self.path)
        if u.path == "/auth/check":
            return self.send(204 if session_valid(self.token()) else 401)
        if u.path == "/login":
            nxt = parse_qs(u.query).get("next", ["/code/"])[0]
            if session_valid(self.token()):
                return self.send(302, headers=[("Location", safe_next(nxt))])
            return self.send(200, self.login_page(nxt))
        self.send(404)

    def do_POST(self):
        u = urlparse(self.path)
        n = int(self.headers.get("Content-Length") or 0)
        form = parse_qs(self.rfile.read(min(n, 10000)).decode(errors="replace"))
        if u.path == "/auth/logout":
            t = self.token()
            with lock:
                s = load(SESS_FILE, {})
                s.pop(hashlib.sha256(t.encode()).hexdigest(), None)
                save(SESS_FILE, s)
            return self.send(302, headers=[("Location", "/login"),
                ("Set-Cookie", f"{COOKIE}=; HttpOnly; Secure; SameSite=Lax; Path=/; Max-Age=0")])
        if u.path != "/auth/login":
            return self.send(404)
        ip, now = self.ip(), time.time()
        nxt = form.get("next", ["/code/"])[0]
        with lock:
            fails[ip] = [t for t in fails.get(ip, []) if now - t < WINDOW]
            if len(fails[ip]) >= MAX_FAILS:
                return self.send(429, self.login_page(nxt, "Слишком много попыток. Подождите 15 минут."))
        if not check_password(form.get("login", [""])[0], form.get("password", [""])[0]):
            with lock:
                fails[ip].append(now)
            return self.send(401, self.login_page(nxt, "Неверный логин или пароль."))
        t = secrets.token_urlsafe(32)
        with lock:
            fails.pop(ip, None)
            s = {k: v for k, v in load(SESS_FILE, {}).items() if v > now}
            s[hashlib.sha256(t.encode()).hexdigest()] = now + TTL
            save(SESS_FILE, s)
        self.send(302, headers=[("Location", safe_next(nxt)),
            ("Set-Cookie", f"{COOKIE}={t}; HttpOnly; Secure; SameSite=Lax; Path=/; Max-Age={TTL}")])


if __name__ == "__main__":
    ThreadingHTTPServer(("127.0.0.1", 8090), H).serve_forever()
