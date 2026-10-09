"""Self-signed TLS certificate generation and SSL context setup for webmd."""

import datetime
import ipaddress
import os
import socket
import ssl
from pathlib import Path

from cryptography import x509
from cryptography.hazmat.primitives import hashes, serialization
from cryptography.hazmat.primitives.asymmetric import ec
from cryptography.x509.oid import ExtendedKeyUsageOID, NameOID

VALID_DAYS = 825
RENEW_WITHIN_DAYS = 30


def local_names(bind):
    """Hostnames and IPs a client might use to reach this machine."""
    names = {"localhost", "127.0.0.1", "::1"}
    hostname = socket.gethostname()
    if hostname:
        names.add(hostname)
        if "." not in hostname:
            names.add(f"{hostname}.local")
    # The primary LAN IP: "connecting" a UDP socket sends nothing but picks the outbound interface.
    try:
        with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as s:
            s.connect(("192.0.2.1", 80))
            names.add(s.getsockname()[0])
    except OSError:
        pass
    if bind not in ("", "0.0.0.0", "::"):
        names.add(bind)
    return names


def _san_entry(name):
    try:
        return x509.IPAddress(ipaddress.ip_address(name))
    except ValueError:
        return x509.DNSName(name)


def _cert_names(cert):
    san = cert.extensions.get_extension_for_class(x509.SubjectAlternativeName).value
    return {str(v) for v in san.get_values_for_type(x509.DNSName)} | {
        str(v) for v in san.get_values_for_type(x509.IPAddress)
    }


def _needs_renewal(cert_path, names):
    """True if the cert is unreadable, near expiry, or doesn't cover all `names`."""
    try:
        cert = x509.load_pem_x509_certificate(cert_path.read_bytes())
        missing = {str(ipaddress.ip_address(n)) if _is_ip(n) else n for n in names} - _cert_names(cert)
    except (OSError, ValueError, x509.ExtensionNotFound):
        return True
    renew_at = cert.not_valid_after_utc - datetime.timedelta(days=RENEW_WITHIN_DAYS)
    return bool(missing) or datetime.datetime.now(datetime.timezone.utc) >= renew_at


def _is_ip(name):
    try:
        ipaddress.ip_address(name)
        return True
    except ValueError:
        return False


def ensure_self_signed(cert_path, key_path, names):
    """Create (or refresh) a self-signed cert covering `names`. Returns True if one was generated."""
    if cert_path.exists() and key_path.exists() and not _needs_renewal(cert_path, names):
        return False

    key = ec.generate_private_key(ec.SECP256R1())
    subject = x509.Name([
        x509.NameAttribute(NameOID.COMMON_NAME, socket.gethostname() or "localhost"),
        x509.NameAttribute(NameOID.ORGANIZATION_NAME, "webmd self-signed"),
    ])
    now = datetime.datetime.now(datetime.timezone.utc)
    cert = (
        x509.CertificateBuilder()
        .subject_name(subject)
        .issuer_name(subject)
        .public_key(key.public_key())
        .serial_number(x509.random_serial_number())
        .not_valid_before(now - datetime.timedelta(minutes=5))
        .not_valid_after(now + datetime.timedelta(days=VALID_DAYS))
        .add_extension(x509.SubjectAlternativeName([_san_entry(n) for n in sorted(names)]), critical=False)
        .add_extension(x509.BasicConstraints(ca=False, path_length=None), critical=True)
        .add_extension(x509.ExtendedKeyUsage([ExtendedKeyUsageOID.SERVER_AUTH]), critical=False)
        .sign(key, hashes.SHA256())
    )

    cert_path.parent.mkdir(parents=True, exist_ok=True)
    key_bytes = key.private_bytes(
        serialization.Encoding.PEM,
        serialization.PrivateFormat.PKCS8,
        serialization.NoEncryption(),
    )
    # Write the key with 0600 from the start; replace any old key atomically.
    tmp = key_path.with_suffix(".tmp")
    fd = os.open(tmp, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
    with os.fdopen(fd, "wb") as f:
        f.write(key_bytes)
    os.replace(tmp, key_path)
    cert_path.write_bytes(cert.public_bytes(serialization.Encoding.PEM))
    return True


def fingerprint(cert_path):
    """SHA-256 fingerprint in the colon-separated form browsers display."""
    cert = x509.load_pem_x509_certificate(Path(cert_path).read_bytes())
    return cert.fingerprint(hashes.SHA256()).hex(":").upper()


def server_context(cert_path, key_path):
    ctx = ssl.SSLContext(ssl.PROTOCOL_TLS_SERVER)
    ctx.minimum_version = ssl.TLSVersion.TLSv1_2
    ctx.load_cert_chain(cert_path, key_path)
    return ctx
