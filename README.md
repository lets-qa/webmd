# webmd

[![CI](https://github.com/lets-qa/webmd/actions/workflows/ci.yml/badge.svg)](https://github.com/lets-qa/webmd/actions/workflows/ci.yml)

Serve any directory in your browser, with Markdown files rendered as proper web
pages. Point it at a folder of notes, docs, or a repo and browse it like a
website: folders become clickable listings, `.md` files render GitHub-style,
and everything else (images, PDFs, source files) is served as-is.

Pure Python, with a single dependency
([`cryptography`](https://cryptography.io/), used for HTTPS certificates).
Everything the browser needs is bundled, so it works fully offline.

```bash
cd ~/notes
webmd --open
```

## Features

- **GitHub-flavored Markdown rendering**: headings, tables, task lists,
  strikethrough, autolinks, and fenced code blocks with syntax highlighting.
- **Automatic light/dark mode** that follows your OS setting.
- **Directory browsing** with breadcrumbs, sorted folders → Markdown → other
  files. Dotfiles are hidden unless you pass `--all`.
- **Heading anchors**: every heading gets an `id`, so `page.md#some-section`
  links work, just like on GitHub.
- **Raw view**: append `?raw` to any Markdown URL (or click **raw** in the
  header) to see the source.
- **Static files served as-is**, so images and relative links inside your
  Markdown just work.
- **Works offline**: the renderer, sanitizer, styles and syntax highlighter
  ship inside the package. No CDN, no internet required.
- **Picks a free port**: if port 8000 is busy, it starts on a random free port
  and prints the URL.
- **Safe by default**:
  - binds to `127.0.0.1` (localhost only) unless you say otherwise
  - auto-enables **HTTPS** and **HTTP Basic Auth** whenever it's exposed
    beyond localhost, with a generated self-signed certificate and password,
    and slows down repeated wrong passwords
  - **treats Markdown as untrusted**: HTML inside `.md` files is sanitized,
    so scripts, event handlers and `javascript:` links never run
  - sends a strict Content-Security-Policy and other security headers
  - refuses paths that escape the served directory (`../`, symlinks out)
  - never serves or lists `.env` / `.env.*` files
- **No build step, no config**. Works on Python 3.9+ on Linux, macOS and
  Windows.

## Install

webmd is a command-line tool, so the recommended way is an isolated tool
install with [uv](https://docs.astral.sh/uv/) or [pipx](https://pipx.pypa.io/).

From PyPI:

```bash
uv tool install webmd
# or
pipx install webmd
# or, into the current environment
pip install webmd
```

Straight from GitHub:

```bash
uv tool install git+https://github.com/lets-qa/webmd.git
# or
pipx install git+https://github.com/lets-qa/webmd.git
```

From a local clone (add `--editable` to pick up code changes without
reinstalling):

```bash
git clone git@github.com:lets-qa/webmd.git
cd webmd
uv tool install .
```

If your shell says `webmd: command not found` after installing, the tool
directory isn't on your `PATH`. Run `uv tool update-shell` (or
`pipx ensurepath`) and open a new terminal.

Check it worked:

```bash
webmd --version
```

To upgrade or remove:

```bash
uv tool upgrade webmd     # pipx upgrade webmd
uv tool uninstall webmd   # pipx uninstall webmd
```

## Usage

```
webmd [DIR] [-p PORT] [-b BIND] [--all] [--open] [--no-auth] [--env-file PATH]
      [--tls | --no-tls] [--cert PATH --key PATH]
```

| Option | Default | Description |
|---|---|---|
| `DIR` | `.` | Directory to serve |
| `-p`, `--port` | `8000` | Port to listen on. See [Ports](#ports) |
| `-b`, `--bind` | `127.0.0.1` | Address to bind. Any non-loopback address turns on HTTPS and auth |
| `--all` | off | Show dotfiles in directory listings |
| `--open` | off | Open your browser when the server starts |
| `--no-auth` | off | Disable Basic Auth even when bound to a non-loopback address |
| `--env-file` | `~/.config/webmd/.env` | Where credentials are read from / generated to |
| `--tls` / `--no-tls` | on if non-loopback | Force HTTPS on (even on localhost) or off |
| `--cert`, `--key` | self-signed in `~/.config/webmd/` | Use your own certificate and private key (PEM) |
| `-V`, `--version` | | Print the version and exit |

### Examples

```bash
webmd                         # serve the current dir at http://127.0.0.1:8000
webmd ~/projects/docs         # serve a specific directory
webmd -p 9000 --open          # this exact port (error if it's busy), open the browser
webmd --all                   # include dotfiles in listings
python -m webmd               # same as `webmd`, no console script needed
```

### Ports

With no `-p`, webmd tries port **8000**. If that's taken, it tries up to 10
random ports in the dynamic range **49152–65535**, never repeating one. It
binds each candidate directly (no separate "is it free?" check that could race
with another program) and prints the URL it actually got:

```
  port 8000 is in use; trying a random port in 49152-65535
webmd serving /Users/you/notes
  -> http://127.0.0.1:53817/  (Ctrl-C to stop)
```

If you pass **`-p` explicitly, that port is used or webmd exits** with an error.
It never silently moves to a different port you didn't ask for, which matters
for bookmarks, firewall rules and reverse-proxy configs. `-p 0` asks the OS for
any free port.

Only "port already in use" triggers a fallback. Other errors, such as
permission denied, an address that isn't on this machine, or a hostname that
doesn't resolve, stop immediately with a message. The bind address (`-b`) is
always kept as-is.

### URLs

| URL | What you get |
|---|---|
| `/` or `/some/dir/` | Directory listing |
| `/notes/todo.md` | Rendered Markdown page |
| `/notes/todo.md?raw` | Raw Markdown source |
| `/notes/todo.md#next-steps` | Rendered page, scrolled to that heading |
| `/images/diagram.png` | The file itself |

## Sharing on your network (HTTPS + auth)

By default webmd only listens on localhost, so nothing else on your network
can reach it. To share it, bind to another address. **HTTPS and Basic Auth
then both turn on automatically**:

```bash
webmd -b 0.0.0.0              # reachable from your LAN at https://, login required
```

**First start:** webmd generates a self-signed certificate and a random
password into `~/.config/webmd/` (honours `$XDG_CONFIG_HOME`), and prints both:

```
webmd serving /Users/you/notes
  -> https://0.0.0.0:8000/  (Ctrl-C to stop)
  tls: generated self-signed certificate /Users/you/.config/webmd/cert.pem
       SHA-256 79:6F:B0:A8:...:67:51:53
  auth: generated credentials in /Users/you/.config/webmd/.env
        user=webmd password=<random-generated-password>
```

| File | Mode (Linux/macOS) | Contents |
|---|---|---|
| `~/.config/webmd/.env` | `0600` | Basic Auth username and password |
| `~/.config/webmd/cert.pem` | `0644` | Self-signed certificate |
| `~/.config/webmd/key.pem` | `0600` | Certificate private key |

On **Linux and macOS**, the password file and private key are created
owner-only (`0600`) from the start, and replaced atomically when rewritten.

On **Windows**, file modes don't exist. The files go in
`%USERPROFILE%\.config\webmd\` and inherit that folder's permissions. On a
standard install, your profile folder is readable only by you and
administrators. webmd doesn't change Windows ACLs itself, so if you point
`XDG_CONFIG_HOME` or `--env-file` somewhere shared, protect that folder
yourself.

**Every start after that** reuses the existing file. Edit it to choose your
own credentials; changes apply on the next restart:

```bash
# ~/.config/webmd/.env
WEBMD_USER=me
WEBMD_PASSWORD=correct-horse-battery-staple
```

**Credential precedence**, highest first:

1. `WEBMD_USER` / `WEBMD_PASSWORD` environment variables
2. The env file (`--env-file PATH`, default `~/.config/webmd/.env`)
3. Generated on first boot (user `webmd`, random password)

**Turning auth off** for a trusted network:

```bash
webmd -b 0.0.0.0 --no-auth    # prints a warning on start
```

**Wrong-password protection.** Each client gets 5 free wrong attempts. After
that, each further miss doubles a wait (1s, 2s, 4s, … up to 60s). During
the wait, webmd answers `429 Too Many Requests` with a `Retry-After` header and
doesn't check the password at all, so guessing faster gains nothing.
Across all clients combined, wrong guesses are also capped at about one every
two seconds once a burst of 60 is used up. There is no permanent lockout: waits
expire on their own, the right password works again once the wait is over, and
a successful login resets that client's count. Clients are identified by their
real network address, never by headers like `X-Forwarded-For`, which a client
could fake. Passwords are never logged.

### HTTPS and the self-signed certificate

The generated certificate covers `localhost`, `127.0.0.1`, `::1`, your
hostname (plus `<hostname>.local`), your LAN IP, and the `-b` address. It's
valid for 825 days and is **regenerated automatically** when it's within 30
days of expiry or no longer covers your current hostname or IP (for example
after you join a different network). Otherwise the same certificate is reused,
so its fingerprint stays stable.

Because it's self-signed, **browsers will warn "Your connection is not
private"** the first time on each device. That's expected:

1. Compare the SHA-256 fingerprint printed at startup with the one the browser
   shows (click the warning → view certificate).
2. If they match, proceed. If they don't, something is intercepting the
   connection, so don't continue.

Visiting the `http://` URL of an HTTPS server returns a short
"This server only speaks HTTPS" message.

webmd does **not** send `Strict-Transport-Security` (HSTS). With a self-signed
certificate, HSTS would make the browser refuse the "proceed anyway" option,
and it would stick to the hostname for a year, breaking any later plain-HTTP
use of `localhost`. If you put webmd behind a reverse proxy with a real
certificate, set HSTS there.

**No warnings, using your own certificate.** With
[mkcert](https://github.com/FiloSottile/mkcert) you can create a certificate
your own devices trust:

```bash
mkcert -install
mkcert localhost 192.168.1.20 my-laptop.local
webmd -b 0.0.0.0 --cert localhost+2.pem --key localhost+2-key.pem
```

Any PEM certificate and key work, including ones from Let's Encrypt.

**Other combinations:**

```bash
webmd --tls                   # HTTPS on localhost too
webmd -b 0.0.0.0 --no-tls     # plain HTTP on the LAN (warns: password not encrypted)
```

> **Note:** with `--no-tls`, Basic Auth credentials travel base64-encoded,
> not encrypted. Keep TLS on unless something else (a reverse proxy,
> Tailscale, cloudflared) already provides HTTPS in front of webmd.

## Security model

webmd assumes **the Markdown files may be untrusted**: a cloned repo, a
downloaded doc, or files someone else can write to.

**Markdown rendering.** Markdown is turned into HTML in the browser by
[marked](https://marked.js.org/), then **sanitized by
[DOMPurify](https://github.com/cure53/DOMPurify)** before anything reaches the
page. This removes:

- `<script>`, inline event handlers (`onerror`, `onload`, …), and `javascript:`,
  `vbscript:` and `data:text/html` links
- `<iframe>`, `<object>`, `<embed>`, `<form>`, `<style>`, `<base>`, `<meta>`,
  `<template>`, and `style="..."` attributes (no page-wide restyling or fake
  login overlays)
- MathML, and dangerous SVG content (scripts, event handlers, `<use>`,
  `<foreignObject>`, animation elements)

It keeps normal Markdown and safe HTML: tables, task lists, `<details>`,
`<kbd>`, `<sub>`/`<sup>`, `<mark>`, images, links, and plain inline SVG. Code
blocks are highlighted by [highlight.js](https://highlightjs.org/), and its
output goes through a second, stricter sanitizer pass that only allows
`<span class>`. If the sanitizer fails to load, the page shows an error
instead of unsanitized HTML.

> Sanitizing can't be turned off. There's no `--unsafe-html` flag, because
> nothing in webmd needs raw HTML to run. If something you rely on is being
> stripped, please open an issue.

**Security headers.** Every response gets:

| Header | Value | Why |
|---|---|---|
| `Content-Security-Policy` | rendered pages: `script-src 'self'`, Trusted Types, `default-src 'none'`, … | Even if a sanitizer bug let markup through, inline scripts, `eval`-style string execution and raw `innerHTML` writes are refused by the browser |
| `Content-Security-Policy` | files from your folder: `sandbox` | An `.html` or `.svg` file in the folder can't run script with webmd's origin or credentials. PDFs are exempt because Chrome won't show them sandboxed, and PDFs can't run script on the page anyway |
| `X-Content-Type-Options` | `nosniff` | The browser can't reinterpret a file as a different type |
| `Referrer-Policy` | `no-referrer` | Your file paths don't leak to external sites through links or images |
| `X-Frame-Options` + CSP `frame-ancestors` | `DENY` / `'none'` | webmd pages can't be embedded by other sites (clickjacking) |
| `Cross-Origin-Opener-Policy` | `same-origin` | Isolates the page from windows opened by other sites |

Images may still load from any URL (`img-src *`), because Markdown commonly
embeds remote badges and screenshots. Those requests reveal your IP to the
image host, but carry no referrer and can't run code.

**Behind a reverse proxy** (nginx, Caddy, Traefik, …):

- **Keep the headers.** Make sure the proxy passes webmd's headers through. If
  it adds its own `Content-Security-Policy`, browsers enforce *both* policies.
  Don't add a weaker one expecting it to replace webmd's.
- **Login slowdown counts the proxy, not the user.** The backoff is keyed on
  the network address webmd sees, which behind a proxy is the proxy's address.
  All users then share one backoff, so put rate limiting at the proxy instead.
  webmd ignores `X-Forwarded-For` on purpose, because a client could fake it.
- **Set HSTS at the proxy** if it terminates TLS with a real certificate.

**Remaining limits.** Basic Auth has no logout and no session expiry. Anyone
who can read `~/.config/webmd/.env` can log in. Remote images (see above) can
reveal that a page was viewed.

## How it works

webmd extends Python's built-in `http.server`. In HTTPS mode the TLS
handshake runs on each connection's own thread, so a slow or stalled client
can't block anyone else. When a request hits a Markdown file, the server
embeds the raw source in a small HTML page. The browser renders it with
marked, sanitizes it with DOMPurify, styles it with
[github-markdown-css](https://github.com/sindresorhus/github-markdown-css), and
highlights code with highlight.js.

All four libraries are **bundled in the package** (`webmd/static/vendor/`) and
served from webmd's own `/__webmd__/static/` path, so pages render with no
internet access at all. They are pinned to exact versions and verified
against npm's published SHA-512 hashes by
[`scripts/vendor_assets.py`](scripts/vendor_assets.py). See
[`src/webmd/static/vendor/README.md`](src/webmd/static/vendor/README.md) for
versions and licenses.

The only network access a page can make is loading images that your Markdown
links to on other sites.

## Development

```bash
git clone git@github.com:lets-qa/webmd.git
cd webmd
uv tool install --editable .              # `webmd` now runs your working copy
uv run --group dev playwright install chromium   # once: browser for the rendering tests
uv run --group dev pytest                 # run the test suite
uv run --group dev ruff check .           # lint
uv run --group dev ruff format .          # format (CI runs `ruff format --check .`)
uv build                                  # build sdist + wheel into dist/
uv run --no-project python scripts/check_dist.py dist   # check both contain the bundled assets
```

The browser tests in `tests/test_render_browser.py` run the real rendering
pipeline in headless Chromium, with all non-webmd network access blocked.
Locally they're skipped if Chromium isn't installed. CI sets
`WEBMD_REQUIRE_BROWSER=1` so a missing browser fails the job instead.

To update a bundled library, change its pin (URL and npm `integrity`) in
`scripts/vendor_assets.py` and run `python scripts/vendor_assets.py`. CI checks
that the committed files match the pins.

The version lives in `src/webmd/__init__.py`. User-facing changes go in
[`CHANGELOG.md`](CHANGELOG.md).

### CI

Every push to `main` and every pull request runs
[`.github/workflows/ci.yml`](.github/workflows/ci.yml):

- **lint:** `ruff check`, `ruff format --check`, and a check that the bundled
  assets match their pinned upstream versions
- **test:** pytest, including the headless-Chromium rendering tests, on
  **Linux, macOS and Windows** with Python 3.9 through 3.14 (18 jobs)
- **build:** builds the sdist and wheel, runs `twine check --strict`, and
  checks both contain every bundled asset
- **install:** installs the built wheel into a clean environment on each of
  the three OSes, starts it, and fetches a page and all of its assets

### Releasing

Releases publish to PyPI from
[`.github/workflows/release.yml`](.github/workflows/release.yml) using
[Trusted Publishing](https://docs.pypi.org/trusted-publishers/), so no API
token is stored in GitHub.

**One-time setup:**

1. On PyPI, go to *Your projects → Publishing* (or, before the first release,
   *Account → Publishing → Add a new pending publisher*) and add:
   - Owner: `lets-qa`, Repository: `webmd`
   - Workflow: `release.yml`, Environment: `pypi`
2. In GitHub, create an environment named `pypi` under *Settings →
   Environments*. Optionally add yourself as a required reviewer so each
   publish waits for approval.

**Each release:**

```bash
# bump __version__ in src/webmd/__init__.py, move the CHANGELOG's
# "Unreleased" notes under the new version, commit, merge to main, then:
git tag vX.Y.Z
git push origin vX.Y.Z
```

The workflow checks the tag matches `__version__`, runs the tests, builds,
publishes to PyPI, and creates a GitHub release with the built files attached.

### Homebrew

A Homebrew formula is prepared in
[`packaging/homebrew/`](packaging/homebrew/), to be published through a
dedicated tap after a release is on PyPI. See that directory's README.

## License

webmd itself is [MIT](LICENSE). The bundled frontend libraries keep their own
licenses: marked (MIT), DOMPurify (MPL-2.0 or Apache-2.0), highlight.js
(BSD-3-Clause), and github-markdown-css (MIT). Their license texts ship in
`src/webmd/static/vendor/`.
