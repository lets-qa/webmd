# Changelog

All notable user-facing changes to webmd are documented here.

The format follows [Keep a Changelog](https://keepachangelog.com/en/1.1.0/),
and webmd uses [Semantic Versioning](https://semver.org/). Each release lists
changes under **Security**, **Added**, **Changed**, **Fixed**, **Removed**, and
**Known limitations**, as applicable. Unreleased work goes under
`[Unreleased]` and moves to a version heading when that version is tagged.

## [Unreleased]

## [0.2.1] - Unreleased (release candidate; date set when tagged)

A maintenance release focused on security, offline use, and Windows support.
No configuration changes are needed. One behaviour change to note: an
explicit `-p PORT` that's already in use is now an error (see **Changed**).

### Security

- **Markdown is now treated as untrusted and sanitized.** Rendered HTML goes
  through [DOMPurify](https://github.com/cure53/DOMPurify) before reaching the
  page. This removes scripts, inline event handlers (`onerror`, …),
  `javascript:` and similar URLs, `<iframe>`/`<object>`/`<embed>`/`<form>`/
  `<style>`/`<template>`, `style` attributes, MathML, and unsafe SVG.
  Previously, a `<script>` or `onerror=` in any `.md` file ran in the browser
  with access to the page, including when viewed through Basic Auth.
  Syntax-highlighted code blocks pass through a second, stricter sanitizer, so
  highlighting can't become an unsanitized path. If the sanitizer can't load,
  the page shows an error instead of unsanitized HTML.
- **Content-Security-Policy on every response.**
  - Rendered pages allow only webmd's own bundled scripts, and enforce
    Trusted Types. Inline scripts and raw `innerHTML` writes are refused by the
    browser even if markup got past the sanitizer.
  - Files served from your folder, including `.html`, `.svg` and `?raw`, get a
    `sandbox` policy, so an HTML or SVG file in the tree can no longer run
    script with webmd's origin or credentials.
- **Other security headers:** `X-Content-Type-Options: nosniff`,
  `Referrer-Policy: no-referrer`, `X-Frame-Options: DENY` with CSP
  `frame-ancestors 'none'`, and `Cross-Origin-Opener-Policy: same-origin`.
- **Repeated wrong passwords are slowed down.**
  - After 5 misses from one client, each further miss doubles a wait (1s up to
    60s).
  - A global cap of about one wrong guess every 2 seconds (after a burst of 60)
    limits guessing spread across many addresses.
  - During a wait, requests get `429 Too Many Requests` with `Retry-After`,
    and the password isn't checked.
  - There is no permanent lockout, and a successful login resets the count.
  - Clients are identified by their network address, never by
    `X-Forwarded-For`.
- **`.env` files are blocked regardless of letter case or trailing dots.**
  Before, `/.ENV`, `/.Env` and similar URLs served the `.env` file on
  case-insensitive filesystems, which are the defaults on macOS and Windows.
- **Windows path syntax is rejected in URLs.** On Windows, URLs containing `\`
  or `:` (drive letters, NTFS alternate data streams such as `::$DATA`) are
  refused.

### Added

- **Offline rendering.** marked, DOMPurify, highlight.js and
  github-markdown-css are bundled in the package and served from
  `/__webmd__/static/`. No CDN is contacted, and pages render with no internet
  access. Versions are pinned and verified against npm's published SHA-512
  hashes. See `src/webmd/static/vendor/README.md`.
- **Automatic port fallback.** If the default port 8000 is busy, webmd tries
  up to 10 random ports in 49152–65535, binding each one directly, and prints
  the URL it actually got.
- **Windows support.** CI now runs the full test suite, including headless
  browser rendering tests, on Windows as well as Linux and macOS, with Python
  3.9–3.14. The built wheel is also installed and smoke-tested on each OS.
- **Homebrew formula** prepared in `packaging/homebrew/`, to be published in a
  `lets-qa/homebrew-tap` tap once 0.2.1 is on PyPI. It isn't published yet.

### Changed

- **An explicit `-p PORT` no longer starts if that port is busy.** It exits
  with `port N on HOST is already in use`. Only the default port falls back
  automatically, so an explicit port is never silently replaced. Before,
  either case crashed with a Python traceback.
- `-p` values outside 0–65535 are rejected with a usage error.
- The startup banner is printed in a single write.
- `?raw` Markdown is served as `text/plain; charset=utf-8`.

### Fixed

- **Startup errors are readable messages instead of Python tracebacks.** This
  covers a busy port, permission denied, an address not on this machine, and
  an unresolvable `-b` hostname.
- A URL containing an encoded NUL byte (`%00`) no longer crashes the request
  handler.
- `HEAD` requests now apply the same checks and headers as `GET`, and send
  correct `Content-Length` values for rendered pages.
- **macOS:** binding a specific address such as `127.0.0.1:8000` while another
  program listened on `*:8000` used to succeed and silently share that port.
  It's now detected as "in use". Restarting webmd on the same port while old
  connections are still closing still works.

### Known limitations

- **HSTS is not sent.** With a self-signed certificate, HSTS would remove the
  browser's "proceed anyway" option and stick to the hostname for a year. Set
  it at a reverse proxy that uses a real certificate.
- **Remote images are allowed** (`img-src *`), because Markdown often embeds
  badges and screenshots. Viewing a page can reveal your IP address to those
  image hosts. No referrer is sent.
- **Behind a reverse proxy, the login slowdown applies to the proxy's
  address**, so all users share one backoff. Rate-limit at the proxy.
- **Windows file permissions:** the password file and TLS key rely on the
  user profile folder's permissions. webmd doesn't set Windows ACLs.
- **Sanitization trade-offs:** `<style>`, inline `style` attributes, MathML and
  forms are removed from Markdown. In rare malformed-HTML cases (for example a
  `<form>` followed by `<noscript>`), DOMPurify drops the rest of the
  document. That's safe but loses content.
- **Basic Auth has no logout or session expiry.**

## [0.2.0] - 2026-10-09

### Added

- HTTPS, on by default for non-loopback binds. On first start webmd generates
  a self-signed certificate (EC P-256) in `~/.config/webmd/`. It covers
  localhost, the hostname, `<hostname>.local`, the LAN IP and the bind address.
  The same certificate is reused across restarts and regenerated near expiry
  or when those names change.
- The SHA-256 certificate fingerprint is printed at startup, so you can check
  it against the browser's certificate view.
- `--tls` / `--no-tls` to override the default; `--cert` / `--key` to use your
  own certificate.
- A clear "This server only speaks HTTPS" response to plain-HTTP requests on
  an HTTPS port.

### Fixed

- Certificate generation no longer fails on hostnames longer than 64
  characters.
- Startup no longer stalls on slow reverse-DNS lookups.

### Changed

- New runtime dependency: `cryptography>=42`.

## [0.1.0] - 2026-10-08 (not published to PyPI)

### Added

- Initial version: serve a directory in the browser, rendering Markdown files
  as GitHub-style pages, with directory listings and a `?raw` view.
- Binds to `127.0.0.1` by default. Non-loopback binds require HTTP Basic Auth,
  with a password generated into `~/.config/webmd/.env`.
- `.env` files are never served or listed, and path traversal is blocked.

[Unreleased]: https://github.com/lets-qa/webmd/compare/v0.2.0...HEAD
[0.2.1]: https://github.com/lets-qa/webmd/compare/v0.2.0...HEAD
[0.2.0]: https://github.com/lets-qa/webmd/releases/tag/v0.2.0
