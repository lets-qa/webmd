# webmd

Serve any directory in your browser, with Markdown files rendered as proper web
pages. Point it at a folder of notes, docs, or a repo and browse it like a
website: folders become clickable listings, `.md` files render GitHub-style,
and everything else (images, PDFs, source files) is served as-is.

Zero dependencies: just the Python standard library.

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
  - auto-enables HTTP Basic Auth whenever it's exposed beyond localhost, with
    a generated password
  - refuses paths that escape the served directory (`../`, symlinks out)
  - never serves or lists `.env` / `.env.*` files
- **No build step, no config, no dependencies**. Works on Python 3.9+.

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
```

| Option | Default | Description |
|---|---|---|
| `DIR` | `.` | Directory to serve |
| `-p`, `--port` | `8000` | Port to listen on |
| `-b`, `--bind` | `127.0.0.1` | Address to bind. Any non-loopback address turns on auth |
| `--all` | off | Show dotfiles in directory listings |
| `--open` | off | Open your browser when the server starts |
| `--no-auth` | off | Disable Basic Auth even when bound to a non-loopback address |
| `--env-file` | `~/.config/webmd/.env` | Where credentials are read from / generated to |
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

## Sharing on your network (auth)

By default webmd only listens on localhost, so nothing else on your network
can reach it. To share it, bind to another address. Basic Auth then turns on
automatically:

```bash
webmd -b 0.0.0.0              # reachable from your LAN, login required
```

**First start:** webmd generates a random password, writes it to
`~/.config/webmd/.env` (file mode `0600`; honours `$XDG_CONFIG_HOME`), and
prints it:

```
webmd serving /Users/you/notes
  -> http://0.0.0.0:8000/  (Ctrl-C to stop)
  auth: generated credentials in /Users/you/.config/webmd/.env
        user=webmd password=<random-generated-password>
```

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

> **Note:** Basic Auth sends credentials base64-encoded, not encrypted. On
> untrusted networks, put webmd behind HTTPS (e.g. Caddy, Tailscale, or
> cloudflared) rather than exposing it directly.

## How it works

webmd extends Python's built-in `http.server`. When a request hits a Markdown
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
uv build                       # build sdist + wheel into dist/
```

The version lives in `src/webmd/__init__.py`.

## License

[MIT](LICENSE)
