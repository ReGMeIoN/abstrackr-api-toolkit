"""abstrackr API client for scripted access.

CONTRACT (recovered from the production bundles, see abstrackr/recon/):
    POST /api/auth/web/login   body {"email","password"}  -> {"errors":[...]}
        empty errors array == success; session is a cookie, no CSRF token.
    POST /api/auth/web/logout  (no body)
    Every other /api/* route answers {"error":"Unauthorized"} without the cookie.

SAFETY RULES BAKED IN
    * Credentials are read from a local JSON file, never from argv, and are never
      echoed. Only the email is printed (with the local part masked).
    * The session cookie is cached in a local file so the password is used once.
    * Only login/logout may POST by default. Any other write needs --execute.
    * Requests are rate-limited; the API is somebody's production service.
    * No parallel requests. One at a time, out of respect for their server.

USAGE
    python abstrackr_client.py login                 # verify credentials, show session info
    python abstrackr_client.py projects              # list projects (ids/names/roles)
    python abstrackr_client.py discover              # post-login: find app chunks + /api routes
    python abstrackr_client.py get  /api/projects    # read any endpoint
    python abstrackr_client.py post /api/... --json '{...}' --execute   # explicit write
"""

import argparse
import http.cookiejar
import json
import os
import re
import sys
import time
import urllib.error
import urllib.request

sys.stdout.reconfigure(encoding="utf-8")

BASE = "https://abstrackr.com"
HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
ABST = os.path.join(ROOT, "abstrackr")
CRED = os.path.join(ABST, "credentials.json")
COOKIES = os.path.join(ABST, ".session_cookies.txt")
UA = ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
      "(KHTML, like Gecko) Chrome/124 Safari/537.36")
SLEEP = 1.0          # seconds between API calls
TIMEOUT = 60


def mask(email):
    if not email or "@" not in email:
        return "(none)"
    local, _, domain = email.partition("@")
    keep = local[:2]
    return f"{keep}{'*' * max(1, len(local) - 2)}@{domain}"


class Client:
    def __init__(self, verbose=False):
        self.jar = http.cookiejar.MozillaCookieJar(COOKIES)
        if os.path.exists(COOKIES):
            try:
                self.jar.load(ignore_discard=True, ignore_expires=True)
            except Exception:  # noqa: BLE001
                pass
        self.opener = urllib.request.build_opener(
            urllib.request.ProxyHandler({}),          # local proxy breaks TLS here
            urllib.request.HTTPCookieProcessor(self.jar))
        self.verbose = verbose
        self._last = 0.0

    # --- plumbing -----------------------------------------------------------
    def _throttle(self):
        dt = time.time() - self._last
        if dt < SLEEP:
            time.sleep(SLEEP - dt)
        self._last = time.time()

    def request(self, method, path, payload=None, raw=False):
        url = path if path.startswith("http") else BASE + path
        data = None
        headers = {"User-Agent": UA, "Accept": "application/json, text/html;q=0.9,*/*;q=0.8",
                   "Origin": BASE, "Referer": BASE + "/"}
        if payload is not None:
            data = json.dumps(payload).encode()
            headers["Content-Type"] = "application/json"
        self._throttle()
        req = urllib.request.Request(url, data=data, headers=headers, method=method)
        try:
            with self.opener.open(req, timeout=TIMEOUT) as r:
                body = r.read()
                status, hdrs = r.status, dict(r.headers)
        except urllib.error.HTTPError as e:
            body, status, hdrs = e.read(), e.code, dict(e.headers or {})
        except Exception as exc:  # noqa: BLE001
            return 0, {}, f"{type(exc).__name__}: {exc}"
        text = body.decode("utf-8", "replace")
        if raw:
            return status, hdrs, text
        try:
            return status, hdrs, json.loads(text)
        except Exception:  # noqa: BLE001
            return status, hdrs, text

    def save_cookies(self):
        try:
            self.jar.save(ignore_discard=True, ignore_expires=True)
        except Exception:  # noqa: BLE001
            pass

    # --- auth ---------------------------------------------------------------
    def login(self, email, password):
        st, hdrs, body = self.request("POST", "/api/auth/web/login",
                                      {"email": email, "password": password})
        errors = []
        if isinstance(body, dict):
            errors = body.get("errors") or []
        ok = st == 200 and not errors
        if ok:
            self.save_cookies()
        return ok, st, errors, hdrs

    def whoami(self):
        """Cheap authenticated read to prove the session works."""
        out = {}
        for path in ("/api/projects", "/api/user", "/api/me"):
            st, hdrs, body = self.request("GET", path)
            out[path] = (st, body if not isinstance(body, str) or len(body) < 300
                         else body[:300])
        return out


