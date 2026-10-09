"""webmd - serve a directory tree in the browser, rendering .md files as web pages.

Usage:
    webmd [DIR] [-p PORT] [-b BIND] [--all] [--open] [--no-auth] [--env-file PATH]
          [--tls | --no-tls] [--cert PATH --key PATH]

Binds to 127.0.0.1 by default. When bound to any non-loopback address, HTTP
Basic Auth is required (disable with --no-auth). Credentials come from
WEBMD_USER / WEBMD_PASSWORD in the environment or the env file (default:
$XDG_CONFIG_HOME/webmd/.env, i.e. ~/.config/webmd/.env), which is generated
with a random password on first boot and reused afterwards.

Non-loopback binds also serve HTTPS by default (disable with --no-tls), using a
self-signed certificate generated into the config dir, or your own via
--cert/--key.

Markdown is rendered client-side with marked.js (GitHub-flavored), sanitized
with DOMPurify, styled with github-markdown-css, and highlighted with
highlight.js. All of these are bundled in the package (webmd/static/) and
served from /__webmd__/static/, so nothing is fetched from the internet.

If the default port (8000) is taken, webmd falls back to a random free port in
49152-65535 (up to 10 tries). A port given explicitly with -p never falls back.
"""

import argparse
import base64
import errno
import hmac
import html
import ipaddress
import json
import math
import mimetypes
import os
import random
import secrets
import socket
import socketserver
import ssl
import sys
import threading
import time
import urllib.parse
import webbrowser
from functools import partial
from http import HTTPStatus
from http.server import SimpleHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

from . import __version__
from .permissions import create_private_file

MD_EXTENSIONS = {".md", ".markdown", ".mdown", ".mkd"}
CONFIG_DIR = Path(os.environ.get("XDG_CONFIG_HOME") or Path.home() / ".config") / "webmd"
DEFAULT_ENV_FILE = CONFIG_DIR / ".env"
DEFAULT_CERT = CONFIG_DIR / "cert.pem"
DEFAULT_KEY = CONFIG_DIR / "key.pem"
DEFAULT_USER = "webmd"
DEFAULT_PORT = 8000

STATIC_DIR = Path(__file__).resolve().parent / "static"
# Reserved URL prefix for bundled assets; never maps into the served directory.
STATIC_PREFIX = "/__webmd__/static/"

FALLBACK_PORTS = range(49152, 65536)  # IANA dynamic/private range, inclusive
FALLBACK_ATTEMPTS = 10

# Rendered pages only ever load bundled same-origin assets. Images may come
# from anywhere (Markdown commonly embeds remote badges/screenshots).
CSP = "; ".join(
    [
        "default-src 'none'",
        "script-src 'self'",
        "style-src 'self'",
        "img-src * data:",
        "media-src * data:",
        "connect-src 'none'",
        "object-src 'none'",
        "frame-src 'none'",
        "child-src 'none'",
        "form-action 'none'",
        "base-uri 'none'",
        "frame-ancestors 'none'",
        "require-trusted-types-for 'script'",
        "trusted-types dompurify",
    ]
)
# Files from the served tree (?raw, .html, .svg, ...) get a sandbox CSP: an
# .html or .svg in the tree can't run script on this origin or read the auth.
# This is also the default for errors and redirects.
RAW_CSP = (
    "default-src 'none'; img-src * data:; media-src * data:; style-src 'unsafe-inline'; frame-ancestors 'none'; sandbox"
)
# Chrome refuses to show PDFs under a sandbox CSP, and PDFs can't run script
# on this origin anyway, so they only get the framing restriction.
PASSIVE_TYPES = {"application/pdf"}
PASSIVE_CSP = "frame-ancestors 'none'"
SECURITY_HEADERS = {
    "X-Content-Type-Options": "nosniff",
    "Referrer-Policy": "no-referrer",
    "X-Frame-Options": "DENY",
    "Cross-Origin-Opener-Policy": "same-origin",
}

