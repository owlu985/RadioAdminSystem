"""Decoded stream recovery, scheduler heartbeat and display-safe operational alerts."""
import os
from pathlib import Path
import shutil
import time

import requests
from flask import current_app

from app.services.runtime_state import locked_state, read_state


def check_stream_http(url):
    """All HTTP errors fail; a successful HTTP response still needs an audio probe."""
    try:
        with requests.get(url, stream=True, timeout=(4, 5)) as response:
            if not 200 <= response.status_code < 300:
                return False, f'HTTP {response.status_code}'
            if response.headers.get('Content-Type', '').lower().startswith(('text/', 'application/json')):
                return False, 'Stream returned non-audio content'
            return True, None
    except requests.RequestException:
        return False, 'Stream connection failed'


def record_stream_check(result, *, restart):
    """One failed scheduled sample is one failure. Playback success resets the streak."""
    cfg = current_app.config
    now = time.time()
    with locked_state('stream') as state:
        state['checked_at'] = now
        state['playable'] = result is not None and result.classification != 'unknown'
        if state['playable']:
            state.update(consecutive_failures=0, manual_restart_needed=False,
                         recovery='healthy', last_success=now,
                         classification=result.classification)
            return None
        state['consecutive_failures'] = state.get('consecutive_failures', 0) + 1
        state['classification'] = None
        state['recovery'] = 'checking'
        threshold = max(1, int(cfg.get('STREAM_DOWN_RESTART_THRESHOLD', 3)))
        if state['consecutive_failures'] < threshold:
            return None
        if not cfg.get('SELF_HEAL_ENABLED', True) or not cfg.get('BARIX_AUTO_RESTART_ENABLED', False):
            state.update(manual_restart_needed=True, recovery='manual')
            return None
        if now - state.get('last_restart_attempt', 0) < max(30, int(cfg.get('BARIX_RESTART_COOLDOWN_SECONDS', 90))):
            return None
        state['last_restart_attempt'] = now
        outcome = restart(reason=f"consecutive_stream_failures={state['consecutive_failures']}")
        state.update(restart_status=outcome.status,
                     recovery='verifying' if outcome.accepted else 'manual',
                     manual_restart_needed=not outcome.accepted)
        # Do not declare recovery until a later decoded sample succeeds.
        return outcome


def monitor_snapshot():
    now = time.time()
    cfg = current_app.config
    state = read_state('stream')
    age = now - state.get('checked_at', 0)
    stale_after = max(90, float(cfg.get('STREAM_PROBE_INTERVAL_MINUTES', 1)) * 180)
    fresh = age <= stale_after
    alerts = []
    if state.get('manual_restart_needed'):
        alerts.append(dict(code='12', severity='critical', message='Barix Error, Restart needed'))
    if fresh and state.get('playable') and state.get('classification') == 'dead_air':
        alerts.append(dict(code='02', severity='critical', message='NO INPUT DETECTED, CHECK BOARD'))
    if not fresh or state.get('playable') is False:
        alerts.append(dict(code='08', severity='warning', message='Audio monitoring unavailable'))
    scheduler = read_state('studio-jobs')
    if now - scheduler.get('checked_at', 0) > max(180, float(cfg.get('STUDIO_MONITOR_INTERVAL_SECONDS', 30)) * 3):
        alerts.append(dict(code='09', severity='warning', message='Background monitoring stalled'))
    if scheduler.get('storage_error'):
        alerts.append(dict(code='07', severity='warning', message='Recording storage unavailable'))
    radiodj = read_state('radiodj-status')
    if cfg.get('RADIODJ_API_BASE_URL') and (not radiodj.get('handshake_ok') or now - radiodj.get('checked_at', 0) > 90):
        alerts.append(dict(code='04', severity='warning', message='RadioDJ handshake failed'))
    snapshot = {'stream_up': state.get('playable') if fresh else None,
            'checked_at': state.get('checked_at'), 'recovery': state.get('recovery'),
            'consecutive_failures': state.get('consecutive_failures', 0),
            'alerts': alerts, 'radiodj': radiodj}
    # A browser/API observer can record a stalled scheduler code even when the
    # scheduler itself cannot run. Persist transitions rather than every refresh.
    from app.services.station_log import record_alert_transitions
    record_alert_transitions(alerts, stream_up=snapshot['stream_up'])
    return snapshot


def check_storage():
    """Check the actual recording destination; never create a missing NAS mount."""
    cfg = current_app.config
    path = Path(cfg.get('OUTPUT_FOLDER') or cfg.get('DATA_ROOT') or current_app.instance_path)
    if not path.is_dir() or not os.access(path, os.W_OK | os.X_OK):
        return True
    mount = cfg.get('MONITOR_STORAGE_MOUNT')
    if mount and not os.path.ismount(mount):
        return True
    try:
        return shutil.disk_usage(path).free < int(cfg.get('MONITOR_STORAGE_MIN_FREE_BYTES', 512 * 1024 * 1024))
    except OSError:
        return True


def run_studio_checks():
    from app.services.radiodj_client import RadioDJClient
    with locked_state('studio-jobs') as state:
        state.update(checked_at=time.time(), storage_error=check_storage())
    client = RadioDJClient()
    payload = client.status() if client.enabled else {}
    def flag(value):
        if str(value).lower() in {'true', '1', 'yes', 'on', 'playing'}:
            return True
        if str(value).lower() in {'false', '0', 'no', 'off', 'stopped'}:
            return False
        return None
    with locked_state('radiodj-status') as state:
        normalized = {str(k).lower(): v for k, v in payload.items()}
        handshake = any(key in normalized for key in ('autodj', 'playing', 'isplaying'))
        state.update(checked_at=time.time(), handshake_ok=handshake,
                     autodj=flag(normalized.get('autodj')),
                     playing=flag(normalized.get('playing', normalized.get('isplaying'))))
