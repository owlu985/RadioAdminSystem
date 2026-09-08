#!/usr/bin/env bash
set -euo pipefail

# Install the scheduler owner as a real boot service. Run from the repository
# root with sudo; the service deliberately remains separate from WSGI.
REPO_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
SERVICE_USER="${RAMS_SERVICE_USER:-${SUDO_USER:-$(id -un)}}"
PYTHON_BIN="${RAMS_PYTHON_BIN:-${REPO_DIR}/.venv/bin/python}"
UNIT_PATH="${RAMS_SYSTEMD_UNIT_PATH:-/etc/systemd/system/rams-background.service}"

if [[ ! -x "${PYTHON_BIN}" ]]; then
    printf 'Python executable not found: %s\n' "${PYTHON_BIN}" >&2
    printf 'Set RAMS_PYTHON_BIN to the Python used by the web application.\n' >&2
    exit 1
fi

cat >"${UNIT_PATH}" <<EOF
[Unit]
Description=RAMS recording and metadata scheduler
After=network-online.target
Wants=network-online.target

[Service]
Type=simple
User=${SERVICE_USER}
WorkingDirectory=${REPO_DIR}
Environment=RAMS_RUN_SCHEDULER_ON_STARTUP=0
ExecStart=${PYTHON_BIN} ${REPO_DIR}/background_service.py
Restart=always
RestartSec=3
TimeoutStopSec=60

[Install]
WantedBy=multi-user.target
EOF

systemctl daemon-reload
systemctl enable --now rams-background.service
systemctl --no-pager --full status rams-background.service