PAGE = """<!doctype html>
<html lang="en">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>{title}</title>
<link rel="stylesheet" href="/__webmd__/static/vendor/github-markdown.css">
<link rel="stylesheet" media="(prefers-color-scheme: light)" href="/__webmd__/static/vendor/highlight-github.min.css">
<link rel="stylesheet" media="(prefers-color-scheme: dark)"
      href="/__webmd__/static/vendor/highlight-github-dark.min.css">
<link rel="stylesheet" href="/__webmd__/static/webmd.css">
</head>
<body>
<header>{breadcrumbs}{extra}</header>
<main class="markdown-body" id="content">{body}</main>
{scripts}
</body>
</html>
"""

MD_SCRIPTS = """
<script id="md-source" type="application/json">{source}</script>
<script src="/__webmd__/static/vendor/purify.min.js"></script>
<script src="/__webmd__/static/vendor/marked.min.js"></script>
<script src="/__webmd__/static/vendor/highlight.min.js"></script>
<script src="/__webmd__/static/webmd.js"></script>
"""


def is_env_file(name):
    """True for .env and .env.* files, which are never served or listed.

    Case-insensitive, and ignores trailing dots/spaces, because the default
    filesystems on macOS and Windows treat ".ENV" or ".env." as ".env".
    """
    name = name.rstrip(". ").lower()
    return name == ".env" or name.startswith(".env.")


def is_loopback(host):
    """True if `host` (name or IP) only resolves to loopback addresses."""
    try:
        return ipaddress.ip_address(host).is_loopback
    except ValueError:
        pass
    try:
        infos = socket.getaddrinfo(host, None)
    except socket.gaierror:
        return False
    return all(ipaddress.ip_address(i[4][0]).is_loopback for i in infos)


def read_env_file(path):
    """Parse simple KEY=VALUE lines; ignores blanks, comments and surrounding quotes."""
    values = {}
    for line in path.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, _, val = line.partition("=")
        key = key.strip().removeprefix("export ").strip()
        val = val.strip()
        if len(val) >= 2 and val[0] == val[-1] and val[0] in "'\"":
            val = val[1:-1]
        values[key] = val
    return values


def load_credentials(env_path):
    """Return (user, password, generated) from the environment / env file.

    Real environment variables win over the file. If the file is missing, or
    lacks WEBMD_PASSWORD, a random password is generated and written to it.
    """
    file_vals = read_env_file(env_path) if env_path.exists() else {}
    user = os.environ.get("WEBMD_USER") or file_vals.get("WEBMD_USER")
    password = os.environ.get("WEBMD_PASSWORD") or file_vals.get("WEBMD_PASSWORD")
    if password:
        return user or DEFAULT_USER, password, False

    user = user or DEFAULT_USER
    password = secrets.token_urlsafe(18)
    lines = []
    if not env_path.exists():
        lines.append("# webmd Basic Auth credentials (used when bound to a non-localhost address).")
        lines.append("# Edit freely; changes apply on the next restart.")
    if "WEBMD_USER" not in file_vals:
        lines.append(f"WEBMD_USER={user}")
    lines.append(f"WEBMD_PASSWORD={password}")
    existing = env_path.read_text(encoding="utf-8") if env_path.exists() else ""
    if existing and not existing.endswith("\n"):
        existing += "\n"
    create_private_file(env_path, (existing + "\n".join(lines) + "\n").encode("utf-8"))
    return user, password, True


