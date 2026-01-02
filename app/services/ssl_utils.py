import ipaddress
import os
import shutil
import subprocess
import tempfile
from typing import Iterable, List, Tuple


def ensure_dir(path: str) -> None:
    os.makedirs(os.path.dirname(path), exist_ok=True)


def _resolve_openssl_bin(openssl_bin: str | None) -> str:
    """Find an OpenSSL executable, preferring explicit overrides then common Windows paths."""

    candidates: Iterable[str | None] = (
        openssl_bin,
        shutil.which("openssl"),
        # Common Windows locations
        r"C:\\Program Files\\Git\\usr\\bin\\openssl.exe",
        r"C:\\Program Files\\OpenSSL-Win64\\bin\\openssl.exe",
        r"C:\\Program Files (x86)\\OpenSSL-Win32\\bin\\openssl.exe",
    )

    for candidate in candidates:
        if candidate and os.path.exists(candidate):
            return candidate

    raise FileNotFoundError(
        "OpenSSL not found. Install OpenSSL (e.g., via Git for Windows) or set DEV_SSL_OPENSSL_BIN / RAMS_DEV_SSL_OPENSSL to the openssl executable, "
        "such as C:\\Program Files\\Git\\usr\\bin\\openssl.exe."
    )


def _normalize_hosts(hosts: Iterable[str] | None) -> List[str]:
    values: List[str] = []
    if hosts:
        for host in hosts:
            if host and host not in values:
                values.append(host)
    for default_host in ("localhost", "127.0.0.1"):
        if default_host not in values:
            values.append(default_host)
    return values


def _san_value(host: str) -> str:
    try:
        ipaddress.ip_address(host)
        return f"IP:{host}"
    except ValueError:
        return f"DNS:{host}"


def ensure_dev_ssl(
    cert_path: str,
    key_path: str,
    common_name: str | None = None,
    openssl_bin: str | None = None,
    hosts: Iterable[str] | None = None,
) -> Tuple[str, str]:
    """
    Ensure a locally trusted dev certificate/key pair exists, signed by a RAMS
    dev CA and including SubjectAltName entries for provided hosts/IPs.

    If the cert/key already exist, they are left untouched. This is best effort
    for local/mobile testing; browsers may still warn until the CA is trusted.
    """

    cert_exists = cert_path and os.path.exists(cert_path)
    key_exists = key_path and os.path.exists(key_path)
    if cert_exists and key_exists:
        return cert_path, key_path

    if not cert_path or not key_path:
        raise ValueError("Both cert_path and key_path must be provided for dev SSL generation")

    ensure_dir(cert_path)
    ensure_dir(key_path)

    openssl_exec = _resolve_openssl_bin(openssl_bin)

    ssl_dir = os.path.dirname(cert_path)
    ca_cert_path = os.path.join(ssl_dir, "rams_dev_ca.pem")
    ca_key_path = os.path.join(ssl_dir, "rams_dev_ca.key")

    # Build SAN list and common name
    alt_hosts = _normalize_hosts(hosts)
    cn = common_name or (alt_hosts[0] if alt_hosts else "localhost")

    # Create CA if missing
    if not (os.path.exists(ca_cert_path) and os.path.exists(ca_key_path)):
        try:
            subprocess.run(
                [
                    openssl_exec,
                    "req",
                    "-x509",
                    "-nodes",
                    "-newkey",
                    "rsa:2048",
                    "-days",
                    "3650",
                    "-subj",
                    "/CN=RAMS Dev CA",
                    "-keyout",
                    ca_key_path,
                    "-out",
                    ca_cert_path,
                ],
                check=True,
            )
        except Exception as exc:  # pragma: no cover - env specific
            raise RuntimeError("Failed to generate RAMS dev CA certificate") from exc

    san_entries = ",".join(_san_value(h) for h in alt_hosts)

    # Create server key + CSR
    with tempfile.NamedTemporaryFile(delete=False) as csr_file:
        csr_path = csr_file.name
    with tempfile.NamedTemporaryFile(delete=False, mode="w") as ext_file:
        ext_file.write(f"subjectAltName={san_entries}\n")
        ext_file_path = ext_file.name

    try:
        subprocess.run(
            [
                openssl_exec,
                "req",
                "-new",
                "-nodes",
                "-newkey",
                "rsa:2048",
                "-keyout",
                key_path,
                "-out",
                csr_path,
                "-subj",
                f"/CN={cn}",
            ],
            check=True,
        )

        subprocess.run(
            [
                openssl_exec,
                "x509",
                "-req",
                "-in",
                csr_path,
                "-CA",
                ca_cert_path,
                "-CAkey",
                ca_key_path,
                "-CAcreateserial",
                "-out",
                cert_path,
                "-days",
                "825",
                "-extfile",
                ext_file_path,
            ],
            check=True,
        )
    except FileNotFoundError as exc:  # pragma: no cover - env specific
        raise FileNotFoundError(
            "OpenSSL executable not found. Install OpenSSL or point DEV_SSL_OPENSSL_BIN / RAMS_DEV_SSL_OPENSSL to the openssl.exe path."
        ) from exc
    except subprocess.CalledProcessError as exc:  # pragma: no cover - env specific
        raise RuntimeError(f"OpenSSL failed to generate a dev cert: {exc}") from exc
    finally:
        for temp_path in (csr_path, ext_file_path):
            try:
                os.remove(temp_path)
            except OSError:
                pass

    return cert_path, key_path
