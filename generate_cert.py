"""
Generate a self-signed TLS certificate for running the app over HTTPS.

HTTPS is required for the browser STREAM source: ``getUserMedia`` (accessing the
client's webcam) only works in a "secure context" — HTTPS or localhost. On a LAN
you reach the station by IP (e.g. https://172.32.201.106:5000), which is NOT a
secure context over plain HTTP, so the browser blocks the camera. A self-signed
cert fixes that (the browser warns once; click "Advanced -> Proceed").

Usage:
    python generate_cert.py                      # localhost + auto-detected LAN IPs
    python generate_cert.py 172.32.201.106       # also valid for this exact IP
    python generate_cert.py 172.32.201.106 myhost

Then set ``USE_HTTPS = True`` in config.py and start the app as usual. Browse to
https://<server-ip>:5000  (accept the one-time security warning).

The generated cert/key are written to ./certs/ and are NOT committed (see
.gitignore) — regenerate them on each machine.

Two backends are tried in order: the ``cryptography`` library, then the
``openssl`` command-line tool. Whichever is available is used.
"""

import os
import sys
import socket
import subprocess
import ipaddress
from pathlib import Path

CERT_DIR = Path(__file__).resolve().parent / "certs"
CERT_FILE = CERT_DIR / "cert.pem"
KEY_FILE = CERT_DIR / "key.pem"


def _local_ips():
    """Best-effort list of this machine's IPv4 addresses for the cert SAN."""
    ips = set()
    try:
        hostname = socket.gethostname()
        for info in socket.getaddrinfo(hostname, None, socket.AF_INET):
            ips.add(info[4][0])
    except Exception:
        pass
    # The classic "connect to a public IP" trick to learn the primary LAN IP
    # (no packet is actually sent — UDP connect just picks the route).
    try:
        s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        s.connect(("8.8.8.8", 80))
        ips.add(s.getsockname()[0])
        s.close()
    except Exception:
        pass
    return ips


def _collect_san(extra_hosts):
    """Return (dns_names, ip_addrs) sets for the certificate SAN."""
    dns_names = {"localhost"}
    ip_addrs = {"127.0.0.1"}
    ip_addrs.update(_local_ips())
    for host in extra_hosts:
        try:
            ipaddress.ip_address(host)
            ip_addrs.add(host)
        except ValueError:
            dns_names.add(host)
    return dns_names, ip_addrs


def _generate_with_cryptography(dns_names, ip_addrs):
    import datetime
    from cryptography import x509
    from cryptography.x509.oid import NameOID
    from cryptography.hazmat.primitives import hashes, serialization
    from cryptography.hazmat.primitives.asymmetric import rsa

    san = [x509.DNSName(d) for d in sorted(dns_names)]
    for ip in sorted(ip_addrs):
        try:
            san.append(x509.IPAddress(ipaddress.ip_address(ip)))
        except ValueError:
            pass

    key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
    subject = issuer = x509.Name([
        x509.NameAttribute(NameOID.COMMON_NAME, "Thai Union Can Inspector"),
        x509.NameAttribute(NameOID.ORGANIZATION_NAME, "Digital Vision"),
    ])
    now = datetime.datetime.now(datetime.timezone.utc)
    cert = (
        x509.CertificateBuilder()
        .subject_name(subject)
        .issuer_name(issuer)
        .public_key(key.public_key())
        .serial_number(x509.random_serial_number())
        .not_valid_before(now - datetime.timedelta(days=1))
        .not_valid_after(now + datetime.timedelta(days=3650))
        .add_extension(x509.SubjectAlternativeName(san), critical=False)
        .add_extension(x509.BasicConstraints(ca=True, path_length=None), critical=True)
        .sign(key, hashes.SHA256())
    )
    KEY_FILE.write_bytes(key.private_bytes(
        encoding=serialization.Encoding.PEM,
        format=serialization.PrivateFormat.TraditionalOpenSSL,
        encryption_algorithm=serialization.NoEncryption(),
    ))
    CERT_FILE.write_bytes(cert.public_bytes(serialization.Encoding.PEM))


def _generate_with_openssl(dns_names, ip_addrs):
    # Build a temporary OpenSSL config carrying the SAN entries.
    alt = []
    for i, d in enumerate(sorted(dns_names), start=1):
        alt.append(f"DNS.{i} = {d}")
    for i, ip in enumerate(sorted(ip_addrs), start=1):
        alt.append(f"IP.{i} = {ip}")
    cfg = (
        "[req]\n"
        "distinguished_name = dn\n"
        "x509_extensions = v3_req\n"
        "prompt = no\n"
        "[dn]\n"
        "CN = Thai Union Can Inspector\n"
        "O = Digital Vision\n"
        "[v3_req]\n"
        "basicConstraints = CA:TRUE\n"
        "subjectAltName = @alt_names\n"
        "[alt_names]\n"
        + "\n".join(alt) + "\n"
    )
    cfg_path = CERT_DIR / "_openssl.cnf"
    cfg_path.write_text(cfg)
    try:
        subprocess.run(
            ["openssl", "req", "-x509", "-newkey", "rsa:2048", "-nodes",
             "-keyout", str(KEY_FILE), "-out", str(CERT_FILE),
             "-days", "3650", "-config", str(cfg_path), "-extensions", "v3_req"],
            check=True,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.PIPE,
        )
    finally:
        try:
            cfg_path.unlink()
        except OSError:
            pass


def main(extra_hosts):
    CERT_DIR.mkdir(parents=True, exist_ok=True)
    dns_names, ip_addrs = _collect_san(extra_hosts)

    backend = None
    try:
        # Catch BaseException: a broken native build of `cryptography` can raise
        # pyo3_runtime.PanicException, which is NOT an Exception subclass.
        _generate_with_cryptography(dns_names, ip_addrs)
        backend = "cryptography"
    except BaseException as crypto_err:
        try:
            _generate_with_openssl(dns_names, ip_addrs)
            backend = "openssl"
        except Exception as ssl_err:
            print("ERROR: could not generate a certificate.")
            print(f"  cryptography backend failed: {crypto_err}")
            print(f"  openssl backend failed:      {ssl_err}")
            print("Install the `cryptography` package or the `openssl` CLI, then retry.")
            sys.exit(1)

    print(f"Self-signed certificate generated (backend: {backend}):")
    print(f"  cert : {CERT_FILE}")
    print(f"  key  : {KEY_FILE}")
    print(f"  valid for: {', '.join(sorted(dns_names) + sorted(ip_addrs))}")
    print()
    print("Next steps:")
    print("  1) Set  USE_HTTPS = True  in config.py")
    print("  2) Start the app:  python app.py")
    print("  3) Browse to  https://<server-ip>:5000  and accept the one-time warning")


if __name__ == "__main__":
    main(sys.argv[1:])