class AuthThrottle:
    """Bounded limits on failed Basic Auth attempts. Thread-safe.

    Two independent limits:

    - Per client (keyed by the TCP peer address, never a client-supplied header):
      after `free_failures` misses, each further miss doubles a wait, starting at
      `base_delay` and capped at `max_delay` seconds. A success resets it.
    - Global token bucket: at most `global_burst` misses at once, refilling at
      `global_rate` per second. This caps a distributed guesser to roughly
      `global_rate` guesses/second however many addresses it uses.

    While a limit is active, requests carrying credentials get 429 without the
    password being checked, so guesses made during the wait are worthless.
    Nothing is permanent: waits expire on their own, client entries are dropped
    after `forget_after` seconds without a failure, and the table is capped at
    `max_clients` entries (oldest evicted first).
    """

    def __init__(
        self,
        free_failures=5,
        base_delay=1.0,
        max_delay=60.0,
        global_burst=60,
        global_rate=0.5,
        forget_after=900.0,
        max_clients=4096,
        clock=time.monotonic,
    ):
        self.free_failures = free_failures
        self.base_delay = base_delay
        self.max_delay = max_delay
        self.global_burst = global_burst
        self.global_rate = global_rate
        self.forget_after = forget_after
        self.max_clients = max_clients
        self.clock = clock
        self._lock = threading.Lock()
        self._clients = {}  # ip -> [failures, blocked_until, last_failure]
        self._tokens = float(global_burst)
        self._refilled_at = clock()

    def _refill(self, now):
        self._tokens = min(self.global_burst, self._tokens + (now - self._refilled_at) * self.global_rate)
        self._refilled_at = now

    def _prune(self, now):
        for ip in [ip for ip, entry in self._clients.items() if now - entry[2] > self.forget_after]:
            del self._clients[ip]

    def retry_after(self, ip):
        """Seconds until a request from `ip` may be checked (0.0 means allowed now)."""
        now = self.clock()
        with self._lock:
            self._refill(now)
            self._prune(now)
            entry = self._clients.get(ip)
            client_wait = entry[1] - now if entry else 0.0
            global_wait = (1 - self._tokens) / self.global_rate if self._tokens < 1 else 0.0
        return max(0.0, client_wait, global_wait)

    def failure(self, ip):
        now = self.clock()
        with self._lock:
            self._refill(now)
            self._prune(now)
            self._tokens = max(0.0, self._tokens - 1)
            if ip not in self._clients and len(self._clients) >= self.max_clients:
                del self._clients[min(self._clients, key=lambda k: self._clients[k][2])]
            entry = self._clients.setdefault(ip, [0, 0.0, 0.0])
            entry[0] += 1
            over = entry[0] - self.free_failures
            delay = 0.0 if over <= 0 else min(self.max_delay, self.base_delay * 2 ** (over - 1))
            entry[1] = now + delay
            entry[2] = now

    def success(self, ip):
        with self._lock:
            self._clients.pop(ip, None)

    def tracked_clients(self):
        with self._lock:
            return len(self._clients)


def sort_entries(entries):
    """Order the entries shown in a directory listing.

    `entries` is a list of os.DirEntry. Return them in display order.
    Current policy: directories first, then Markdown files, then everything
    else; case-insensitive alphabetical within each group.
    """

    def key(e):
        if e.is_dir():
            group = 0
        elif Path(e.name).suffix.lower() in MD_EXTENSIONS:
            group = 1
        else:
            group = 2
        return (group, e.name.lower())

    return sorted(entries, key=key)


