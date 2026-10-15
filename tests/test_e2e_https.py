"""End to end, in a real browser: self-signed HTTPS and the generated password.

A LAN bind (-b 0.0.0.0) turns on HTTPS with a certificate webmd generates and
Basic Auth with a password it generates. These tests drive Chromium the way a
person would:

- A normal browser must reject the self-signed certificate.
- A browser told to trust *exactly that* certificate's public key (Chromium's
  --ignore-certificate-errors-spki-list, i.e. pinning) must connect over TLS,
  and the certificate it sees must be the one whose SHA-256 webmd printed.
  So these tests check the real certificate instead of disabling verification.
- The generated password from ~/.config/webmd/.env must log in; anything else
  must not, and must not render the page.
"""

import base64
import hashlib
import re

import pytest
from cryptography import x509
from cryptography.hazmat.primitives import hashes, serialization


def spki_pin(cert_path):
    """base64(SHA-256(SubjectPublicKeyInfo)): the format Chromium's SPKI allowlist takes."""
    cert = x509.load_pem_x509_certificate(cert_path.read_bytes())
    der = cert.public_key().public_bytes(serialization.Encoding.DER, serialization.PublicFormat.SubjectPublicKeyInfo)
    return base64.b64encode(hashlib.sha256(der).digest()).decode()


def printed_fingerprint(output):
    return re.search(r"SHA-256 ((?:[0-9A-F]{2}:){31}[0-9A-F]{2})", output).group(1)


@pytest.fixture
def lan_server(serve):
    """webmd bound to 0.0.0.0: HTTPS + auth, both generated on this first boot."""
    s = serve("-b", "0.0.0.0")
    assert s.scheme == "https"
    assert "generated self-signed certificate" in s.output
    assert "generated credentials" in s.output
    return s


@pytest.fixture
def pinned_browser(playwright_instance, tmp_path, browser):
    """Chromium that trusts only the given certificate's key. Returns a context factory."""
    contexts = []

    def open_context(cert_path, **context_kwargs):
        # The SPKI allowlist is a launch flag, so this needs its own browser instance.
        ctx = playwright_instance.chromium.launch_persistent_context(
            tmp_path / f"profile-{len(contexts)}",
            headless=True,
            args=[f"--ignore-certificate-errors-spki-list={spki_pin(cert_path)}"],
            **context_kwargs,
        )
        contexts.append(ctx)
        return ctx

    yield open_context
    for ctx in contexts:
        ctx.close()


def https_url(server, path="/"):
    # localhost (not 127.0.0.1) so the hostname is checked against the cert's SAN list.
    return f"https://localhost:{server.port}{path}"


# --- Self-signed certificate ----------------------------------------------------


def test_untrusted_browser_rejects_self_signed_cert(browser, lan_server):
    ctx = browser.new_context()  # ordinary verification
    page = ctx.new_page()
    with pytest.raises(Exception, match=r"ERR_CERT_AUTHORITY_INVALID"):
        page.goto(https_url(lan_server, "/README.md"))
    ctx.close()


def test_pinned_cert_connects_over_tls_and_matches_printed_fingerprint(pinned_browser, lan_server):
    cert_path = lan_server.config_dir / "cert.pem"
    user, password = lan_server.credentials()
    ctx = pinned_browser(cert_path, http_credentials={"username": user, "password": password})
    page = ctx.new_page()
    response = page.goto(https_url(lan_server, "/normal.md"))

    assert response.status == 200
    details = response.security_details()
    assert details["protocol"] in ("TLS 1.2", "TLS 1.3")
    assert details["subjectName"] == "webmd"

    # The cert the browser accepted is the one webmd generated and printed.
    cert = x509.load_pem_x509_certificate(cert_path.read_bytes())
    assert cert.fingerprint(hashes.SHA256()).hex(":").upper() == printed_fingerprint(lan_server.output)
    assert details["validTo"] == int(cert.not_valid_after_utc.timestamp())

    page.wait_for_selector("#content h1")
    assert page.locator("#content h1").text_content() == "Title Here"
    assert page.locator("pre code span[class^='hljs-']").count() >= 3  # bundled JS ran over HTTPS


def test_pinning_a_different_key_does_not_trust_webmd(pinned_browser, lan_server, tmp_path):
    from webmd import tls

    other_cert, other_key = tmp_path / "other.pem", tmp_path / "other-key.pem"
    tls.ensure_self_signed(other_cert, other_key, {"localhost"})
    ctx = pinned_browser(other_cert)
    with pytest.raises(Exception, match=r"ERR_CERT_AUTHORITY_INVALID"):
        ctx.new_page().goto(https_url(lan_server, "/README.md"))


