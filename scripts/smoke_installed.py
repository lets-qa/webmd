"""Smoke-test an *installed* webmd (run with the target venv's python, outside the repo).

Starts `python -m webmd` on a temp dir with no network beyond loopback, then
checks the rendered page, every bundled asset it references, and the security
headers. Used by CI on Linux, macOS and Windows.
"""

import os
import re
import subprocess
import sys
import tempfile
import time
import urllib.request
from pathlib import Path

import webmd

pkg = Path(webmd.__file__).resolve().parent
assert "site-packages" in str(pkg), f"testing the repo, not the installed package: {pkg}"
print(f"webmd {webmd.__version__} installed at {pkg}")

with tempfile.TemporaryDirectory() as site:
    Path(site, "README.md").write_text("# Smoke\n\n```python\nprint(1)\n```\n", encoding="utf-8")
    cfg = Path(site, "cfg")
    proc = subprocess.Popen(
        [sys.executable, "-m", "webmd", site, "-p", "0", "--no-auth"],
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        text=True,
        env={**os.environ, "XDG_CONFIG_HOME": str(cfg), "PYTHONUNBUFFERED": "1"},
    )
    try:
        line = ""
        deadline = time.time() + 30
        while time.time() < deadline and "->" not in line:
            line = proc.stdout.readline()
            print(line, end="")
        url = re.search(r"-> (\S+)", line).group(1)

        with urllib.request.urlopen(url + "README.md", timeout=10) as r:
            page = r.read().decode()
            headers = r.headers
        assert "Content-Security-Policy" in headers and headers["X-Content-Type-Options"] == "nosniff"
        assets = re.findall(r'(?:src|href)="(/__webmd__/static/[^"]+)"', page)
        assert len(assets) >= 7, assets
        assert "cdn." not in page
        for asset in assets:
            with urllib.request.urlopen(url.rstrip("/") + asset, timeout=10) as r:
                size = len(r.read())
            assert size > 100, (asset, size)
            print(f"  ok {asset} ({size} bytes)")
        print("installed package serves all bundled assets")
    finally:
        proc.terminate()
        proc.wait(timeout=10)