class Handler(SimpleHTTPRequestHandler):
    show_hidden = False
    auth_header = None  # expected "Basic ..." value, or None when auth is off
    throttle = AuthThrottle()

    def end_headers(self):
        # Every response, including errors, redirects and 401s, gets the baseline headers.
        for name, value in SECURITY_HEADERS.items():
            self.send_header(name, value)
        self.send_header("Content-Security-Policy", getattr(self, "_csp", RAW_CSP))
        super().end_headers()

    def guess_type(self, path):
        ctype = super().guess_type(path)
        if ctype in PASSIVE_TYPES:
            self._csp = PASSIVE_CSP
        return ctype

    def _reply_empty(self, status, headers=()):
        self.send_response(status)
        for name, value in headers:
            self.send_header(name, value)
        self.send_header("Content-Length", "0")
        self.end_headers()

    def _authorized(self):
        if self.auth_header is None:
            return True
        challenge = [("WWW-Authenticate", 'Basic realm="webmd", charset="UTF-8"')]
        got = self.headers.get("Authorization")
        if not got:
            # A browser's first request carries no credentials: just prompt. Not a guess.
            self._reply_empty(HTTPStatus.UNAUTHORIZED, challenge)
            return False
        ip = self.client_address[0]
        wait = self.throttle.retry_after(ip)
        if wait > 0:
            # Refuse without checking the password, so guesses during the wait reveal nothing.
            self._reply_empty(HTTPStatus.TOO_MANY_REQUESTS, [("Retry-After", str(math.ceil(wait)))])
            return False
        if hmac.compare_digest(got.encode(), self.auth_header.encode()):
            self.throttle.success(ip)
            return True
        self.throttle.failure(ip)
        self._reply_empty(HTTPStatus.UNAUTHORIZED, challenge)
        return False

    def _gate(self):
        """Run auth and path checks. Returns the resolved target, or None if a response was sent."""
        if not self._authorized():
            return None
        if urllib.parse.urlsplit(self.path).path.startswith(STATIC_PREFIX):
            return self.send_static()
        target = self._fs_path()
        if target is None:
            self.send_error(HTTPStatus.FORBIDDEN)
            return None
        rel_parts = urllib.parse.unquote(urllib.parse.urlsplit(self.path).path).split("/")
        if is_env_file(target.name) or any(is_env_file(p) for p in rel_parts):
            self.send_error(HTTPStatus.NOT_FOUND)
            return None
        return target

    def _fs_path(self):
        """Resolve the request URL to a filesystem path inside the served root."""
        url_path = urllib.parse.urlsplit(self.path).path
        rel = urllib.parse.unquote(url_path).lstrip("/")
        # NUL crashes path resolution. On Windows, "\\" is a separator and ":" is
        # drive/stream syntax (C:, file.md::$DATA), so neither may reach the filesystem.
        if "\x00" in rel or (sys.platform == "win32" and ("\\" in rel or ":" in rel)):
            return None
        root = Path(self.directory).resolve()
        target = (root / rel).resolve()
        # Refuse anything that escapes the root (../ tricks, symlinks out).
        if target != root and root not in target.parents:
            return None
        return target

    def _breadcrumbs(self, url_path):
        parts = [p for p in url_path.split("/") if p]
        crumbs = ['<a href="/">~</a>']
        href = ""
        for p in parts:
            href += "/" + urllib.parse.quote(p)
            crumbs.append(f'<a href="{href}{"/" if p != parts[-1] else ""}">{html.escape(p)}</a>')
        return " / ".join(crumbs)

    def _send_html(self, text):
        data = text.encode("utf-8")
        self._csp = CSP  # our own pages: bundled scripts only
        self.send_response(HTTPStatus.OK)
        self.send_header("Content-Type", "text/html; charset=utf-8")
        self.send_header("Content-Length", str(len(data)))
        self.send_header("Cache-Control", "no-cache")
        self.end_headers()
        if self.command != "HEAD":
            self.wfile.write(data)

    def send_static(self):
        """Serve a bundled asset from webmd/static/. Always returns None (response sent)."""
        rel = urllib.parse.unquote(urllib.parse.urlsplit(self.path).path[len(STATIC_PREFIX) :])
        root = STATIC_DIR.resolve()
        target = (root / rel).resolve()
        if root not in target.parents or not target.is_file():
            self.send_error(HTTPStatus.NOT_FOUND)
            return None
        data = target.read_bytes()
        self._csp = CSP
        self.send_response(HTTPStatus.OK)
        self.send_header("Content-Type", mimetypes.guess_type(target.name)[0] or "application/octet-stream")
        self.send_header("Content-Length", str(len(data)))
        self.send_header("Cache-Control", "no-cache")
        self.end_headers()
        if self.command != "HEAD":
            self.wfile.write(data)
        return None

    def do_HEAD(self):
        self.do_GET()

    def do_GET(self):
        parsed = urllib.parse.urlsplit(self.path)
        target = self._gate()
        if target is None:
            return
        query = urllib.parse.parse_qs(parsed.query, keep_blank_values=True)
        if target.is_file() and target.suffix.lower() in MD_EXTENSIONS and "raw" not in query:
            self.render_markdown(target, urllib.parse.unquote(parsed.path))
            return
        if target.is_file() and target.suffix.lower() in MD_EXTENSIONS:
            # ?raw: show the source as text, never let the browser interpret it.
            self.extensions_map = {**self.extensions_map, target.suffix: "text/plain; charset=utf-8"}
        # Files from the served tree, directory redirects, listings.
        if self.command == "HEAD":
            super().do_HEAD()
        else:
            super().do_GET()

    def render_markdown(self, path, url_path):
        try:
            source = path.read_text(encoding="utf-8", errors="replace")
        except OSError as e:
            self.send_error(HTTPStatus.NOT_FOUND, str(e))
            return
        # Embed as JSON; escaping "</" stops the content from closing the script tag.
        embedded = json.dumps(source).replace("</", "<\\/")
        self._send_html(
            PAGE.format(
                title=html.escape(path.name),
                breadcrumbs=self._breadcrumbs(url_path),
                extra='<a class="raw" href="?raw">raw</a>',
                body="",
                scripts=MD_SCRIPTS.replace("{source}", embedded),
            )
        )

    def list_directory(self, path):
        try:
            entries = [
                e
                for e in os.scandir(path)
                if not is_env_file(e.name) and (self.show_hidden or not e.name.startswith("."))
            ]
        except OSError:
            self.send_error(HTTPStatus.NOT_FOUND, "No permission to list directory")
            return None

        url_path = urllib.parse.unquote(urllib.parse.urlsplit(self.path).path)
        items = []
        if url_path not in ("", "/"):
            items.append('<li><span class="icon">⬆️</span><a href="../">..</a></li>')
        for e in sort_entries(entries):
            is_dir = e.is_dir()
            is_md = Path(e.name).suffix.lower() in MD_EXTENSIONS
            icon = "📁" if is_dir else ("📝" if is_md else "📄")
            href = urllib.parse.quote(e.name) + ("/" if is_dir else "")
            label = html.escape(e.name) + ("/" if is_dir else "")
            items.append(f'<li><span class="icon">{icon}</span><a href="{href}">{label}</a></li>')

        body = f'<h2>{html.escape(url_path or "/")}</h2><ul class="listing">{"".join(items)}</ul>'
        self._send_html(
            PAGE.format(
                title=html.escape(url_path or "/"),
                breadcrumbs=self._breadcrumbs(url_path),
                extra="",
                body=body,
                scripts="",
            )
        )
        return None  # we already wrote the response

    def log_message(self, fmt, *args):
        sys.stderr.write(f"  {self.command} {self.path} -> {args[1] if len(args) > 1 else ''}\n")


