#!/usr/bin/env python3
"""Сервис входа на сайт: /login, /auth/login, /auth/check, /auth/logout. Слушает 127.0.0.1:8090.
Второй фактор (TOTP, код из приложения-аутентификатора) включается файлом totp.json — его создаёт vklyuchit_kod.py.
Нет файла — вход только по паролю."""
import base64, hashlib, hmac, html, json, os, secrets, struct, threading, time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from http.cookies import SimpleCookie
from urllib.parse import parse_qs, urlparse

BASE = "/root/brain-auth"
AUTH_FILE = f"{BASE}/auth.json"
SESS_FILE = f"{BASE}/sessions.json"
TOTP_FILE = f"{BASE}/totp.json"
COOKIE = "brain_session"
TTL = 7 * 24 * 3600
MAX_FAILS, WINDOW = 8, 15 * 60
lock = threading.Lock()
fails = {}
used_steps = set()  # уже использованные коды (защита от повтора)


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


def totp_at(secret, step):
    key = base64.b32decode(secret.upper() + "=" * (-len(secret) % 8))
    d = hmac.new(key, struct.pack(">Q", step), hashlib.sha1).digest()
    o = d[-1] & 15
    return "%06d" % ((struct.unpack(">I", d[o:o + 4])[0] & 0x7FFFFFFF) % 1000000)


