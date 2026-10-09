"""Fetch the pinned frontend assets into src/webmd/static/vendor/.

Run from the repo root:  python scripts/vendor_assets.py [--check]

Each file comes from a pinned npm tarball whose SHA-512 must match the
registry-published integrity value, so the vendored files are reproducible
and auditable. --check verifies the committed files instead of writing.
"""

import base64
import hashlib
import io
import sys
import tarfile
import urllib.request
from pathlib import Path

VENDOR = Path(__file__).resolve().parent.parent / "src" / "webmd" / "static" / "vendor"

# (tarball URL, npm integrity, {path in tarball: vendored filename})
PACKAGES = [
    (
        "https://registry.npmjs.org/marked/-/marked-12.0.2.tgz",
        "sha512-qXUm7e/YKFoqFPYPa3Ukg9xlI5cyAtGmyEIzMfW//m6kXwCy2Ps9DYf5ioijFKQ8qyuscrHoY04iJGctu2Kg0Q==",
        {"package/marked.min.js": "marked.min.js", "package/LICENSE.md": "LICENSE.marked.md"},
    ),
    (
        "https://registry.npmjs.org/dompurify/-/dompurify-3.4.16.tgz",
        "sha512-sqo+pNp3qRhCIpbgRi1y8Tgk27Bo2Ry7w0dC1NBeNTdZChWjz9Xb/KOoZbRP/R6pQZ80Qw8YhXw13hWWBbMRnQ==",
        {
            "package/dist/purify.min.js": "purify.min.js",
            "package/LICENSE": "LICENSE.dompurify",
            "package/LICENSE-MPL": "LICENSE-MPL.dompurify",
        },
    ),
    (
        "https://registry.npmjs.org/@highlightjs/cdn-assets/-/cdn-assets-11.12.0.tgz",
        "sha512-KvOKXODaiFmId9xaq3xc5xCL66wVLUuOngDbO9B/kewbFTqdGbn2nJxNhN3H5R1cgDTVj6R8vH0zgiNDEGjpDw==",
        {
            "package/highlight.min.js": "highlight.min.js",
            "package/styles/github.min.css": "highlight-github.min.css",
            "package/styles/github-dark.min.css": "highlight-github-dark.min.css",
            "package/LICENSE": "LICENSE.highlightjs",
        },
    ),
    (
        "https://registry.npmjs.org/github-markdown-css/-/github-markdown-css-5.9.0.tgz",
        "sha512-tmT5sY+zvg2302XLYEfH2mtkViIM1SWf2nvYoF5N1ZsO0V6B2qZTiw3GOzw4vpjLygK/KG35qRlPFweHqfzz5w==",
        {"package/github-markdown.css": "github-markdown.css", "package/license": "LICENSE.github-markdown-css"},
    ),
]


def fetch(url, integrity):
    data = urllib.request.urlopen(url, timeout=60).read()
    algo, _, expected = integrity.partition("-")
    actual = base64.b64encode(hashlib.new(algo, data).digest()).decode()
    if actual != expected:
        sys.exit(f"integrity mismatch for {url}")
    return data


def main():
    check = "--check" in sys.argv
    VENDOR.mkdir(parents=True, exist_ok=True)
    stale = []
    for url, integrity, files in PACKAGES:
        with tarfile.open(fileobj=io.BytesIO(fetch(url, integrity)), mode="r:gz") as tar:
            for member, name in files.items():
                content = tar.extractfile(member).read()
                dest = VENDOR / name
                if check:
                    if not dest.exists() or dest.read_bytes() != content:
                        stale.append(name)
                else:
                    dest.write_bytes(content)
                    print(f"wrote {dest.relative_to(VENDOR.parent.parent.parent.parent)}")
    if stale:
        sys.exit(f"vendored files differ from pinned upstream: {', '.join(stale)}")
    if check:
        print("vendored assets match pinned upstream")


if __name__ == "__main__":
    main()