class Server(ThreadingHTTPServer):
    # Port-conflict detection must be exact, or the fallback logic can't work:
    # - Linux: SO_REUSEADDR only skips TIME_WAIT; it never allows a second listener.
    # - Windows: SO_REUSEADDR *would* allow a second listener, so it's off and
    #   SO_EXCLUSIVEADDRUSE is set instead.
    # - macOS/BSD: SO_REUSEADDR lets 127.0.0.1:8000 bind while another process
    #   listens on *:8000 (silently stealing its traffic), so it's off and
    #   _bind_reusing_time_wait() decides per bind.
    allow_reuse_address = sys.platform.startswith("linux")

    def server_bind(self):
        if hasattr(socket, "SO_EXCLUSIVEADDRUSE"):
            self.socket.setsockopt(socket.SOL_SOCKET, socket.SO_EXCLUSIVEADDRUSE, 1)
        if sys.platform.startswith("linux") or sys.platform == "win32":
            socketserver.TCPServer.server_bind(self)
        else:
            self._bind_reusing_time_wait()
        # Unlike the stdlib, don't call socket.getfqdn() here: it's a reverse DNS
        # lookup that can hang for many seconds before the socket starts listening.
        self.server_name, self.server_port = self.server_address[:2]

    def _bind_reusing_time_wait(self):
        """macOS/BSD: bind strictly; if refused, retry with SO_REUSEADDR only when nothing is listening.

        A strict bind also fails while old connections from a previous webmd
        sit in TIME_WAIT (up to ~30s after a restart). Those don't accept
        connections, a real listener does, so a connect attempt tells them apart.
        """
        try:
            self.socket.bind(self.server_address)
        except OSError as e:
            if e.errno != errno.EADDRINUSE or _someone_listening(self.address_family, *self.server_address[:2]):
                raise
            self.socket.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
            self.socket.bind(self.server_address)
        self.server_address = self.socket.getsockname()


def _someone_listening(family, host, port):
    if host in ("", "0.0.0.0", "::"):
        host = "::1" if family == socket.AF_INET6 else "127.0.0.1"
    with socket.socket(family) as probe:
        probe.settimeout(1)
        try:
            probe.connect((host, port))
        except OSError:
            return False  # refused (or unreachable): nobody is accepting connections there
    return True


