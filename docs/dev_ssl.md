# Local self-signed SSL for RAMS testing

These steps enable HTTPS locally (useful for testing WebRTC flows like Remote Link on mobile devices). The certificate is signed by a RAMS-dev CA and intended **only** for development.

## How to enable
1) Set the environment flag when running RAMS:
   ```bash
   RAMS_DEV_SSL=1 python run.py
   ```

2) Optional overrides (if you want custom paths):
   ```bash
   RAMS_DEV_SSL_CERT=/path/to/dev_cert.pem \
   RAMS_DEV_SSL_KEY=/path/to/dev_key.pem \
 RAMS_DEV_SSL=1 python run.py
  ```

If the cert/key don’t exist, RAMS will call `openssl` to generate:
- a RAMS dev CA (`instance/ssl/rams_dev_ca.pem`)
- a server cert signed by that CA with SubjectAltName entries for `localhost`, `127.0.0.1`, and your bind host (when set)

For a trusted experience in browsers, import `instance/ssl/rams_dev_ca.pem` into your local trust store after the first run. Without trust, you can still proceed past the warning for local testing.

If `openssl` is not on your PATH (common on Windows), either install it or point RAMS to the executable. Common locations:

```bash
RAMS_DEV_SSL_OPENSSL="C:/Program Files/Git/usr/bin/openssl.exe" \
RAMS_DEV_SSL=1 python run.py

# or, if you installed a standalone OpenSSL build
RAMS_DEV_SSL_OPENSSL="C:/Program Files/OpenSSL-Win64/bin/openssl.exe" \
RAMS_DEV_SSL=1 python run.py
```

## Defaults (config.py)
- `DEV_SSL_ENABLED = False`
- `DEV_SSL_CERT_PATH = instance/ssl/rams_dev_cert.pem`
- `DEV_SSL_KEY_PATH = instance/ssl/rams_dev_key.pem`

## Notes
- Browsers may warn about the self-signed cert; allow the exception for local testing.
- For LAN testing, combine `RAMS_DEV_SSL=1` with `RAMS_HOST=0.0.0.0` to reach the server from another device.

PowerShell example:
```powershell
$env:RAMS_DEV_SSL = "1"
# optional for LAN testing
# $env:RAMS_HOST = "0.0.0.0"
# add one of these if OpenSSL is not on PATH
# $env:RAMS_DEV_SSL_OPENSSL = "C:\\Program Files\\Git\\usr\\bin\\openssl.exe"
# $env:RAMS_DEV_SSL_OPENSSL = "C:\\Program Files\\OpenSSL-Win64\\bin\\openssl.exe"
python run.py
```

If you see **“OpenSSL not found”** at startup, set `RAMS_DEV_SSL_OPENSSL` to the path of `openssl.exe` (Git for Windows ships one under `C:\Program Files\Git\usr\bin\openssl.exe`).
