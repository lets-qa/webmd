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

Markdown is rendered client-side with marked.js (GitHub-flavored), styled with
github-markdown-css, and code blocks are highlighted with highlight.js (all
loaded from a CDN).
"""

import argparse
import base64
import hmac
import html
import ipaddress
import json
import os
import secrets
import socket
import socketserver
import ssl
import sys
import urllib.parse
import webbrowser
from functools import partial
from http import HTTPStatus
from http.server import SimpleHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

from . import __version__

MD_EXTENSIONS = {".md", ".markdown", ".mdown", ".mkd"}
CONFIG_DIR = Path(os.environ.get("XDG_CONFIG_HOME") or Path.home() / ".config") / "webmd"
DEFAULT_ENV_FILE = CONFIG_DIR / ".env"
DEFAULT_CERT = CONFIG_DIR / "cert.pem"
DEFAULT_KEY = CONFIG_DIR / "key.pem"
DEFAULT_USER = "webmd"

PAGE = """<!doctype html>
<html lang="en">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>{title}</title>
<link rel="stylesheet" href="https://cdn.jsdelivr.net/npm/github-markdown-css@5/github-markdown.min.css">
<link rel="stylesheet" media="(prefers-color-scheme: light)" href="https://cdn.jsdelivr.net/npm/@highlightjs/cdn-assets@11/styles/github.min.css">
<link rel="stylesheet" media="(prefers-color-scheme: dark)" href="https://cdn.jsdelivr.net/npm/@highlightjs/cdn-assets@11/styles/github-dark.min.css">
<style>
  :root {{ color-scheme: light dark; }}
  body {{ margin: 0; font-family: -apple-system, BlinkMacSystemFont, "Segoe UI", Helvetica, Arial, sans-serif;
         background: Canvas; color: CanvasText; }}
  header {{ position: sticky; top: 0; padding: 10px 24px; font-size: 14px; z-index: 1;
           border-bottom: 1px solid rgba(127,127,127,.3); background: Canvas; }}
  header a {{ color: #0969da; text-decoration: none; }}
  header a:hover {{ text-decoration: underline; }}
  header .raw {{ float: right; }}
  .markdown-body {{ max-width: 900px; margin: 0 auto; padding: 32px 24px; background: transparent; }}
  .listing {{ list-style: none; padding: 0; }}
  .listing li {{ padding: 6px 4px; border-bottom: 1px solid rgba(127,127,127,.15); }}
  .listing .icon {{ display: inline-block; width: 1.6em; }}
</style>
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
<script src="https://cdn.jsdelivr.net/npm/marked@12/marked.min.js"></script>
<script src="https://cdn.jsdelivr.net/npm/@highlightjs/cdn-assets@11/highlight.min.js"></script>
<script>
  const src = JSON.parse(document.getElementById("md-source").textContent);
  marked.use({ gfm: true });
  const el = document.getElementById("content");
  el.innerHTML = marked.parse(src);
  if (window.hljs) el.querySelectorAll("pre code").forEach(b => hljs.highlightElement(b));
  // Give headings ids so #anchors work, GitHub-style.
  el.querySelectorAll("h1,h2,h3,h4,h5,h6").forEach(h => {
    if (!h.id) h.id = h.textContent.trim().toLowerCase().replace(/[^\\w\\- ]+/g, "").replace(/ /g, "-");
  });
  if (location.hash) document.getElementById(decodeURIComponent(location.hash.slice(1)))?.scrollIntoView();
</script>
"""


def is_env_file(name):
    """True for .env and .env.* files, which are never served or listed."""
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
    env_path.parent.mkdir(parents=True, exist_ok=True)
    # Create with 0600 so the password isn't world-readable.
    fd = os.open(env_path, os.O_WRONLY | os.O_CREAT | os.O_APPEND, 0o600)
    with os.fdopen(fd, "a", encoding="utf-8") as f:
        if env_path.stat().st_size and not env_path.read_text(encoding="utf-8").endswith("\n"):
            f.write("\n")
        f.write("\n".join(lines) + "\n")
    return user, password, True


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

    def _authorized(self):
        if self.auth_header is None:
            return True
        got = self.headers.get("Authorization", "")
        if hmac.compare_digest(got.encode(), self.auth_header.encode()):
            return True
        self.send_response(HTTPStatus.UNAUTHORIZED)
        self.send_header("WWW-Authenticate", 'Basic realm="webmd", charset="UTF-8"')
        self.send_header("Content-Length", "0")
        self.end_headers()
        return False

    def _gate(self):
        """Run auth and path checks. Returns the resolved target, or None if a response was sent."""
        if not self._authorized():
            return None
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
        self.send_response(HTTPStatus.OK)
        self.send_header("Content-Type", "text/html; charset=utf-8")
        self.send_header("Content-Length", str(len(data)))
        self.send_header("Cache-Control", "no-cache")
        self.end_headers()
        self.wfile.write(data)

    def do_HEAD(self):
        if self._gate() is not None:
            super().do_HEAD()

    def do_GET(self):
        parsed = urllib.parse.urlsplit(self.path)
        target = self._gate()
        if target is None:
            return
        query = urllib.parse.parse_qs(parsed.query, keep_blank_values=True)
        if target.is_file() and target.suffix.lower() in MD_EXTENSIONS and "raw" not in query:
            self.render_markdown(target, urllib.parse.unquote(parsed.path))
            return
        super().do_GET()  # static files, directory redirects, listings

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
    def server_bind(self):
        # The stdlib calls socket.getfqdn() here, a reverse DNS lookup that can
        # hang for many seconds before the socket starts listening.
        socketserver.TCPServer.server_bind(self)
        self.server_name, self.server_port = self.server_address[:2]


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


def main():
    ap = argparse.ArgumentParser(prog="webmd", description="Serve a directory, rendering Markdown as HTML.")
    ap.add_argument("-V", "--version", action="version", version=f"%(prog)s {__version__}")
    ap.add_argument("dir", nargs="?", default=".", help="directory to serve (default: .)")
    ap.add_argument("-p", "--port", type=int, default=8000)
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
    if use_tls:
        ctx, cert_path, cert_generated = setup_tls(args)
        server = TLSServer((args.bind, args.port), handler, ctx)
    else:
        server = Server((args.bind, args.port), handler)
    host = f"[{args.bind}]" if ":" in args.bind else args.bind
    url = f"{'https' if use_tls else 'http'}://{host}:{args.port}/"
    print(f"webmd serving {root}\n  -> {url}  (Ctrl-C to stop)", flush=True)
    if use_tls:
        from .tls import fingerprint

        source = "generated self-signed" if cert_generated else "using"
        print(f"  tls: {source} certificate {cert_path}\n       SHA-256 {fingerprint(cert_path)}", flush=True)
    if use_auth:
        if generated:
            print(f"  auth: generated credentials in {env_path}\n        user={user} password={password}", flush=True)
        else:
            print(f"  auth: user={user} (password from {env_path} or WEBMD_PASSWORD)", flush=True)
    elif not local:
        print("  WARNING: listening on a non-loopback address with auth disabled (--no-auth)", flush=True)
    if use_auth and not use_tls:
        print("  WARNING: auth without TLS (--no-tls) sends the password readable on the network", flush=True)
    if args.open:
        webbrowser.open(url)
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        print("\nbye")


if __name__ == "__main__":
    main()