class TLSServer(Server):
    """ThreadingHTTPServer that does the TLS handshake on the per-connection thread.

    Wrapping the listening socket would handshake inside accept(), letting one
    slow or stalled client block every other connection.
    """

    handshake_timeout = 10

    def __init__(self, addr, handler, ssl_context):
        self.ssl_context = ssl_context
        super().__init__(addr, handler)

    def finish_request(self, request, client_address):
        request.settimeout(self.handshake_timeout)
        try:
            first = request.recv(1, socket.MSG_PEEK)
            if first and first != b"\x16":  # not a TLS ClientHello, so plain HTTP
                request.sendall(
                    b"HTTP/1.1 400 Bad Request\r\nContent-Type: text/plain\r\n"
                    b"Connection: close\r\n\r\nThis server only speaks HTTPS. Use https://\n"
                )
                return
            conn = self.ssl_context.wrap_socket(request, server_side=True)
        except (ssl.SSLError, OSError):
            return  # failed handshake (e.g. browser rejected the cert); process_request_thread closes it
        try:
            conn.settimeout(None)
            self.RequestHandlerClass(conn, client_address, self)
        finally:
            conn.close()


def is_port_conflict(err):
    """True for bind errors meaning "this port is taken"; other errors are not retried.

    Windows reports a port held by another process (or reserved by the OS)
    as WSAEACCES (10013) rather than WSAEADDRINUSE, so on Windows that
    also counts as a conflict.
    """
    if err.errno == errno.EADDRINUSE or getattr(err, "winerror", None) == 10048:
        return True
    return sys.platform == "win32" and getattr(err, "winerror", None) == 10013


def address_family(host):
    """AF_INET6 for IPv6 literals (and "::"), AF_INET otherwise."""
    try:
        return socket.AF_INET6 if ipaddress.ip_address(host).version == 6 else socket.AF_INET
    except ValueError:
        return socket.AF_INET  # hostnames: the stdlib server resolves them as IPv4


def bind_server(make_server, host, port, *, strict, rng=random, log=print):
    """Bind `make_server((host, port))`, falling back to random high ports on conflict.

    Each candidate is bound directly (no check-then-bind race) and the bound
    server is returned with its socket still open. Only "port in use" errors
    trigger a retry; anything else (permission denied, bad address, ...)
    raises immediately. Raises SystemExit with a clear message on failure.
    """
    try:
        return make_server((host, port))
    except OSError as e:
        if not is_port_conflict(e):
            raise SystemExit(f"webmd: cannot listen on {host}:{port}: {e.strerror or e}") from e
        if strict:
            raise SystemExit(f"webmd: port {port} on {host} is already in use") from e
        first_error = e
    candidates = rng.sample([p for p in FALLBACK_PORTS if p != port], FALLBACK_ATTEMPTS)
    log(f"  port {port} is in use; trying a random port in {FALLBACK_PORTS[0]}-{FALLBACK_PORTS[-1]}", flush=True)
    for candidate in candidates:
        try:
            return make_server((host, candidate))
        except OSError as e:
            if not is_port_conflict(e):
                raise SystemExit(f"webmd: cannot listen on {host}:{candidate}: {e.strerror or e}") from e
    raise SystemExit(
        f"webmd: port {port} on {host} is in use ({first_error.strerror or first_error}) and "
        f"{FALLBACK_ATTEMPTS} random fallback ports were also taken; choose a free port with -p"
    )


def display_url(scheme, host, port):
    host = f"[{host}]" if ":" in host else host
    return f"{scheme}://{host}:{port}/"


def setup_tls(args):
    """Return (ssl_context, cert_path, generated) for the chosen cert source."""
    from . import tls  # imported lazily: only TLS mode needs `cryptography`

    if args.cert or args.key:
        if not (args.cert and args.key):
            sys.exit("webmd: --cert and --key must be given together")
        cert_path, key_path, generated = args.cert.expanduser(), args.key.expanduser(), False
    else:
        cert_path, key_path = DEFAULT_CERT, DEFAULT_KEY
        generated = tls.ensure_self_signed(cert_path, key_path, tls.local_names(args.bind))
    try:
        ctx = tls.server_context(cert_path, key_path)
    except (OSError, ssl.SSLError) as e:
        sys.exit(f"webmd: could not load certificate {cert_path} / key {key_path}: {e}")
    return ctx, cert_path, generated


def port_number(value):
    port = int(value)
    if not 0 <= port <= 65535:
        raise argparse.ArgumentTypeError(f"{value} is not a valid port (0-65535)")
    return port


