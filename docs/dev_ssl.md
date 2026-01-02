# Local self-signed SSL for RAMS testing

These steps enable HTTPS locally (useful for testing WebRTC flows like Remote Link on mobile devices). The certificate is self-signed and intended **only** for development.

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

If the cert/key don’t exist, RAMS will call `openssl` to generate a 2048-bit, 1-year self-signed cert for `CN=localhost` under `instance/ssl/` by default.

## Defaults (config.py)
- `DEV_SSL_ENABLED = False`
- `DEV_SSL_CERT_PATH = instance/ssl/rams_dev_cert.pem`
- `DEV_SSL_KEY_PATH = instance/ssl/rams_dev_key.pem`

## Notes
- Browsers may warn about the self-signed cert; allow the exception for local testing.
- For LAN testing, combine `RAMS_DEV_SSL=1` with `RAMS_HOST=0.0.0.0` to reach the server from another device.
