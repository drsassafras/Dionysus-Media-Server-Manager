"""What the dev tools' own pages share (dev/style.py's review page, dev/auditgui.py's audits page): their colours, light
and dark, so they look like one set of tools. Not the app's look: the app's pages are the style guide's. And how their
servers answer: to the browser that opened the address they printed only (Session), with the same headers (send)."""

LIGHT = ("--bg:#f4f4f1;--card:#fff;--ink:#1b1f1d;--mute:#5a625d;--line:#dcdcd6;--code:#f6f7f5;--before:#7f4205;"
         "--before-bg:#fbe8cf;--after:#155c37;--after-bg:#ddefe3;--gone:#8a1c14;--gone-bg:#f8d9d6")
DARK = ("--bg:#161917;--card:#1f2321;--ink:#e6e9e7;--mute:#a3aca6;--line:#343a37;--code:#272c29;--before:#f3c58e;"
        "--before-bg:#4a3213;--after:#9fdcb8;--after-bg:#173b28;--gone:#f2aaa3;--gone-bg:#4a1e1a;color-scheme:dark")
THEME = (":root{" + LIGHT + "}@media (prefers-color-scheme:dark){:root:not([data-theme=light]){" + DARK + "}}"
         ":root[data-theme=dark]{" + DARK + "}")
BASE = ("*{box-sizing:border-box}[hidden]{display:none!important}body{margin:0;background:var(--bg);color:var(--ink);"
        "font:15px/1.5 system-ui,sans-serif}")


# ---------------------------------------------------------------- their servers (127.0.0.1, a port of their own)
def headers(nonce: str = "", img: str = "data:") -> list[tuple[str, str]]:
    """What every response of a dev tool's page sends: never framed (an Approve or Start button overlaid by another
    site's page), never sniffed, and a Content-Security-Policy that runs only the page's own script and styles (their
    nonce, this response's: no style="..." in its markup) and loads nothing from anywhere else."""
    script = f"'nonce-{nonce}'" if nonce else "'none'"
    style = script
    return [("Cache-Control", "no-store"), ("X-Content-Type-Options", "nosniff"), ("X-Frame-Options", "DENY"),
            ("Referrer-Policy", "no-referrer"),
            ("Content-Security-Policy", f"default-src 'none'; script-src {script}; style-src {style}; connect-src 'self'; "
                                        f"img-src {img}; font-src 'self'; base-uri 'none'; form-action 'none'; frame-ancestors 'none'")]


def nonced(page: str, nonce: str) -> str:
    """A page with this response's nonce on each of its own <script> and <style> tags, and its stylesheet links."""
    return (page.replace("<script>", f'<script nonce="{nonce}">').replace("<style>", f'<style nonce="{nonce}">')
            .replace('<link rel="stylesheet"', f'<link rel="stylesheet" nonce="{nonce}"'))


