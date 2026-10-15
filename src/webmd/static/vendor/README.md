# Vendored frontend assets

These files are bundled so webmd works offline and never loads code from a CDN.
They are fetched from pinned npm tarballs, verified against the registry's
SHA-512 integrity values, by `scripts/vendor_assets.py`. Do not edit them by
hand: change the pins in that script and re-run it.

| File | Package | Version | License |
|---|---|---|---|
| `marked.min.js` | [marked](https://github.com/markedjs/marked) | 12.0.2 | MIT (`LICENSE.marked.md`) |
| `purify.min.js` | [DOMPurify](https://github.com/cure53/DOMPurify) | 3.4.16 | MPL-2.0 OR Apache-2.0 (`LICENSE-MPL.dompurify`, `LICENSE.dompurify`) |
| `highlight.min.js`, `highlight-github.min.css`, `highlight-github-dark.min.css` | [highlight.js](https://github.com/highlightjs/highlight.js) (`@highlightjs/cdn-assets`) | 11.12.0 | BSD-3-Clause (`LICENSE.highlightjs`) |
| `github-markdown.css` | [github-markdown-css](https://github.com/sindresorhus/github-markdown-css) | 5.9.0 | MIT (`LICENSE.github-markdown-css`) |