def load_creds():
    if not os.path.exists(CRED):
        print(f"[!] 凭据文件不存在：{CRED}")
        print("    请复制 abstrackr/credentials.example.json 为 credentials.json 并填写。")
        print("    ⚠️ 不要用主账号；建议新建一个专用账号并邀请为项目 Member。")
        return None
    with open(CRED, encoding="utf-8") as fh:
        data = json.load(fh)
    email = (data.get("email") or "").strip()
    password = data.get("password") or ""
    if not email or not password:
        print("[!] credentials.json 里 email/password 为空。")
        return None
    return email, password


def cmd_login(args):
    creds = load_creds()
    if not creds:
        return 2
    email, password = creds
    print(f"登录账号：{mask(email)}")
    ok, st, errors, hdrs = Client().login(email, password)
    print(f"  POST /api/auth/web/login -> HTTP {st}")
    if errors:
        print(f"  errors: {errors}")
    if not ok:
        print("  ❌ 登录失败。常见原因：密码错 / 账号未激活（注册后需点邮件里的激活链接）"
              "/ 触发风控。")
        return 1
    print("  ✅ 登录成功，会话 Cookie 已缓存到 abstrackr/.session_cookies.txt")
    for path, (s, b) in Client().whoami().items():
        preview = json.dumps(b, ensure_ascii=False)[:200] if not isinstance(b, str) else b[:200]
        print(f"  GET {path:<16} -> {s}  {preview}")
    return 0


def cmd_cookies(_args):
    c = Client()
    print(f"cookie file: {COOKIES}  exists={os.path.exists(COOKIES)}")
    for ck in c.jar:
        print(f"  {ck.domain} {ck.path} {ck.name} "
              f"{'<hidden>' if 'sess' in ck.name.lower() or 'token' in ck.name.lower() else ck.value}")
    return 0


def cmd_projects(_args):
    c = Client()
    st, hdrs, body = c.request("GET", "/api/projects")
    print(f"GET /api/projects -> {st}")
    print(json.dumps(body, ensure_ascii=False, indent=1)[:4000]
          if not isinstance(body, str) else body[:2000])
    return 0


def cmd_get(args):
    c = Client()
    st, hdrs, body = c.request("GET", args.path)
    print(f"GET {args.path} -> {st}")
    txt = body if isinstance(body, str) else json.dumps(body, ensure_ascii=False, indent=1)
    if isinstance(txt, str) and ("<!DOCTYPE" in txt[:200] or "<html" in txt[:200]):
        print(f"  (HTML shell, {len(txt)} chars -- unknown route / app page, not JSON)")
        return 0
    print(txt[:6000])
    return 0


def cmd_post(args):
    if not args.execute:
        print("拒绝执行：写操作需要显式 --execute（防止误触发）。")
        return 2
    c = Client()
    payload = json.loads(args.json) if args.json else None
    st, hdrs, body = c.request("POST", args.path, payload)
    print(f"POST {args.path} -> {st}")
    txt = body if isinstance(body, str) else json.dumps(body, ensure_ascii=False, indent=1)
    print(txt[:4000])
    return 0