class Session:
    """Who a dev tool's page is for: the browser that opened it. Its address carries a one-time code; the first visit
    with it gets a cookie (HttpOnly, SameSite=Strict) and every request after needs the cookie. So the page, and the
    token in it, go to that browser only: never to another program on this computer that merely asks with the right
    Host. A second visit with the code is refused, unless it's from the browser that has the cookie.

    The page is on a name of its own (<name>-<random>.localhost, this computer): cookies don't keep to a port, so on
    127.0.0.1 its cookie would go to every other server there too (another user's, say), but they do keep to a name.
    A browser that can't find that name gets 127.0.0.1 instead (opener's page tries the name first). The code is never
    on a command line nor in what a tool prints: the browser is sent a file only you can read (opener) that takes it there."""

    def __init__(self, name: str):
        import secrets
        self.name = name
        self.code = secrets.token_urlsafe(18)
        self.secret = secrets.token_urlsafe(32)
        self.host = f"{name}-{secrets.token_hex(8)}.localhost"
        self.used = False
        self.at = ""        # (the Host the browser came in by, once it has: the page's from then on)
        self.folder = ""    # (opener's, until the code is spent or the page closes)

    def cookie(self, port: int) -> str:
        return f"{self.name}-{port}"   # (each page's its own name)

    def address(self, port: int, host: str = "") -> str:
        """The page's address, without its code: what a tool prints."""
        return f"http://{host or self.host}:{port}/"

    def url(self, port: int, host: str = "") -> str:
        return f"{self.address(port, host)}?code={self.code}"

    def opener(self, port: int) -> str:
        """The file the browser is sent to: in a folder only you can open, only you can read it, and it takes the
        browser to the page — on its own name if the browser finds it (a tiny picture fetched from there first), else
        on 127.0.0.1. Removed once the code is spent (close)."""
        import json
        import os
        import tempfile
        self.folder = tempfile.mkdtemp(prefix=f"{self.name}-page-")   # (0700)
        path = os.path.join(self.folder, "open.html")
        named, loopback = self.url(port), self.url(port, "127.0.0.1")
        page = ('<!doctype html><html lang="en"><head><meta charset="utf-8"><title>Opening the page…</title></head><body>'
                '<p>Opening the page…</p><script>(function(){var done=false,go=function(u){if(!done){done=true;location.replace(u);}},'
                f'i=new Image();i.onload=function(){{go({json.dumps(named)});}};i.onerror=function(){{go({json.dumps(loopback)});}};'
                f'setTimeout(function(){{go({json.dumps(loopback)});}},4000);i.src={json.dumps(self.address(port) + "ping.png")}+"?"+Date.now();'
                '})();</script></body></html>')
        fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
        with os.fdopen(fd, "w", encoding="utf-8") as fh:
            fh.write(page)
        return path

    def close(self) -> None:
        import shutil
        if self.folder:
            shutil.rmtree(self.folder, ignore_errors=True)
            self.folder = ""

    def _hosts(self, port: int) -> tuple[str, ...]:
        return (self.at,) if self.at else (f"{self.host}:{port}", f"127.0.0.1:{port}")

    def ok(self, handler, port: int) -> bool:
        """Whether a request is from the page's browser: by the name it came in by (not another site's name pointed
        here), not sent by another site's page, and with the cookie."""
        import http.cookies
        import secrets
        h = handler.headers
        if not self.at or h.get("Host") != self.at or h.get("Sec-Fetch-Site", "same-origin") not in ("same-origin", "none"):
            return False
        try:
            jar = http.cookies.SimpleCookie(h.get("Cookie", ""))
        except http.cookies.CookieError:
            return False
        got = jar.get(self.cookie(port))
        return got is not None and secrets.compare_digest(got.value, self.secret)

    def enter(self, handler, port: int, query: str) -> str | None:
        """For a visit with the code: the Set-Cookie that lets this browser in (the code then spent), else None."""
        import secrets
        import urllib.parse
        code = (urllib.parse.parse_qs(query).get("code") or [""])[0]
        host = handler.headers.get("Host", "")
        if not code or host not in self._hosts(port) or not secrets.compare_digest(code, self.code):
            return None
        if self.used and not self.ok(handler, port):
            return None
        self.used, self.at = True, host
        self.close()   # (the code spent: its file goes)
        return f"{self.cookie(port)}={self.secret}; Path=/; HttpOnly; SameSite=Strict"


def show(session: Session, port: int, open_browser: bool = True) -> str:
    """The page opened in the browser by way of session.opener's file (the code never on a command line); the file's
    path, to say (to open by hand when no browser opened)."""
    import pathlib
    import webbrowser
    path = session.opener(port)
    if open_browser:
        webbrowser.open(pathlib.Path(path).as_uri())
    return path


def reopen(path: str) -> None:
    """show's file (its path, as show gave it) opened in the browser again."""
    import pathlib
    import webbrowser
    webbrowser.open(pathlib.Path(path).as_uri())


def private(path: str):
    """A log a dev tool's page writes (what it says while it's open), for you alone to read: 0600, made or not."""
    import os
    fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
    os.fchmod(fd, 0o600)
    return os.fdopen(fd, "w", encoding="utf-8", buffering=1)


