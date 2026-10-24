/* Display-only polling. Recovery and NWS collection belong to the scheduler. */
(() => {
    const zone = document.body.dataset.timezone || 'America/New_York';
    const byId = id => document.getElementById(id);
    const logo = byId('stationLogo').src;
    let latest = null, nextStart = null, activeMarathon = true;
    const time = value => new Intl.DateTimeFormat('en-US', {timeZone: zone, hour: 'numeric', minute: '2-digit', hour12: true}).format(new Date(value));
    const dateKey = value => new Intl.DateTimeFormat('en-CA', {timeZone: zone, year: 'numeric', month: '2-digit', day: '2-digit'}).format(new Date(value));
    function line(parent, text, cls) {
        const el = document.createElement('div'); el.className = cls; el.textContent = text; parent.append(el); return el;
    }
    function clockParts() {
        return Object.fromEntries(new Intl.DateTimeFormat('en-US', {timeZone: zone, hour: 'numeric', minute: 'numeric', second: 'numeric', hourCycle: 'h23'}).formatToParts(new Date()).filter(p => ['hour', 'minute', 'second'].includes(p.type)).map(p => [p.type, Number(p.value)]));
    }
    function renderAlerts() {
        const box = byId('alerts'); box.replaceChildren();
        const items = (latest?.monitor?.alerts || []).map(a => ({title: `CODE ${a.code} · ${a.message}`, detail: '', severity: a.severity}));
        for (const a of latest?.nws?.alerts || []) {
            if (a.valid_until * 1000 <= Date.now()) continue;
            items.push({title: a.event, detail: `${a.area}${a.additional_towns ? ' · +' + a.additional_towns + ' more' : ''} · Until ${time(a.expires)}${a.stale ? ' · Last known alert' : ''}`, severity: ['Extreme', 'Severe'].includes(a.severity) ? 'critical' : 'warning'});
        }
        const parts = clockParts(), seconds = (59 - parts.minute) * 60 + (60 - parts.second);
        if (latest?.now?.status === 'on_air' && seconds <= 180) items.push({title: `Station ID in ${Math.floor(seconds / 60)}:${String(seconds % 60).padStart(2, '0')}`, detail: 'Due at the top of the hour', severity: 'warning'});
        if (!latest?.nws?.available) items.push({title: 'NWS info unavailable', detail: '', severity: 'warning'});
        if (!latest) items.unshift({title: 'Station data unavailable', detail: '', severity: 'warning'});
        items.sort((a, b) => (a.severity === 'critical' ? 0 : 1) - (b.severity === 'critical' ? 0 : 1));
        const tick = Math.floor(Date.now() / 10000);
        // Pin the highest-priority issue. Cycle additional issues in the other row.
        let shown = items;
        if (items.length > 2) shown = items[0].severity === 'critical' ? [items[0], items[1 + tick % (items.length - 1)]] : [items[tick % items.length], items[(tick + 1) % items.length]];
        byId('alertCount').textContent = items.length ? `${items.length} ACTIVE` : 'ALL CLEAR';
        if (!items.length) line(box, 'No active station or NWS alerts', 'all-clear good');
        shown.forEach(a => {const row = line(box, '', 'alert-row'); line(row, a.title, 'alert-title ' + a.severity); if (a.detail) line(row, a.detail, 'alert-detail');});
        document.body.classList.toggle('urgent', items.some(a => a.severity === 'critical'));
        document.body.classList.toggle('overnight', parts.hour < 7);
        byId('screensaver').hidden = !(document.body.dataset.screensaver === 'true' && parts.hour < 7 && !activeMarathon && latest && latest.now.status !== 'on_air' && !items.length);
    }
    function formatClock(value) {
        const [h, m] = String(value || '').split(':').map(Number);
        return Number.isFinite(h) && Number.isFinite(m) ? `${h % 12 || 12}:${String(m).padStart(2, '0')} ${h < 12 ? 'AM' : 'PM'}` : '—';
    }
    function render(data) {
        latest = data;
        const n = data.now, m = data.monitor;
        byId('streamState').textContent = m.stream_up === true ? 'STREAM ONLINE' : m.stream_up === false ? 'STREAM UNAVAILABLE' : 'STREAM STATUS UNKNOWN';
        byId('streamState').className = m.stream_up === true ? 'good' : 'warning';
        const noInput = (m.alerts || []).some(a => a.code === '02');
        byId('audioState').textContent = noInput ? 'NO INPUT DETECTED' : n.status === 'automation' ? 'AUTOMATION ACTIVE' : m.stream_up === true ? 'AUDIO DETECTED' : 'AUDIO · CHECKING';
        byId('audioState').className = noInput ? 'critical' : '';
        const live = n.status === 'on_air' && n.show, auto = n.status === 'automation' && n.track;
        byId('mode').textContent = live ? 'SCHEDULED LIVE SHOW' : auto ? 'AUTOMATION · NOW PLAYING' : 'AUTOMATION STATUS UNAVAILABLE';
        byId('trackTitle').textContent = live ? n.show.name : auto ? (n.track.title || 'Station programming') : 'Waiting for playback status';
        byId('trackArtist').textContent = live ? n.show.host : auto ? (n.track.artist || '') : '';
        byId('showWindow').hidden = !live;
        if (live) {const w = n.show.window; byId('showWindow').textContent = w.start_at ? `${time(w.start_at)}–${time(w.end_at)}` : `${formatClock(w.start_time)}–${formatClock(w.end_time)}`;}
        byId('cover').hidden = !auto;
        if (auto) {
            const hasCover = n.track.is_music && n.track.cover_url;
            byId('cover').classList.toggle('station-art', !hasCover);
            byId('cover').src = hasCover ? window.ramsUrl(n.track.cover_url) : logo;
        }
        byId('history').hidden = !auto; byId('historyRows').replaceChildren();
        if (auto) (n.history || []).forEach(t => line(byId('historyRows'), `${t.title || 'Unknown title'} — ${t.artist || 'Unknown artist'}`, 'history-line'));
        const next = n.next_show, w = next?.window;
        nextStart = w?.start_at && dateKey(w.start_at) === dateKey(Date.now()) ? new Date(w.start_at) : null;
        byId('nextTitle').textContent = nextStart ? `${next.name} · ${next.host}` : 'No more shows today';
        byId('nextTime').textContent = nextStart ? `${time(w.start_at)}–${time(w.end_at)}` : '';
        byId('stationChecked').textContent = m.checked_at ? `Station checked ${time(m.checked_at * 1000)}` : 'Station monitor pending';
        renderAlerts();
    }
    async function refresh() {
        try {const res = await fetch(window.ramsUrl('/api/studio/status'), {cache: 'no-store', signal: AbortSignal.timeout(20000)}); if (!res.ok) throw Error(); render(await res.json());}
        catch {
            latest = null; byId('streamState').textContent = 'STATION DATA UNAVAILABLE'; byId('audioState').textContent = 'AUDIO STATUS UNKNOWN';
            byId('cover').hidden = true; byId('history').hidden = true; byId('trackTitle').textContent = 'Station data unavailable'; byId('trackArtist').textContent = ''; byId('showWindow').hidden = true;
            nextStart = null; byId('nextTitle').textContent = 'Schedule unavailable'; byId('nextTime').textContent = ''; renderAlerts();
        } finally {setTimeout(refresh, 5000);}
    }
    const temp = x => x == null ? '—' : `${Math.round(x)}°`;
    async function marathon() {
        try {
            const res = await fetch(window.ramsUrl('/api/schedule'), {signal: AbortSignal.timeout(10000)});
            if (!res.ok) throw Error();
            const data = await res.json(), now = Date.now();
            activeMarathon = (data.events || []).some(e => e.type === 'marathon' && new Date(e.start_date).valueOf() <= now && new Date(e.end_date).valueOf() > now);
        } catch {activeMarathon = true;}
        finally {setTimeout(marathon, 300000);}
    }
    async function weather() {
        try {
            const res = await fetch(window.ramsUrl('/api/weather/tempest'), {signal: AbortSignal.timeout(12000)}); if (!res.ok) throw Error();
            const data = await res.json(); if (data.status !== 'ok') throw Error();
            byId('temperature').textContent = temp(data.current?.temp); byId('conditions').textContent = data.current?.condition || 'Conditions unavailable';
            const days = (data.next_days || []).slice(0, 3); byId('high').textContent = '↑ High ' + temp(days[0]?.high); byId('low').textContent = '↓ Low ' + temp(days[0]?.low);
            byId('forecast').replaceChildren();
            days.forEach(d => {const tile = line(byId('forecast'), d.day || '', ''); const hi = document.createElement('strong'); hi.textContent = '↑ ' + temp(d.high); tile.append(hi); const lo = document.createElement('span'); lo.textContent = '↓ ' + temp(d.low); tile.append(lo); line(tile, d.condition || 'Forecast unavailable', 'forecast-condition');});
            byId('weatherChecked').textContent = `Weather fetched ${time(Date.now())}`;
        } catch {byId('temperature').textContent = '—'; byId('conditions').textContent = 'Weather unavailable'; byId('high').textContent = '↑ High —'; byId('low').textContent = '↓ Low —'; byId('forecast').replaceChildren(); byId('weatherChecked').textContent = 'Weather unavailable';}
        finally {setTimeout(weather, 300000);}
    }
    byId('cover').addEventListener('error', () => {byId('cover').hidden = true;});
    function tick() {
        const clock = new Intl.DateTimeFormat('en-US', {timeZone: zone, hour: 'numeric', minute: '2-digit', second: '2-digit', hour12: true}).formatToParts(new Date());
        byId('clockDigits').textContent = clock.filter(part => part.type !== 'dayPeriod').map(part => part.value).join('').trim();
        byId('clockPeriod').textContent = clock.find(part => part.type === 'dayPeriod').value;
        const seconds = nextStart ? Math.max(0, Math.ceil((nextStart - Date.now()) / 1000)) : 0;
        byId('countdown').textContent = nextStart ? `Starts in ${Math.floor(seconds / 3600) ? Math.floor(seconds / 3600) + ':' : ''}${String(Math.floor(seconds % 3600 / 60)).padStart(2, '0')}:${String(seconds % 60).padStart(2, '0')}` : '';
        if (latest) renderAlerts();
    }
    refresh(); weather(); marathon(); tick(); setInterval(tick, 1000);
})();