def cmd_discover(args):
    """After login, fetch the key app pages and mine their chunks for /api routes.

    The screening/import code only loads once authenticated, and Turbopack has no
    _buildManifest.js, so the chunk list has to come out of the page HTML and its
    RSC payload (both list /_next/static/chunks/*.js).
    """
    c = Client()
    pages = args.pages or ["/dashboard", "/projects", f"/projects/{args.project}",
                           f"/projects/{args.project}/screen",
                           f"/projects/{args.project}/manage",
                           f"/projects/{args.project}/members"]
    seen, queue, routes, hits = set(), [], set(), []
    print("== seeded from pages ==")
    for page in pages:
        st, hdrs, html = c.request("GET", page, raw=True)
        if not isinstance(html, str):
            html = str(html)
        srcs = re.findall(r'/_next/static/chunks/[A-Za-z0-9_.\-]+\.js', html)
        print(f"  {page:<38} {st}  {len(html):>7} chars  {len(set(srcs)):>3} chunks")
        for s in set(srcs):
            u = BASE + s
            if u not in seen:
                queue.append(u)

    n = 0
    while queue and n < int(args.max_chunks):
        u = queue.pop(0)
        if u in seen:
            continue
        seen.add(u)
        n += 1
        st, hdrs, js = c.request("GET", u, raw=True)
        if st != 200 or not isinstance(js, str):
            continue
        before = len(routes)
        for m in re.finditer(r'["\'`](/api/[A-Za-z0-9_\-/{}$.:\[\]]{1,90})["\'`]', js):
            routes.add(m.group(1))
        for m in re.finditer(r'["\'`](/api/[A-Za-z0-9_\-/]{1,60})["\'`]\s*\+', js):
            routes.add(m.group(1) + " <concat>")
        for kw in ("labels", "citations", "import", "upload", "screening",
                   "screening_sets", "resolution", "tags"):
            for m in re.finditer(r'.{70}' + kw + r'.{110}', js, re.I):
                hits.append((u.split("/")[-1], kw, re.sub(r"\s+", " ", m.group(0))))
        for m in re.finditer(r'/_next/static/chunks/[A-Za-z0-9_.\-]+\.js', js):
            u2 = BASE + m.group(0)
            if u2 not in seen:
                queue.append(u2)
        if len(routes) != before:
            print(f"  [{n:>3}] {u.split('/')[-1][:44]:<46} +{len(routes) - before} routes")
        time.sleep(0.15)

    print(f"\n== /api routes discovered while authenticated ({len(routes)}) ==")
    for r in sorted(routes):
        print(f"  {r}")
    out = os.path.join(ABST, "recon", "routes_authenticated.txt")
    os.makedirs(os.path.dirname(out), exist_ok=True)
    with open(out, "w", encoding="utf-8") as fh:
        fh.write("\n".join(sorted(routes)) + "\n")
    print("written:", out)

    seen_ctx, shown = set(), 0
    print("\n== keyword contexts ==")
    for name, kw, ctx in hits:
        k = ctx[:60]
        if k in seen_ctx:
            continue
        seen_ctx.add(k)
        shown += 1
        if shown > int(args.max_ctx):
            break
        print(f"  [{name[:20]}] {ctx[:240]}")
    with open(os.path.join(ABST, "recon", "route_contexts.txt"), "w",
              encoding="utf-8") as fh:
        for name, kw, ctx in hits:
            fh.write(f"[{name}] {kw}\n{ctx}\n\n")
    return 0


def main():
    ap = argparse.ArgumentParser(description="abstrackr API client (read-first, --execute for writes)")
    sub = ap.add_subparsers(dest="cmd", required=True)
    sub.add_parser("login")
    sub.add_parser("cookies")
    sub.add_parser("projects")
    d = sub.add_parser("discover")
    d.add_argument("--project", default=os.environ.get("ABSTRACKR_PROJECT", ""))
    d.add_argument("--pages", nargs="*")
    d.add_argument("--max-chunks", default=250)
    d.add_argument("--max-ctx", default=60)
    g = sub.add_parser("get"); g.add_argument("path")
    p = sub.add_parser("post"); p.add_argument("path")
    p.add_argument("--json"); p.add_argument("--execute", action="store_true")
    args = ap.parse_args()
    return {"login": cmd_login, "cookies": cmd_cookies, "projects": cmd_projects,
            "discover": cmd_discover, "get": cmd_get, "post": cmd_post}[args.cmd](args)


if __name__ == "__main__":
    raise SystemExit(main())