REFUSED = (b"This page is for the browser that opened the address its command printed (that address works once). "
           b"To open it again, run the command again.\n")


def send(handler, code: int, body: bytes, ctype: str, nonce: str = "", img: str = "data:",
         more: list[tuple[str, str]] | None = None) -> None:
    """A response of a dev tool's page, with what every one sends (headers)."""
    handler.send_response(code)
    handler.send_header("Content-Type", ctype)
    handler.send_header("Content-Length", str(len(body)))
    for k, v in headers(nonce, img) + list(more or []):
        handler.send_header(k, v)
    handler.end_headers()
    handler.wfile.write(body)


# (a 1×1 transparent PNG: what opener's page fetches to see whether the browser finds the page's own name)
PING = bytes.fromhex("89504e470d0a1a0a0000000d49484452000000010000000108060000001f15c489"
                     "0000000b4944415478da636000020000050001e9fadcd80000000049454e44ae426082")


def entry(handler, session: Session, port: int, where: str, query: str) -> bool:
    """opener's picture, or a visit with the one-time code, answered (True): the cookie and on to the page, or refused;
    False for any other request, for the page's own checks (session.ok). On to the page by a page of its own, not a
    redirect: the visit comes from opener's file, another site to the browser, and a SameSite=Strict cookie isn't sent
    on the way on from there; from the page's own address it is."""
    import secrets
    import urllib.parse
    if where == "/ping.png":
        send(handler, 200, PING, "image/png")
        return True
    if where != "/" or "code" not in urllib.parse.parse_qs(query):
        return False
    cookie = session.enter(handler, port, query)
    if cookie is None and not session.ok(handler, port):
        send(handler, 403, REFUSED, "text/plain; charset=utf-8")
        return True
    nonce = secrets.token_urlsafe(18)
    on = (f'<!doctype html><html lang="en"><head><meta charset="utf-8"><title>Opening the page…</title></head><body>'
          f'<p><a href="/">Open the page</a></p><script nonce="{nonce}">location.replace("/");</script></body></html>')
    send(handler, 200, on.encode(), "text/html; charset=utf-8", nonce, more=[("Set-Cookie", cookie)] if cookie else [])
    return True


# ---------------------------------------------------------------- the server itself
TOKEN = "X-Page-Token"   # noqa: S105 — (the header a page's own script sends with each request: the token the page was served with)


def beat(token: str, every: float) -> str:
    """The script a page runs while it's open: each tab beats (/alive) every ``every`` seconds (and when it comes back in
    front) and says goodbye as it goes (/bye), so its server stops once the last tab is closed. sendBeacon sends no
    headers, so the token's in the address. To place in the page's own <script>, where its token is known. A beat
    answered "stale" (serve's ``stale``) calls the page's pageStale(), one not answered its pageLost(), when it has them."""
    import json
    return ("(function(){var id=Math.random().toString(36).slice(2),q='?tab='+id+'&t='+encodeURIComponent(" + json.dumps(token) + ");"
            "var b=function(){fetch('/alive'+q,{method:'POST'}).then(function(r){if(r.status===200&&window.pageStale)pageStale();},"
            "function(){if(window.pageLost)pageLost();});};b();setInterval(b," + str(int(every * 1000)) + ");"
            "addEventListener('visibilitychange',function(){if(!document.hidden)b();});"
            "addEventListener('pagehide',function(){navigator.sendBeacon('/bye'+q);});})();")


TYPES = {".png": "image/png", ".svg": "image/svg+xml", ".css": "text/css; charset=utf-8", ".js": "text/javascript; charset=utf-8",
         ".json": "application/json", ".txt": "text/plain; charset=utf-8", ".html": "text/html; charset=utf-8", ".woff2": "font/woff2"}