def main():
    ap = argparse.ArgumentParser(prog="webmd", description="Serve a directory, rendering Markdown as HTML.")
    ap.add_argument("-V", "--version", action="version", version=f"%(prog)s {__version__}")
    ap.add_argument("dir", nargs="?", default=".", help="directory to serve (default: .)")
    ap.add_argument(
        "-p",
        "--port",
        type=port_number,
        default=None,
        help=f"port to listen on (default: {DEFAULT_PORT}, or a random free port if that's taken); "
        "an explicit port exits with an error if it's taken",
    )
    ap.add_argument(
        "-b",
        "--bind",
        default="127.0.0.1",
        help="address to bind (default: 127.0.0.1); non-loopback enables Basic Auth",
    )
    ap.add_argument(
        "--no-auth", action="store_true", help="disable Basic Auth even when bound to a non-loopback address"
    )
    ap.add_argument(
        "--env-file", type=Path, default=DEFAULT_ENV_FILE, help=f"credentials file (default: {DEFAULT_ENV_FILE})"
    )
    ap.add_argument(
        "--tls",
        action=argparse.BooleanOptionalAction,
        default=None,
        help="serve HTTPS (default: on for non-loopback binds, off for localhost)",
    )
    ap.add_argument("--cert", type=Path, help=f"TLS certificate PEM (default: self-signed {DEFAULT_CERT})")
    ap.add_argument("--key", type=Path, help=f"TLS private key PEM (default: {DEFAULT_KEY})")
    ap.add_argument("--all", action="store_true", help="show dotfiles in listings")
    ap.add_argument("--open", action="store_true", help="open the browser on start")
    args = ap.parse_args()

    root = Path(args.dir).resolve()
    if not root.is_dir():
        sys.exit(f"webmd: not a directory: {root}")

    Handler.show_hidden = args.all
    local = is_loopback(args.bind)
    use_auth = not local and not args.no_auth
    use_tls = (not local) if args.tls is None else args.tls
    if use_auth:
        env_path = args.env_file.expanduser().resolve()
        user, password, generated = load_credentials(env_path)
        token = base64.b64encode(f"{user}:{password}".encode()).decode()
        Handler.auth_header = f"Basic {token}"

    handler = partial(Handler, directory=str(root))
    family = address_family(args.bind)
    if use_tls:
        ctx, cert_path, cert_generated = setup_tls(args)
        server_class = type("TLSServer", (TLSServer,), {"address_family": family})

        def make_server(addr):
            return server_class(addr, handler, ctx)

    else:
        server_class = type("Server", (Server,), {"address_family": family})

        def make_server(addr):
            return server_class(addr, handler)

    explicit_port = args.port is not None
    # WEBMD_DEFAULT_PORT is for tests: lets them occupy "the default" without needing port 8000.
    port = args.port if explicit_port else int(os.environ.get("WEBMD_DEFAULT_PORT", DEFAULT_PORT))
    server = bind_server(make_server, args.bind, port, strict=explicit_port)
    url = display_url("https" if use_tls else "http", args.bind, server.server_address[1])
    # Build the banner first and print it in one write, so it can't interleave
    # with request logs and anything reading it sees all of it at once.
    banner = [f"webmd serving {root}", f"  -> {url}  (Ctrl-C to stop)"]
    if use_tls:
        from .tls import fingerprint

        source = "generated self-signed" if cert_generated else "using"
        banner += [f"  tls: {source} certificate {cert_path}", f"       SHA-256 {fingerprint(cert_path)}"]
    if use_auth:
        if generated:
            banner += [f"  auth: generated credentials in {env_path}", f"        user={user} password={password}"]
        else:
            banner.append(f"  auth: user={user} (password from {env_path} or WEBMD_PASSWORD)")
    elif not local:
        banner.append("  WARNING: listening on a non-loopback address with auth disabled (--no-auth)")
    if use_auth and not use_tls:
        banner.append("  WARNING: auth without TLS (--no-tls) sends the password readable on the network")
    print("\n".join(banner), flush=True)
    if args.open:
        webbrowser.open(url)
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        print("\nbye")
    finally:
        server.server_close()


if __name__ == "__main__":
    main()
