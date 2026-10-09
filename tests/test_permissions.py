import os
import stat
import sys

import pytest

from webmd.permissions import create_private_file, is_private

posix_only = pytest.mark.skipif(sys.platform == "win32", reason="POSIX permission bits")


def test_creates_parent_dirs_and_content(tmp_path):
    target = tmp_path / "a" / "b" / "secret"
    create_private_file(target, b"data")
    assert target.read_bytes() == b"data"


def test_overwrites_atomically_and_leaves_no_temp(tmp_path):
    target = tmp_path / "secret"
    create_private_file(target, b"one")
    create_private_file(target, b"two")
    assert target.read_bytes() == b"two"
    assert [p.name for p in tmp_path.iterdir()] == ["secret"]


def test_binary_content_is_not_newline_translated(tmp_path):
    # O_BINARY matters on Windows: "\n" must not become "\r\n" in key files.
    target = tmp_path / "k"
    create_private_file(target, b"a\nb\n")
    assert target.read_bytes() == b"a\nb\n"


@posix_only
def test_posix_mode_is_0600_even_with_permissive_umask(tmp_path):
    old = os.umask(0)
    try:
        target = tmp_path / "secret"
        create_private_file(target, b"x")
    finally:
        os.umask(old)
    assert stat.S_IMODE(target.stat().st_mode) == 0o600
    assert is_private(target)


@posix_only
def test_posix_replaces_world_readable_file(tmp_path):
    target = tmp_path / "secret"
    target.write_bytes(b"old")
    target.chmod(0o644)
    create_private_file(target, b"new")
    assert stat.S_IMODE(target.stat().st_mode) == 0o600


@posix_only
def test_is_private_detects_group_or_world_access(tmp_path):
    target = tmp_path / "f"
    target.write_bytes(b"")
    target.chmod(0o640)
    assert not is_private(target)


@pytest.mark.skipif(sys.platform != "win32", reason="Windows behaviour")
def test_windows_relies_on_profile_acl(tmp_path):
    target = tmp_path / "secret"
    create_private_file(target, b"x")
    assert is_private(target)  # documented: protection comes from the user-profile ACL
