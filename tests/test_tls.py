import socket
import ssl

import pytest
from cryptography import x509

from webmd import tls
from webmd.permissions import is_private


def test_self_signed_cert_covers_names(tmp_path):
    cert, key = tmp_path / "cert.pem", tmp_path / "key.pem"
    names = {"localhost", "127.0.0.1", "my-host.local"}
    assert tls.ensure_self_signed(cert, key, names) is True
    parsed = x509.load_pem_x509_certificate(cert.read_bytes())
    assert names <= tls._cert_names(parsed)
    assert is_private(key)
    tls.server_context(cert, key)  # loads cleanly


def test_cert_reused_then_regenerated_for_new_names(tmp_path):
    cert, key = tmp_path / "cert.pem", tmp_path / "key.pem"
    tls.ensure_self_signed(cert, key, {"localhost"})
    assert tls.ensure_self_signed(cert, key, {"localhost"}) is False
    assert tls.ensure_self_signed(cert, key, {"localhost", "10.9.8.7"}) is True


def test_fingerprint_format(tmp_path):
    cert, key = tmp_path / "cert.pem", tmp_path / "key.pem"
    tls.ensure_self_signed(cert, key, {"localhost"})
    fp = tls.fingerprint(cert)
    assert len(fp.split(":")) == 32 and fp == fp.upper()


def test_cert_validates_against_itself(serve):
    s = serve("--tls")
    ctx = ssl.create_default_context(cafile=str(s.config_dir / "cert.pem"))
    with socket.create_connection(("localhost", s.port)) as raw, ctx.wrap_socket(raw, server_hostname="localhost"):
        pass  # handshake + hostname verification succeeded


def test_plain_http_to_tls_port_gets_message(serve):
    s = serve("--tls")
    with socket.create_connection(("127.0.0.1", s.port), timeout=5) as c:
        c.sendall(b"GET / HTTP/1.1\r\nHost: x\r\n\r\n")
        assert b"only speaks HTTPS" in c.recv(4096)


def test_stalled_client_does_not_block(serve):
    s = serve("--tls")
    stalled = [socket.create_connection(("127.0.0.1", s.port)) for _ in range(3)]
    try:
        assert s.get("/")[0] == 200
    finally:
        for c in stalled:
            c.close()


def test_server_survives_rejected_cert(serve):
    s = serve("--tls")
    with pytest.raises(ssl.SSLCertVerificationError):
        with socket.create_connection(("127.0.0.1", s.port)) as raw:
            ssl.create_default_context().wrap_socket(raw, server_hostname="localhost")
    assert s.get("/")[0] == 200


def test_long_hostname(tmp_path, monkeypatch):
    # CN is capped at 64 chars; CI runners (and some real machines) have longer hostnames.
    monkeypatch.setattr(socket, "gethostname", lambda: "h" * 67)
    cert, key = tmp_path / "cert.pem", tmp_path / "key.pem"
    assert tls.ensure_self_signed(cert, key, tls.local_names("0.0.0.0")) is True
    assert "h" * 67 in tls._cert_names(x509.load_pem_x509_certificate(cert.read_bytes()))
