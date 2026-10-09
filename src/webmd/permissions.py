"""Owner-only file creation that works on both POSIX and Windows."""

import os
import sys
from pathlib import Path

PRIVATE_MODE = 0o600


def create_private_file(path, data):
    """Atomically write `data` (bytes) to `path`, readable only by the current user.

    POSIX: the temp file is created with mode 0600 from the start (no window where
    it's world-readable) and then renamed over `path`.

    Windows: POSIX modes don't apply; NTFS files inherit the parent directory's
    ACL. The config dir lives under the user's profile (%USERPROFILE%\\.config by
    default), which is private to that user on a standard install, so the file is
    protected by inheritance. webmd does not modify ACLs itself.
    """
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_name(path.name + ".tmp")
    fd = os.open(tmp, os.O_WRONLY | os.O_CREAT | os.O_TRUNC | getattr(os, "O_BINARY", 0), PRIVATE_MODE)
    try:
        with os.fdopen(fd, "wb") as f:
            f.write(data)
        if sys.platform != "win32":
            os.chmod(tmp, PRIVATE_MODE)  # in case umask or a pre-existing tmp file widened it
        os.replace(tmp, path)
    except BaseException:
        try:
            os.unlink(tmp)
        except OSError:
            pass
        raise


def is_private(path):
    """True if `path` is not readable by group/others (always True on Windows; see above)."""
    if sys.platform == "win32":
        return True
    return Path(path).stat().st_mode & 0o077 == 0
