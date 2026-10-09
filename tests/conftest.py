import base64
import os
import socket
import ssl
import subprocess
import sys
import time
import urllib.error
import urllib.request

import pytest


def free_port():
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


class Server:
    def __init__(self, proc, port, scheme, config_dir, output):
        self.proc, self.port, self.scheme = proc, port, scheme
        self.config_dir, self.output = config_dir, output

    @property
    def url(self):
        return f"{self.scheme}://127.0.0.1:{self.port}"

    def get(self, path="/", auth=None, method="GET"):
        """Return (status, headers, body). Doesn't verify certs: tests check TLS separately."""
        req = urllib.request.Request(self.url + path, method=method)
        if auth:
            req.add_header("Authorization", "Basic " + base64.b64encode(f"{auth[0]}:{auth[1]}".encode()).decode())
        ctx = ssl._create_unverified_context()
        try:
            with urllib.request.urlopen(req, context=ctx, timeout=5) as r:
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


@pytest.fixture
def site(tmp_path):
    root = tmp_path / "site"
    (root / "docs").mkdir(parents=True)
    (root / "README.md").write_text("# Hello\n\n</script><b>x</b>\n")
    (root / "docs" / "guide.md").write_text("## Guide\n")
    (root / "notes.txt").write_text("plain text\n")
    (root / ".hidden").write_text("dotfile\n")
    (root / ".env").write_text("SECRET=1\n")
    return root


@pytest.fixture
def serve(tmp_path, site):
    procs = []

    def start(*args, env=None):
        port = free_port()
        config_home = tmp_path / "config"
        # Isolate from the developer's real config and credentials.
        full_env = {k: v for k, v in os.environ.items() if not k.startswith("WEBMD_")}
        full_env.update(XDG_CONFIG_HOME=str(config_home), **(env or {}))
        log = tmp_path / f"server-{port}.log"
        proc = subprocess.Popen(
            [sys.executable, "-m", "webmd", str(site), "-p", str(port), *args],
            stdout=log.open("w"),
            stderr=subprocess.STDOUT,
            env=full_env,
        )
        procs.append(proc)
        deadline = time.time() + 15
        while time.time() < deadline:
            if proc.poll() is not None:
                pytest.fail(f"server exited early:\n{log.read_text()}")
            try:
                socket.create_connection(("127.0.0.1", port), timeout=0.2).close()
                break
            except OSError:
                time.sleep(0.1)
        else:
            pytest.fail(f"server did not start:\n{log.read_text()}")
        output = log.read_text()
        scheme = "https" if "-> https://" in output else "http"
        return Server(proc, port, scheme, config_home / "webmd", output)

    yield start
    for p in procs:
        p.terminate()
        p.wait(timeout=5)