def check_code(code):
    """True, если второй фактор выключен или код верный (TOTP ±30 с либо запасной код, одноразовый)."""
    t = load(TOTP_FILE, {})
    if not t.get("secret"):
        return True
    code = code.strip().replace(" ", "").lower()
    if code.isdigit() and len(code) == 6:
        now = int(time.time() // 30)
        for step in (now - 1, now, now + 1):
            if hmac.compare_digest(totp_at(t["secret"], step), code):
                with lock:
                    if step in used_steps:
                        return False
                    used_steps.add(step)
                    for s in [x for x in used_steps if x < now - 2]:
                        used_steps.discard(s)
                return True
        return False
    h = hashlib.sha256(code.replace("-", "").encode()).hexdigest()
    with lock:
        t = load(TOTP_FILE, {})
        if h in t.get("recovery", []):
            t["recovery"].remove(h)
            save(TOTP_FILE, t)
            return True
    return False


def totp_on():
    return bool(load(TOTP_FILE, {}).get("secret"))


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
<meta name="robots" content="noindex">
<meta name="theme-color" content="#121214">
<title>Вход — Облачное хранилище</title>
<link rel="icon" href="data:image/svg+xml,%3Csvg xmlns='http://www.w3.org/2000/svg' viewBox='0 0 100 100'%3E%3Crect width='100' height='100' rx='24' fill='%23ffdd2d'/%3E%3Cpath d='M30 66h40a13 13 0 0 0 0-26 18 18 0 0 0-35-3 14 14 0 0 0-5 29z' fill='%23121214'/%3E%3C/svg%3E">
<style>
:root{color-scheme:dark;--y:#ffdd2d;--y2:#ffc400;--bg:#121214;--bg2:#1a1a1d;--line:rgba(255,255,255,.08);--muted:#9a9aa3;--txt:#f2f2f4}
*{box-sizing:border-box}
html,body{min-height:100%}
body{margin:0;min-height:100vh;display:flex;align-items:center;justify-content:center;padding:32px 16px;
color:var(--txt);font:16px/1.45 -apple-system,BlinkMacSystemFont,"Segoe UI",Roboto,"Helvetica Neue",Arial,sans-serif;
background:radial-gradient(900px 520px at 12% 8%,rgba(255,221,45,.13),transparent 60%),
radial-gradient(700px 480px at 95% 100%,rgba(255,196,0,.08),transparent 60%),linear-gradient(180deg,var(--bg2),var(--bg))}
.stage>*{min-width:0}
.stage{width:100%;max-width:1000px;display:grid;grid-template-columns:1fr 400px;gap:32px;align-items:stretch}
.card{border-radius:18px;background:linear-gradient(180deg,rgba(255,255,255,.035),rgba(255,255,255,.012));border:1px solid var(--line);box-shadow:0 18px 40px rgba(0,0,0,.45)}
.hero{position:relative;overflow:hidden;padding:36px;min-height:420px;display:flex;flex-direction:column;justify-content:space-between}
.clouds{position:absolute;inset:0;pointer-events:none}
.cl{position:absolute;opacity:.12;filter:blur(5px);animation:fx 20s ease-in-out infinite}
.cl.a{width:420px;left:-40px;top:18px}.cl.b{width:260px;right:-60px;top:150px;animation-duration:24s}.cl.c{width:200px;left:60px;bottom:-14px;animation-duration:28s}
@keyframes fx{0%,100%{transform:translateX(-12px)}50%{transform:translateX(12px)}}
@media (prefers-reduced-motion:reduce){.cl{animation:none}}
.title{position:relative;display:flex;gap:16px;align-items:center}
.logo{flex:none;width:60px;height:60px;border-radius:16px;background:linear-gradient(135deg,var(--y),var(--y2));display:flex;align-items:center;justify-content:center;box-shadow:0 8px 24px rgba(255,221,45,.22)}
.logo svg{width:34px;height:34px}
h1{font-size:24px;margin:0;letter-spacing:-.01em}
.lead{margin:4px 0 0;color:var(--muted)}
.features{position:relative;display:grid;grid-template-columns:repeat(2,1fr);gap:18px;margin-top:36px}
.f{display:flex;gap:12px;align-items:flex-start}
.f b{display:block;font-weight:600}
.f span{display:block;font-size:13px;color:var(--muted)}
.ic{flex:none;width:40px;height:40px;border-radius:12px;background:rgba(255,221,45,.1);border:1px solid rgba(255,221,45,.18);display:flex;align-items:center;justify-content:center}
.ic svg{width:20px;height:20px;stroke:var(--y);fill:none;stroke-width:1.8;stroke-linecap:round;stroke-linejoin:round}
.foot{position:relative;margin-top:28px;font-size:12px;color:#6d6d75}
form{padding:32px 28px;display:flex;flex-direction:column}
h2{font-size:20px;margin:0 0 6px}
.sub{margin:0 0 10px;font-size:14px;color:var(--muted)}
label{display:block;font-size:13px;color:var(--muted);margin:14px 0 6px}
input{width:100%;font:inherit;font-size:16px;padding:12px 14px;border-radius:11px;border:1px solid rgba(255,255,255,.1);background:rgba(0,0,0,.25);color:var(--txt);outline:none;transition:border-color .15s,box-shadow .15s}
input:focus{border-color:rgba(255,221,45,.6);box-shadow:0 0 0 3px rgba(255,221,45,.15)}
button{margin-top:24px;width:100%;font:inherit;font-size:16px;font-weight:600;padding:13px;border:0;border-radius:11px;background:var(--y);color:#121214;cursor:pointer;transition:background .15s,transform .05s}
button:hover{background:#ffe55c}button:active{transform:translateY(1px)}
.err{color:#ff8a80;margin-top:14px;font-size:14px}
@media (max-width:860px){.stage{grid-template-columns:1fr;gap:16px;max-width:440px}form{order:1}.hero{order:2;min-height:0;padding:24px}.features{grid-template-columns:1fr;gap:14px;margin-top:24px}}
</style></head><body><main class="stage">
<section class="card hero" aria-labelledby="t">
<div class="clouds" aria-hidden="true">
<svg class="cl a" viewBox="0 0 200 60"><g fill="#ffdd2d"><ellipse cx="40" cy="30" rx="36" ry="20"/><ellipse cx="82" cy="22" rx="28" ry="16"/><ellipse cx="122" cy="32" rx="48" ry="22"/></g></svg>
<svg class="cl b" viewBox="0 0 200 60"><g fill="#fff"><ellipse cx="40" cy="30" rx="30" ry="16"/><ellipse cx="80" cy="22" rx="24" ry="14"/></g></svg>
<svg class="cl c" viewBox="0 0 200 60"><g fill="#ffdd2d"><ellipse cx="60" cy="30" rx="36" ry="18"/></g></svg>
</div>
<div>
<div class="title">
<div class="logo" aria-hidden="true"><svg viewBox="0 0 24 24" fill="none" stroke="#121214" stroke-width="1.9" stroke-linecap="round" stroke-linejoin="round"><path d="M7 18h10a4 4 0 0 0 .6-7.96A5.5 5.5 0 0 0 7.1 9.1 4.5 4.5 0 0 0 7 18z"/></svg></div>
<div><h1 id="t">Облачное хранилище</h1><p class="lead">Доступ к вашим файлам и данным в безопасном облаке.</p></div>
</div>
<div class="features" aria-hidden="true">
<div class="f"><div class="ic"><svg viewBox="0 0 24 24"><path d="M3 7a2 2 0 0 1 2-2h4l2 2h8a2 2 0 0 1 2 2v8a2 2 0 0 1-2 2H5a2 2 0 0 1-2-2z"/></svg></div><div><b>Хранение файлов</b><span>Документы и фото в одном месте</span></div></div>
<div class="f"><div class="ic"><svg viewBox="0 0 24 24"><rect x="5" y="11" width="14" height="9" rx="2"/><path d="M8 11V8a4 4 0 0 1 8 0v3"/></svg></div><div><b>Защищённый доступ</b><span>Шифрование и вход с подтверждением</span></div></div>
<div class="f"><div class="ic"><svg viewBox="0 0 24 24"><path d="M20 12a8 8 0 0 1-14 5.3M4 12a8 8 0 0 1 14-5.3"/><path d="M18 3v4h-4M6 21v-4h4"/></svg></div><div><b>Синхронизация</b><span>Изменения видны сразу на всех устройствах</span></div></div>
<div class="f"><div class="ic"><svg viewBox="0 0 24 24"><rect x="3" y="4" width="13" height="10" rx="1.5"/><rect x="15" y="9" width="6" height="11" rx="1.5"/><path d="M7 18h6"/></svg></div><div><b>Любое устройство</b><span>Компьютер, телефон, планшет</span></div></div>
</div>
</div>
<div class="foot">Доступ только для зарегистрированных пользователей.</div>
</section>
<form class="card" method="post" action="/api/auth/login" aria-labelledby="lt">
<h2 id="lt">Вход в аккаунт</h2><p class="sub">Введите данные учётной записи</p>
<input type="hidden" name="next" value="{next}">
<label for="l">Логин</label><input id="l" name="login" autocomplete="username" autocapitalize="none" required>
<label for="p">Пароль</label><input id="p" name="password" type="password" autocomplete="current-password" required>
{code}<button type="submit">Войти</button>{err}
</form>
</main></body></html>"""


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
        c = ('<label for="c">Код из приложения</label><input id="c" name="code" inputmode="numeric" '
             'autocomplete="one-time-code" autocapitalize="none" required>') if totp_on() else ""
        return PAGE.replace("{next}", html.escape(safe_next(nxt))).replace("{code}", c).replace("{err}", e).encode()

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
        ok = check_password(form.get("login", [""])[0], form.get("password", [""])[0])
        if ok and not check_code(form.get("code", [""])[0]):
            ok = False
        if not ok:
            with lock:
                fails[ip].append(now)
            msg = "Неверный логин, пароль или код." if totp_on() else "Неверный логин или пароль."
            return self.send(401, self.login_page(nxt, msg))
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
