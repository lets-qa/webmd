import base64
import os
import re
import socket
import ssl
import subprocess
import sys
import time
import urllib.error
import urllib.request
from pathlib import Path

import pytest

FIXTURES = Path(__file__).parent / "fixtures"
URL_LINE = re.compile(r"-> (?P<scheme>https?)://[^ ]*:(?P<port>\d+)/")


def free_port():
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


class NoRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, *args, **kwargs):
        return None  # surface 301s to the test instead of following them


class Server:
    def __init__(self, proc, port, scheme, config_dir, output):
        self.proc, self.port, self.scheme = proc, port, scheme
        self.config_dir, self.output = config_dir, output

    @property
    def url(self):
        return f"{self.scheme}://127.0.0.1:{self.port}"

    def get(self, path="/", auth=None, method="GET", headers=None):
        """Return (status, headers, body). Doesn't verify certs: tests check TLS separately."""
        req = urllib.request.Request(self.url + path, method=method, headers=headers or {})
        if auth:
            req.add_header("Authorization", "Basic " + base64.b64encode(f"{auth[0]}:{auth[1]}".encode()).decode())
        ctx = ssl._create_unverified_context()
        try:
            opener = urllib.request.build_opener(urllib.request.HTTPSHandler(context=ctx), NoRedirect)
            with opener.open(req, timeout=5) as r:
                return r.status, r.headers, r.read().decode()
        except urllib.error.HTTPError as e:
            return e.code, e.headers, e.read().decode()

    def credentials(self):
        vals = dict(
            line.split("=", 1)
            for line in (self.config_dir / ".env").read_text().splitlines()
            if "=" in line and not line.startswith("#")
        )
        return vals["WEBMD_USER"], vals["WEBMD_PASSWORD"]


def _accepts(port):
    try:
        socket.create_connection(("127.0.0.1", port), timeout=0.5).close()
        return True
    except OSError:
        return False


@pytest.fixture
def site(tmp_path):
    root = tmp_path / "site"
    (root / "docs").mkdir(parents=True)
    # write_bytes, not write_text: on Windows text mode turns "\n" into "\r\n".
    (root / "README.md").write_bytes(b"# Hello\n\n</script><b>x</b>\n")
    for fixture in FIXTURES.glob("*.md"):
        (root / fixture.name).write_bytes(fixture.read_bytes())
    (root / "docs" / "guide.md").write_bytes(b"## Guide\n")
    (root / "notes.txt").write_bytes(b"plain text\n")
    (root / ".hidden").write_bytes(b"dotfile\n")
    (root / ".env").write_bytes(b"SECRET=1\n")
    return root


@pytest.fixture
def serve(tmp_path, site):
    procs = []

    def start(*args, env=None, port=None, port_flag=True):
        port = port or free_port()
        config_home = tmp_path / "config"
        # Isolate from the developer's real config and credentials.
        full_env = {k: v for k, v in os.environ.items() if not k.startswith("WEBMD_")}
        full_env["PYTHONUNBUFFERED"] = "1"
        full_env.update(XDG_CONFIG_HOME=str(config_home), **(env or {}))
        log = tmp_path / f"server-{port}.log"
        port_args = ["-p", str(port)] if port_flag and "-p" not in args else []
        proc = subprocess.Popen(
            [sys.executable, "-m", "webmd", str(site), *port_args, *args],
            stdout=log.open("w"),
            stderr=subprocess.STDOUT,
            env=full_env,
        )
        procs.append(proc)
        # Take the port from the banner (webmd may have fallen back to another
        # one; the banner is printed in a single write), then wait until the
        # server accepts connections.
        deadline = time.time() + 20
        match = None
        while time.time() < deadline:
            if proc.poll() is not None:
                pytest.fail(f"server exited early:\n{log.read_text(encoding='utf-8')}")
            match = match or URL_LINE.search(log.read_text(encoding="utf-8"))
            if match and _accepts(int(match["port"])):
                break
            time.sleep(0.05)
        else:
            pytest.fail(f"server did not start:\n{log.read_text(encoding='utf-8')}")
        output = log.read_text(encoding="utf-8")
        return Server(proc, int(match["port"]), match["scheme"], config_home / "webmd", output)

    yield start
    for p in procs:
        p.terminate()
        p.wait(timeout=5)
