import os
import shutil
import subprocess
from typing import Iterable, Tuple


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


def ensure_dev_ssl(
    cert_path: str,
    key_path: str,
    common_name: str = "localhost",
    openssl_bin: str | None = None,
) -> Tuple[str, str]:
    """
    Ensure a self-signed certificate/key pair exists at the provided paths.

    This is intended for local testing only (e.g., enabling HTTPS on LAN so
    mobile devices can exercise WebRTC flows). If the files already exist,
    they are left untouched.
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

    cmd = [
        openssl_exec,
        "req",
        "-x509",
        "-newkey",
        "rsa:2048",
        "-nodes",
        "-keyout",
        key_path,
        "-out",
        cert_path,
        "-days",
        "365",
        "-subj",
        f"/CN={common_name}",
    ]

    try:
        subprocess.run(cmd, check=True)
    except FileNotFoundError as exc:  # pragma: no cover - env specific
        raise FileNotFoundError(
            "OpenSSL executable not found. Install OpenSSL or point DEV_SSL_OPENSSL_BIN / RAMS_DEV_SSL_OPENSSL to the openssl.exe path."
        ) from exc
    except subprocess.CalledProcessError as exc:  # pragma: no cover - env specific
        raise RuntimeError(f"OpenSSL failed to generate a dev cert: {exc}") from exc

    return cert_path, key_path
