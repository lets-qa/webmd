# webmd

[![CI](https://github.com/lets-qa/webmd/actions/workflows/ci.yml/badge.svg)](https://github.com/lets-qa/webmd/actions/workflows/ci.yml)

Serve any directory in your browser, with Markdown files rendered as proper web
pages. Point it at a folder of notes, docs, or a repo and browse it like a
website: folders become clickable listings, `.md` files render GitHub-style,
and everything else (images, PDFs, source files) is served as-is.

Pure Python, with a single dependency
([`cryptography`](https://cryptography.io/), used for HTTPS certificates).

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
- **Safe by default**:
  - binds to `127.0.0.1` (localhost only) unless you say otherwise
  - auto-enables **HTTPS** and **HTTP Basic Auth** whenever it's exposed
    beyond localhost, with a generated self-signed certificate and password
  - refuses paths that escape the served directory (`../`, symlinks out)
  - never serves or lists `.env` / `.env.*` files
- **No build step, no config**. Works on Python 3.9+.

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
| `-p`, `--port` | `8000` | Port to listen on |
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
webmd -p 9000 --open          # different port, open the browser
webmd --all                   # include dotfiles in listings
python -m webmd               # same as `webmd`, no console script needed
```

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

| File | Mode | Contents |
|---|---|---|
| `~/.config/webmd/.env` | `0600` | Basic Auth username and password |
| `~/.config/webmd/cert.pem` | `0644` | Self-signed certificate |
| `~/.config/webmd/key.pem` | `0600` | Certificate private key |

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

## How it works

webmd extends Python's built-in `http.server`. In HTTPS mode the TLS
handshake runs on each connection's own thread, so a slow or stalled client
can't block anyone else. When a request hits a Markdown
file, the server embeds the raw source in a small HTML page, and the browser
renders it with [marked](https://marked.js.org/), styles it with
[github-markdown-css](https://github.com/sindresorhus/github-markdown-css), and
highlights code with [highlight.js](https://highlightjs.org/).

Things to be aware of:

- Those libraries load from the jsDelivr CDN, so **viewing rendered pages needs
  internet access**. Offline, you'll see an unstyled page.
- **Raw HTML inside Markdown is rendered as-is.** That's fine for your own
  files, but don't serve untrusted Markdown to other people.

## Development

```bash
git clone git@github.com:lets-qa/webmd.git
cd webmd
uv tool install --editable .   # `webmd` now runs your working copy
uv run --group dev pytest      # run the test suite
uv run --group dev ruff check . # lint
uv build                       # build sdist + wheel into dist/
```

The version lives in `src/webmd/__init__.py`.

### CI

Every push to `main` and every pull request runs
[`.github/workflows/ci.yml`](.github/workflows/ci.yml):

- **lint:** `ruff check`
- **test:** pytest on Linux and macOS, Python 3.9 through 3.14
- **build:** builds the sdist and wheel, runs `twine check`, and smoke-tests
  the installed `webmd` command

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
# bump __version__ in src/webmd/__init__.py, commit, merge to main, then:
git tag v0.2.0
git push origin v0.2.0
```

The workflow checks the tag matches `__version__`, runs the tests, builds,
publishes to PyPI, and creates a GitHub release with the built files attached.

## License

[MIT](LICENSE)
