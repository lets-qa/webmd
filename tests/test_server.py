import json
import re

import pytest

from webmd.cli import is_env_file, is_loopback, read_env_file
from webmd.permissions import is_private


@pytest.mark.parametrize(
    "host,expected",
    [
        ("127.0.0.1", True),
        ("::1", True),
        ("localhost", True),
        ("0.0.0.0", False),
        ("192.168.1.5", False),
    ],
)
def test_is_loopback(host, expected):
    assert is_loopback(host) is expected


def test_is_env_file():
    assert is_env_file(".env") and is_env_file(".env.local")
    assert not is_env_file("env") and not is_env_file(".envrc")


@pytest.mark.parametrize("name", [".ENV", ".Env", ".env.", ".env ", ".ENV.Local"])
def test_is_env_file_case_and_trailing_variants(name):
    # macOS and Windows filesystems are case-insensitive and Windows drops trailing
    # dots/spaces, so each of these opens the real .env file there.
    assert is_env_file(name)


def test_read_env_file(tmp_path):
    f = tmp_path / ".env"
    f.write_text("# comment\nexport A=1\nB=\"two\"\nC='three'\n\nnot a pair\n")
    assert read_env_file(f) == {"A": "1", "B": "two", "C": "three"}


def test_listing_and_markdown(serve):
    s = serve()
    assert s.scheme == "http"
    status, _, body = s.get("/")
    assert status == 200
    names = re.findall(r'<a href="([^"]+)">', body.split('class="listing"')[1])
    assert names == ["docs/", "normal.md", "README.md", "xss.md", "notes.txt"]  # dirs, markdown, other; no dotfiles

    status, headers, body = s.get("/README.md")
    assert status == 200 and headers["Content-Type"].startswith("text/html")
    source = re.search(r'<script id="md-source" type="application/json">(.*?)</script>', body, re.S).group(1)
    assert json.loads(source.replace("<\\/", "</")) == "# Hello\n\n</script><b>x</b>\n"

    status, headers, body = s.get("/README.md?raw")
    assert status == 200 and body.startswith("# Hello")
    assert headers["Content-Type"].startswith("text/plain")
    assert s.get("/notes.txt")[2] == "plain text\n"


def test_show_hidden_still_hides_env(serve):
    s = serve("--all")
    body = s.get("/")[2]
    assert ".hidden" in body and ".env" not in body


@pytest.mark.parametrize(
    "path",
    ["/.env", "/.env?raw", "/%2Eenv", "/.ENV", "/.Env", "/.env%2E", "/../../etc/passwd", "/a%00.md", "/.env%00"],
)
def test_blocked_paths(serve, path):
    s = serve("--all")
    status = s.get(path)[0]
    assert status in (403, 404)
    assert s.get(path, method="HEAD")[0] in (403, 404)


def test_auth_and_tls_on_lan_bind(serve):
    s = serve("-b", "0.0.0.0")
    assert s.scheme == "https"
    assert re.search(r"SHA-256 ([0-9A-F]{2}:){31}[0-9A-F]{2}", s.output)
    assert is_private(s.config_dir / "key.pem")
    assert is_private(s.config_dir / ".env")

    status, headers, _ = s.get("/")
    assert status == 401 and headers["WWW-Authenticate"].startswith("Basic")
    assert s.get("/", auth=("webmd", "wrong"))[0] == 401
    assert s.get("/", method="HEAD")[0] == 401
    assert s.get("/README.md", auth=s.credentials())[0] == 200


def test_credentials_and_cert_reused(serve):
    first = serve("-b", "0.0.0.0")
    creds, cert = first.credentials(), (first.config_dir / "cert.pem").read_bytes()
    first.proc.terminate()
    first.proc.wait()

    second = serve("-b", "0.0.0.0")
    assert "generated" not in second.output
    assert second.credentials() == creds
    assert (second.config_dir / "cert.pem").read_bytes() == cert


def test_env_var_overrides_file(serve):
    s = serve("-b", "0.0.0.0", env={"WEBMD_USER": "me", "WEBMD_PASSWORD": "pw"})
    assert s.get("/", auth=("me", "pw"))[0] == 200


def test_no_auth_and_no_tls(serve):
    s = serve("-b", "0.0.0.0", "--no-auth", "--no-tls")
    assert s.scheme == "http"
    assert "WARNING" in s.output
    assert s.get("/")[0] == 200


