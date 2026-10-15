"""Verify built distributions contain every bundled frontend asset.

Usage: python scripts/check_dist.py dist/
Exits non-zero (listing what's missing) if the wheel or sdist lacks a file
that exists under src/webmd/static/.
"""

import sys
import tarfile
import zipfile
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
STATIC = ROOT / "src" / "webmd" / "static"


def main():
    dist = Path(sys.argv[1] if len(sys.argv) > 1 else "dist")
    expected = sorted(p.relative_to(STATIC.parent).as_posix() for p in STATIC.rglob("*") if p.is_file())
    if not expected:
        sys.exit("no files under src/webmd/static; nothing to check")
    wheels, sdists = sorted(dist.glob("*.whl")), sorted(dist.glob("*.tar.gz"))
    if not wheels or not sdists:
        sys.exit(f"expected a wheel and an sdist in {dist}")
    failed = False
    for wheel in wheels:
        names = set(zipfile.ZipFile(wheel).namelist())
        missing = [f for f in expected if f"webmd/{f}" not in names]
        failed |= report(wheel, missing, len(expected))
    for sdist in sdists:
        with tarfile.open(sdist) as tar:
            names = {n.split("/", 1)[1] for n in tar.getnames() if "/" in n}
        missing = [f for f in expected if f"src/webmd/{f}" not in names]
        failed |= report(sdist, missing, len(expected))
    sys.exit(1 if failed else 0)


def report(path, missing, total):
    if missing:
        print(f"{path.name}: MISSING {len(missing)} of {total} assets: {', '.join(missing)}")
        return True
    print(f"{path.name}: all {total} bundled assets present")
    return False


if __name__ == "__main__":
    main()
