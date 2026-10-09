import json
import re

import pytest

from webmd.cli import is_env_file, is_loopback, read_env_file


@pytest.mark.parametrize("host,expected", [
    ("127.0.0.1", True), ("::1", True), ("localhost", True),
    ("0.0.0.0", False), ("192.168.1.5", False),
])
def test_is_loopback(host, expected):
    assert is_loopback(host) is expected


def test_is_env_file():
    assert is_env_file(".env") and is_env_file(".env.local")
    assert not is_env_file("env") and not is_env_file(".envrc")


def test_read_env_file(tmp_path):
    f = tmp_path / ".env"
    f.write_text('# comment\nexport A=1\nB="two"\nC=\'three\'\n\nnot a pair\n')
    assert read_env_file(f) == {"A": "1", "B": "two", "C": "three"}


def test_listing_and_markdown(serve):
    s = serve()
    assert s.scheme == "http"
    status, _, body = s.get("/")
    assert status == 200
    names = re.findall(r'<a href="([^"]+)">', body.split('class="listing"')[1])
    assert names == ["docs/", "README.md", "notes.txt"]  # dirs, markdown, other; no dotfiles

    status, headers, body = s.get("/README.md")
    assert status == 200 and headers["Content-Type"].startswith("text/html")
    source = re.search(r'<script id="md-source" type="application/json">(.*?)</script>', body, re.S).group(1)
    assert json.loads(source.replace("<\\/", "</")) == "# Hello\n\n</script><b>x</b>\n"

    status, _, body = s.get("/README.md?raw")
    assert status == 200 and body.startswith("# Hello")
    assert s.get("/notes.txt")[2] == "plain text\n"


def test_show_hidden_still_hides_env(serve):
    s = serve("--all")
    body = s.get("/")[2]
    assert ".hidden" in body and ".env" not in body


@pytest.mark.parametrize("path", ["/.env", "/.env?raw", "/%2Eenv", "/../../etc/passwd"])
def test_blocked_paths(serve, path):
    s = serve("--all")
    status = s.get(path)[0]
    assert status in (403, 404)
    assert s.get(path, method="HEAD")[0] in (403, 404)


def test_auth_and_tls_on_lan_bind(serve):
    s = serve("-b", "0.0.0.0")
    assert s.scheme == "https"
    assert re.search(r"SHA-256 ([0-9A-F]{2}:){31}[0-9A-F]{2}", s.output)
    assert (s.config_dir / "key.pem").stat().st_mode & 0o777 == 0o600
    assert (s.config_dir / ".env").stat().st_mode & 0o777 == 0o600

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