def test_markdown_page_loads_only_bundled_assets(serve):
    """No CDN: every script and stylesheet is served by webmd itself."""
    s = serve()
    body = s.get("/README.md")[2]
    refs = re.findall(r'(?:src|href)="([^"]+)"', body)
    assets = [r for r in refs if r.endswith((".js", ".css"))]
    assert {"/__webmd__/static/vendor/purify.min.js", "/__webmd__/static/vendor/marked.min.js"} <= set(assets)
    assert all(r.startswith("/__webmd__/static/") for r in assets), assets
    assert "cdn." not in body and "https://" not in body
    for asset in assets:
        status, headers, data = s.get(asset)
        assert status == 200 and len(data) > 100, asset
        expected = "text/css" if asset.endswith(".css") else ("text/javascript", "application/javascript")
        assert headers["Content-Type"].startswith(expected), (asset, headers["Content-Type"])
        assert headers["X-Content-Type-Options"] == "nosniff"


def test_purify_loads_before_renderer(serve):
    body = serve().get("/README.md")[2]
    assert body.index("purify.min.js") < body.index("marked.min.js") < body.index("webmd.js")


@pytest.mark.parametrize(
    "path", ["/__webmd__/static/../cli.py", "/__webmd__/static/%2e%2e/cli.py", "/__webmd__/static/nope.js"]
)
def test_static_route_is_confined(serve, path):
    assert serve().get(path)[0] == 404


def test_static_route_does_not_shadow_served_files(serve, site):
    (site / "__webmd__").mkdir()
    (site / "__webmd__" / "mine.txt").write_text("user file\n")
    s = serve()
    assert s.get("/__webmd__/mine.txt")[2] == "user file\n"


def test_security_headers_on_rendered_page(serve):
    s = serve()
    _, headers, _ = s.get("/README.md")
    csp = headers["Content-Security-Policy"]
    assert "script-src 'self'" in csp and "'unsafe-inline'" not in csp.split("script-src")[1].split(";")[0]
    assert "default-src 'none'" in csp and "frame-ancestors 'none'" in csp
    assert "object-src 'none'" in csp and "base-uri 'none'" in csp
    assert "require-trusted-types-for 'script'" in csp
    assert headers["X-Content-Type-Options"] == "nosniff"
    assert headers["Referrer-Policy"] == "no-referrer"
    assert headers["X-Frame-Options"] == "DENY"
    assert "Strict-Transport-Security" not in headers  # see README: HSTS is a deployment decision


@pytest.mark.parametrize("path", ["/README.md?raw", "/notes.txt", "/", "/docs", "/missing"])
def test_security_headers_on_every_response(serve, path):
    _, headers, _ = serve().get(path)
    assert headers["X-Content-Type-Options"] == "nosniff"
    assert "frame-ancestors 'none'" in headers["Content-Security-Policy"]


def test_raw_files_are_sandboxed(serve, site):
    (site / "page.html").write_text("<script>alert(1)</script>")
    (site / "pic.svg").write_text('<svg xmlns="http://www.w3.org/2000/svg"><script>alert(1)</script></svg>')
    s = serve()
    for path in ("/page.html", "/pic.svg", "/README.md?raw"):
        csp = s.get(path)[1]["Content-Security-Policy"]
        assert "sandbox" in csp and "script-src" not in csp, path


def test_pdf_not_sandboxed(serve, site):
    # Chrome won't display PDFs under a sandbox CSP; PDFs can't script this origin anyway.
    (site / "doc.pdf").write_bytes(b"%PDF-1.1\n%%EOF\n")
    headers = serve().get("/doc.pdf")[1]
    assert headers["Content-Security-Policy"] == "frame-ancestors 'none'"


def test_security_headers_with_auth(serve):
    s = serve("-b", "0.0.0.0")
    for status, headers, _ in (s.get("/"), s.get("/README.md", auth=s.credentials())):
        assert status in (200, 401)
        assert headers["X-Content-Type-Options"] == "nosniff"
        assert "frame-ancestors 'none'" in headers["Content-Security-Policy"]


def test_head_matches_get_without_body(serve):
    s = serve()
    status, headers, body = s.get("/README.md", method="HEAD")
    assert status == 200 and body == ""
    assert int(headers["Content-Length"]) == len(s.get("/README.md")[2].encode())
