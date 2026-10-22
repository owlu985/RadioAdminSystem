"""NWS county alerts, matched against Census town boundaries for a short kiosk summary."""
from datetime import datetime, timezone
from functools import lru_cache
import json
from pathlib import Path
import re
import time
from urllib.parse import urlparse

import requests
from flask import current_app
from shapely.geometry import shape
from shapely.errors import ShapelyError

from app.logger import init_logger
from app.services.runtime_state import locked_state, read_state

PRIORITY = ('Putney', 'Brattleboro', 'Keene')
logger = init_logger()


def epoch(value):
    try:
        dt = datetime.fromisoformat(str(value).replace('Z', '+00:00'))
        return dt.replace(tzinfo=dt.tzinfo or timezone.utc).timestamp()
    except (ValueError, TypeError):
        return None


@lru_cache(maxsize=1)
def town_boundaries():
    data = json.loads((Path(__file__).parents[1] / 'data' / 'alert_towns.geojson').read_text())
    return [(f['properties']['BASENAME'],
             ('VTC025' if f['properties']['STATE'] == '50' else 'NHC005'),
             shape(f['geometry'])) for f in data['features']]


def affected_towns(feature, zones=None):
    """Never infer a town from a county alone when a warning supplies a polygon."""
    p = feature.get('properties') or {}
    codes = set((p.get('geocode') or {}).get('UGC') or [])
    geometries = []
    if feature.get('geometry'):
        geometries = [shape(feature['geometry'])]
    else:
        for url in p.get('affectedZones') or []:
            geometry = (zones or {}).get(url)
            if geometry:
                geometries.append(shape(geometry))
    names = set()
    for name, county, boundary in town_boundaries():
        if geometries:
            # Merely sharing a boundary line is not enough to claim a town is affected.
            if any(boundary.intersection(g).area > 1e-9 for g in geometries):
                names.add(name)
        elif county in codes:
            names.add(name)
    return sorted(names, key=lambda name: (PRIORITY.index(name) if name in PRIORITY else 3, name))


def summarize(feature, zones=None, now=None):
    p = feature.get('properties') or {}
    now = time.time() if now is None else now
    expires = epoch(p.get('expires'))
    ends = epoch(p.get('ends'))
    if (p.get('status') != 'Actual' or p.get('messageType') == 'Cancel'
            or expires is None or expires <= now or (ends is not None and ends <= now)):
        return None
    towns = affected_towns(feature, zones)
    if feature.get('geometry') and not towns:
        return None  # A distant Cheshire warning must not become a local warning.
    if not towns:
        # Keep the authoritative affected area for alerts elsewhere in Windham,
        # or for a zone whose geometry could not be resolved. Do not invent towns.
        area = '; '.join(str(p.get('areaDesc') or 'Affected area unavailable').split(';')[:2])[:80]
    else:
        area = ', '.join(towns[:3])
    return {'id': p.get('id') or feature.get('id'), 'event': p.get('event') or 'NWS alert',
            'towns': towns[:3], 'additional_towns': max(0, len(towns) - 3),
            'area': area, 'expires': p.get('ends') if ends is not None else p.get('expires'),
            'valid_until': min(expires, ends) if ends is not None else expires,
            'severity': p.get('severity'), 'urgency': p.get('urgency'),
            'sent': p.get('sent'), 'stale': False}


def _get(url, headers, params=None):
    # NWS supplies the pagination/zone URLs; accept only the official HTTPS host.
    parsed = urlparse(url)
    if parsed.scheme != 'https' or parsed.netloc != 'api.weather.gov':
        raise ValueError('Invalid NWS URL')
    with requests.get(url, headers=headers, params=params, timeout=(4, 10)) as response:
        response.raise_for_status()
        return response.json()


def refresh_weather_alerts():
    cfg = current_app.config
    if not cfg.get('NWS_ALERTS_ENABLED', True):
        return
    now = time.time()
    # Shared process lock + last_attempt enforce one collector even if multiple
    # scheduler processes accidentally run. Browser requests never contact NWS.
    with locked_state('nws-alerts') as state:
        if now - state.get('last_attempt', 0) < max(30, int(cfg.get('NWS_ALERT_POLL_SECONDS', 60))):
            return
        state['last_attempt'] = now
        headers = {'User-Agent': cfg.get('NWS_USER_AGENT') or 'RAMS-WLMC/1.0', 'Accept': 'application/geo+json'}
        try:
            result = _get('https://api.weather.gov/alerts/active', headers,
                          {'zone': ','.join(cfg.get('NWS_ALERT_COUNTIES', ['VTC025', 'NHC005']))})
            if result.get('type') != 'FeatureCollection' or not isinstance(result.get('features'), list):
                raise ValueError('Invalid NWS collection')
            features = result['features']
            pages = 0
            while (result.get('pagination') or {}).get('next'):
                pages += 1
                if pages > 5:
                    raise ValueError('Incomplete NWS collection')
                result = _get(result['pagination']['next'], headers)
                if not isinstance(result.get('features'), list):
                    raise ValueError('Invalid NWS page')
                features.extend(result['features'])
            zones = state.setdefault('zones', {})
            for feature in features:
                if feature.get('geometry'):
                    continue
                p = feature.get('properties') or {}
                for url in p.get('affectedZones') or []:
                    if not re.fullmatch(r'https://api\.weather\.gov/zones/(forecast|fire|county)/[A-Z]{2}[ZC][0-9]{3}', url):
                        continue
                    relevant = cfg.get('NWS_RELEVANT_ZONES', ['VTZ014', 'VTZ015', 'VTZ037', 'VTZ038', 'NHZ011', 'NHZ024'])
                    if url.rsplit('/', 1)[-1] not in relevant:
                        continue
                    cached = zones.get(url, {})
                    if now - cached.get('checked_at', 0) < 86400 * 7:
                        continue
                    try:
                        zone = _get(url, headers)
                        if zone.get('geometry'):
                            zones[url] = {'geometry': zone['geometry'], 'checked_at': now}
                    except (requests.RequestException, ValueError):
                        logger.warning('NWS zone boundary unavailable')
            geometries = {url: v['geometry'] for url, v in zones.items() if v.get('geometry')}
            items = {}
            for feature in features:
                item = summarize(feature, geometries, now=now)
                if item and item['id']:
                    items[item['id']] = item
            state.update(alerts=list(items.values()), last_success=time.time(), failed=False)
        except (requests.RequestException, ValueError, TypeError, KeyError, ShapelyError):
            state['failed'] = True
            logger.warning('NWS alert collection failed; retaining unexpired alerts')


def weather_alert_snapshot():
    state = read_state('nws-alerts')
    now = time.time()
    available = (not state.get('failed', True)
                 and now - state.get('last_success', 0) <= int(current_app.config.get('NWS_ALERT_STALE_SECONDS', 180)))
    items = [dict(item, stale=not available) for item in state.get('alerts', []) if item.get('valid_until', 0) > now]
    rank = {'Extreme': 0, 'Severe': 1, 'Moderate': 2, 'Minor': 3, 'Unknown': 4}
    items.sort(key=lambda item: (rank.get(item.get('severity'), 4),
                                min((PRIORITY.index(t) for t in item['towns'] if t in PRIORITY), default=3),
                                item.get('event', '')))
    return {'available': available, 'message': None if available else 'NWS info unavailable',
            'checked_at': state.get('last_success'), 'alerts': items}