def serve(name: str, html, routes_get: dict | None = None, routes_post: dict | None = None, *, files: dict[str, str] | None = None,
          title: str = "", note: str = "Ctrl-C to stop", img: str = "data:", gone: tuple[float, float] = (3.0, 10.0),
          idle: tuple[float, str] | None = None, busy=None, port: int = 0, open_browser: bool = True, ready=None,
          ended=None, linger: float = 0.0, stale=None, kept: dict | None = None, keep=None) -> None:
    """A dev tool's page, served on 127.0.0.1 (a port of its own) to the browser that opened the address it printed
    (Session: its one-time code, its cookie, its own name), until the page is closed, Ctrl-C, or ``ended`` is set.

    ``html(token)`` is the page (its own script sends the token in the X-Page-Token header, and beats: beat); each response's
    nonce is put on its <script> and <style> tags (nonced). ``routes_get`` map a path to a function of the address's
    query (a dict of its first values), ``routes_post`` to one of the request's JSON body; what one returns goes back as
    JSON, and a ValueError or OSError it raises as a 400 with its message. ``files`` map a name to a file served as it
    is (a picture of the sample, say). ``gone`` is how long after a tab's goodbye (a reload comes back sooner) and
    how long without any tab's beat the page counts as closed; ``busy()`` true keeps it up all the same (an audit
    running); ``idle`` is (seconds, the command that opens it again) to close it unused — the beats don't count as using
    it. ``ready(server, url, token)`` is told once it's up (the url with its code: for a test's browser). ``linger``:
    how long it stays up once its last tab is closed (a tab opened again meanwhile finds it still there). ``stale()``
    true answers each beat "stale": the page's code changed since the server started, and the page says to start it
    again. ``kept`` (port, host, secret, token, at) brings it back on the address and keys it had (a tab still open
    reconnects by itself; no new tab is opened unless none does within seconds) — unless that port's taken now, then a
    fresh address — and ``keep(info)`` is told the address and keys to keep, as they're set."""
    import http.server
    import json
    import os
    import secrets
    import threading
    import time
    import urllib.parse
    title = title or name
    if kept and port and kept.get("port") != port:
        kept = None
    token = kept["token"] if kept else secrets.token_urlsafe(24)
    session = Session(name)
    if kept:
        session.host, session.secret, session.at = kept["host"], kept["secret"], str(kept.get("at", ""))
    routes_get, routes_post, files = routes_get or {}, routes_post or {}, files or {}

    def kept_now() -> dict:
        return {"port": port, "host": session.host, "secret": session.secret, "token": token, "at": session.at}
    lock = threading.Lock()
    used = [time.time()]
    ended = ended if ended is not None else threading.Event()
    tabs: dict[str, list] = {}   # each open tab's page: [its last beat, when it said goodbye (or None)]

    class Handler(http.server.BaseHTTPRequestHandler):
        def log_message(self, format, *args):   # noqa: A002  (quiet: the tool says what matters on the terminal)
            pass

        def _host_ok(self) -> bool:   # (the page's browser: this computer's name, its cookie)
            return session.ok(self, port)

        def _token_ok(self) -> bool:
            return secrets.compare_digest(self.headers.get(TOKEN, ""), token)

        def _send(self, code: int, body: bytes, ctype: str, nonce: str = "") -> None:
            send(self, code, body, ctype, nonce, img=img)

        def _json(self, fn, q) -> None:
            try:
                got = fn(q)
            except (ValueError, OSError) as e:
                return self._send(400, json.dumps({"error": str(e)}).encode(), "application/json")
            self._send(200, json.dumps(got, default=str).encode(), "application/json")

        def do_GET(self):
            used[0] = time.time()
            where, _, query = self.path.partition("?")
            if entry(self, session, port, where, query):
                if keep and session.at:
                    keep(kept_now())
                return None
            if not self._host_ok():
                return self._send(403, REFUSED, "text/plain; charset=utf-8")
            if where == "/":
                nonce = secrets.token_urlsafe(18)   # (this response's: its own script and style run, nothing else)
                return self._send(200, nonced(html(token), nonce).encode(), "text/html; charset=utf-8", nonce)
            name = where.lstrip("/")
            if name in files:
                try:
                    with open(files[name], "rb") as f:
                        body = f.read()
                except OSError:
                    return self._send(404, b"", "text/plain")
                return self._send(200, body, TYPES.get(os.path.splitext(name)[1].lower(), "application/octet-stream"))
            if where not in routes_get or not self._token_ok():
                return self._send(404 if where not in routes_get else 403, b"", "text/plain")
            self._json(routes_get[where], {k: v[0] for k, v in urllib.parse.parse_qs(query).items()})

        def do_POST(self):
            where, _, query = self.path.partition("?")
            if where in ("/alive", "/bye"):   # (a tab's beat or goodbye: not a use of it, for idle's limit)
                q = urllib.parse.parse_qs(query)
                tab, t = (q.get("tab") or [""])[0], (q.get("t") or [""])[0]
                if not self._host_ok() or not tab or not secrets.compare_digest(t, token):
                    return self._send(403, b"", "text/plain")
                with lock:
                    seen = tabs.setdefault(tab, [time.time(), None])
                    seen[0 if where == "/alive" else 1] = time.time()
                    if where == "/alive":
                        seen[1] = None   # (back after a goodbye: a reload)
                if where == "/alive" and stale and stale():   # (the server runs old code: the page says to start it again)
                    return self._send(200, b"stale", "text/plain")
                return self._send(204, b"", "text/plain")
            used[0] = time.time()
            if not self._host_ok() or not self._token_ok() or where not in routes_post:
                return self._send(403, b"", "text/plain")
            try:
                q = json.loads(self.rfile.read(min(int(self.headers.get("Content-Length", "0")), 2_000_000)) or b"{}")
            except ValueError:
                return self._send(400, b"", "text/plain")
            self._json(routes_post[where], q if isinstance(q, dict) else {})

    try:
        server = http.server.ThreadingHTTPServer(("127.0.0.1", kept["port"] if kept else port), Handler)
    except OSError:   # (its old port's taken now: a new address, and a new tab)
        if not kept:
            raise
        kept, token = None, secrets.token_urlsafe(24)
        session = Session(name)
        server = http.server.ThreadingHTTPServer(("127.0.0.1", port), Handler)
    port = server.server_port
    if keep:
        keep(kept_now())
    url = session.url(port)   # (its one-time code: never printed, nor on a command line; the browser gets it from opener's file)
    opened = show(session, port, open_browser and not kept)
    print(f"The {title} page: {session.address(port)}  ({note}). " + ("A tab still open reconnects by itself. " if kept else "")
          + f"If no browser opened it: open {opened}")

    def reopen_unless_back():   # (back on its old address: a tab still open reconnects within seconds; if none does, one is opened)
        if not ended.wait(10.0):
            with lock:
                none = not tabs
            if none:
                reopen(opened)
    if kept and open_browser:
        threading.Thread(target=reopen_unless_back, daemon=True).start()

    def watch():   # (closes it once its page is closed (and linger has passed), or after idle's time unused)
        waits = [1.0, gone[0] / 2, gone[1] / 4] + ([idle[0]] if idle else [])
        closed = None
        while not ended.wait(min(waits)):   # (often enough for the times it keeps to)
            now = time.time()
            if idle and now - used[0] > idle[0]:
                print(f"No one has used the page for {round(idle[0] / 60)} minutes, so it's closed: {idle[1]} opens it again.")
                break
            with lock:   # (a request under way finishes first, if it holds the lock)
                open_ = [t for t, (beat_, bye) in tabs.items() if ((now - bye < gone[0]) if bye else (now - beat_ < gone[1]))]
                gone_ = bool(tabs) and not open_ and not (busy and busy())
            if not gone_:
                closed = None
            elif closed is None:
                closed = now
            if gone_ and closed is not None and now - closed >= linger:
                print(f"The {title} page was closed" + (f" {round(linger / 60)} minutes ago." if linger else "."))
                break
        server.shutdown()
    threading.Thread(target=watch, daemon=True).start()
    if ready:
        ready(server, url, token)
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        ended.set()
        server.server_close()
        session.close()