def test_regenerated_cert_after_restart_is_the_same_cert(pinned_browser, serve):
    first = serve("-b", "0.0.0.0")
    pin_before = spki_pin(first.config_dir / "cert.pem")
    first.proc.terminate()
    first.proc.wait()

    second = serve("-b", "0.0.0.0")
    assert "using certificate" in second.output
    assert printed_fingerprint(second.output) == printed_fingerprint(first.output)
    assert spki_pin(second.config_dir / "cert.pem") == pin_before

    user, password = second.credentials()
    ctx = pinned_browser(second.config_dir / "cert.pem", http_credentials={"username": user, "password": password})
    assert ctx.new_page().goto(https_url(second, "/README.md")).status == 200


def test_http_url_on_https_port_explains_itself(browser, lan_server):
    ctx = browser.new_context()
    page = ctx.new_page()
    page.goto(f"http://127.0.0.1:{lan_server.port}/")
    assert "only speaks HTTPS" in page.content()
    ctx.close()


# --- Generated password -----------------------------------------------------------


def test_generated_password_is_written_privately_and_printed(lan_server):
    user, password = lan_server.credentials()
    assert user == "webmd"
    assert len(password) >= 24 and re.fullmatch(r"[A-Za-z0-9_-]+", password)  # token_urlsafe(18)
    assert f"user={user} password={password}" in lan_server.output
    from webmd.permissions import is_private

    assert is_private(lan_server.config_dir / ".env")


def test_generated_password_logs_in_through_browser(pinned_browser, lan_server):
    user, password = lan_server.credentials()
    ctx = pinned_browser(lan_server.config_dir / "cert.pem", http_credentials={"username": user, "password": password})
    page = ctx.new_page()
    assert page.goto(https_url(lan_server, "/")).status == 200
    assert page.locator(".listing a", has_text="normal.md").count() == 1
    page.locator(".listing a", has_text="normal.md").click()
    page.wait_for_selector("#content h1")
    assert page.url.endswith("/normal.md")


@pytest.mark.parametrize(
    "credentials",
    [None, ("webmd", "wrong-password"), ("someone-else", "{password}")],
    ids=["no-credentials", "wrong-password", "wrong-user"],
)
def test_browser_without_the_generated_password_is_refused(pinned_browser, lan_server, credentials):
    kwargs = {}
    if credentials:
        _, password = lan_server.credentials()
        user, pw = credentials
        kwargs["http_credentials"] = {"username": user, "password": pw.format(password=password)}
    ctx = pinned_browser(lan_server.config_dir / "cert.pem", **kwargs)
    page = ctx.new_page()
    response = page.goto(https_url(lan_server, "/normal.md"))
    assert response.status in (401, 429)
    assert page.locator("#content h1").count() == 0  # nothing rendered
    assert "Title Here" not in page.content()


def test_password_reused_across_restart_and_still_logs_in(pinned_browser, serve):
    first = serve("-b", "0.0.0.0")
    creds = first.credentials()
    first.proc.terminate()
    first.proc.wait()

    second = serve("-b", "0.0.0.0")
    assert "generated credentials" not in second.output
    assert second.credentials() == creds
    assert creds[1] not in second.output  # only printed when first generated

    ctx = pinned_browser(second.config_dir / "cert.pem", http_credentials={"username": creds[0], "password": creds[1]})
    assert ctx.new_page().goto(https_url(second, "/README.md")).status == 200


def test_edited_env_file_password_is_used(pinned_browser, serve):
    first = serve("-b", "0.0.0.0")
    env_file = first.config_dir / ".env"
    first.proc.terminate()
    first.proc.wait()
    env_file.write_text("WEBMD_USER=me\nWEBMD_PASSWORD=chosen-by-hand\n", encoding="utf-8")

    second = serve("-b", "0.0.0.0")
    cert = second.config_dir / "cert.pem"
    good = pinned_browser(cert, http_credentials={"username": "me", "password": "chosen-by-hand"})
    assert good.new_page().goto(https_url(second, "/README.md")).status == 200
    old = pinned_browser(cert, http_credentials={"username": "webmd", "password": "anything"})
    assert old.new_page().goto(https_url(second, "/README.md")).status == 401
