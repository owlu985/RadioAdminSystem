import os
import subprocess
from typing import Tuple


def ensure_dir(path: str) -> None:
    os.makedirs(os.path.dirname(path), exist_ok=True)


def ensure_dev_ssl(cert_path: str, key_path: str, common_name: str = "localhost") -> Tuple[str, str]:
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

    cmd = [
        "openssl",
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

    subprocess.run(cmd, check=True)
    return cert_path, key_path
