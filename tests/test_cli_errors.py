"""CLI startup paths that end in a clear error, plus credential-file handling."""

import os
import subprocess
import sys

import pytest

from webmd import cli, permissions


def run_webmd(*args, tmp_path, env=None, timeout=30):
    base = {k: v for k, v in os.environ.items() if not k.startswith("WEBMD_") and k != "XDG_CONFIG_HOME"}
    return subprocess.run(
        [sys.executable, "-m", "webmd", *map(str, args)],
        capture_output=True,
        text=True,
        timeout=timeout,
        env={**base, "XDG_CONFIG_HOME": str(tmp_path / "cfg"), **(env or {})},
    )


def assert_clean_error(result, message):
    assert result.returncode != 0
    assert message in result.stderr, result.stderr
    assert "Traceback" not in result.stderr


def test_not_a_directory(tmp_path):
    assert_clean_error(run_webmd(tmp_path / "missing", tmp_path=tmp_path), "not a directory")


def test_cert_without_key(site, tmp_path):
    cert = tmp_path / "c.pem"
    cert.write_text("x")
    assert_clean_error(
        run_webmd(site, "--tls", "--cert", cert, "-p", "0", tmp_path=tmp_path),
        "--cert and --key must be given together",
    )


def test_key_without_cert(site, tmp_path):
    key = tmp_path / "k.pem"
    key.write_text("x")
    assert_clean_error(
        run_webmd(site, "--tls", "--key", key, "-p", "0", tmp_path=tmp_path), "--cert and --key must be given together"
    )


@pytest.mark.parametrize("content", ["not a pem", ""])
def test_unloadable_certificate(site, tmp_path, content):
    bad = tmp_path / "bad.pem"
    bad.write_text(content)
    assert_clean_error(
        run_webmd(site, "--tls", "--cert", bad, "--key", bad, "-p", "0", tmp_path=tmp_path),
        "could not load certificate",
    )


def test_missing_certificate_file(site, tmp_path):
    assert_clean_error(
        run_webmd(
            site,
            "--tls",
            "--cert",
            tmp_path / "nope.pem",
            "--key",
            tmp_path / "nope-key.pem",
            "-p",
            "0",
            tmp_path=tmp_path,
        ),
        "could not load certificate",
    )


def test_own_certificate_is_used(serve, tmp_path):
    from webmd import tls

    cert, key = tmp_path / "mine.pem", tmp_path / "mine-key.pem"
    tls.ensure_self_signed(cert, key, {"localhost", "127.0.0.1"})
    s = serve("--tls", "--cert", str(cert), "--key", str(key))
    assert f"using certificate {cert}" in s.output
    assert tls.fingerprint(cert) in s.output
    assert s.get("/README.md")[0] == 200


# --- Credentials file ---------------------------------------------------------------


def test_first_boot_creates_env_file_in_new_directory(tmp_path):
    env_path = tmp_path / "deep" / "new" / "dir" / ".env"
    user, password, generated = cli.load_credentials(env_path)
    assert generated and user == "webmd" and len(password) >= 24
    text = env_path.read_text(encoding="utf-8")
    assert text.startswith("# webmd Basic Auth credentials")
    assert f"WEBMD_USER=webmd\nWEBMD_PASSWORD={password}\n" in text
    assert permissions.is_private(env_path)


def test_password_added_to_file_that_only_has_a_user(tmp_path):
    env_path = tmp_path / ".env"
    env_path.write_bytes(b"WEBMD_USER=alice")  # no trailing newline, no password
    user, password, generated = cli.load_credentials(env_path)
    assert generated and user == "alice"
    assert env_path.read_bytes() == f"WEBMD_USER=alice\nWEBMD_PASSWORD={password}\n".encode()  # user kept once


def test_existing_password_is_not_regenerated(tmp_path):
    env_path = tmp_path / ".env"
    env_path.write_bytes(b"WEBMD_PASSWORD=keep-me\n")
    before = env_path.read_bytes()
    assert cli.load_credentials(env_path) == ("webmd", "keep-me", False)
    assert env_path.read_bytes() == before


def test_environment_overrides_file(tmp_path, monkeypatch):
    env_path = tmp_path / ".env"
    env_path.write_bytes(b"WEBMD_USER=file-user\nWEBMD_PASSWORD=file-pw\n")
    monkeypatch.setenv("WEBMD_USER", "env-user")
    monkeypatch.setenv("WEBMD_PASSWORD", "env-pw")
    assert cli.load_credentials(env_path) == ("env-user", "env-pw", False)


def test_generated_passwords_are_unique(tmp_path):
    passwords = {cli.load_credentials(tmp_path / f"{i}.env")[1] for i in range(20)}
    assert len(passwords) == 20


# --- Private-file failure cleanup -------------------------------------------------------


def test_failed_write_removes_temp_file_and_keeps_original(tmp_path, monkeypatch):
    target = tmp_path / "secret"
    target.write_bytes(b"original")

    def boom(*args):
        raise OSError("disk full")

    monkeypatch.setattr(permissions.os, "replace", boom)
    with pytest.raises(OSError, match="disk full"):
        permissions.create_private_file(target, b"new")
    assert target.read_bytes() == b"original"
    assert sorted(p.name for p in tmp_path.iterdir()) == ["secret"]  # no .tmp left behind


def test_failed_write_survives_missing_temp_file(tmp_path, monkeypatch):
    # If the temp file is already gone when cleanup runs, the original error still surfaces.
    def boom(src, dst):
        os.unlink(src)
        raise OSError("replace failed")

    monkeypatch.setattr(permissions.os, "replace", boom)
    with pytest.raises(OSError, match="replace failed"):
        permissions.create_private_file(tmp_path / "secret", b"x")
