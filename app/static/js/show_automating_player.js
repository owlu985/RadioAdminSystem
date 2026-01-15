(() => {
const nowPlayingPanel = document.getElementById('nowPlayingPanel');
const liveQueuePanel = document.getElementById('liveQueuePanel');
const queueBuilderPanel = document.getElementById('queueBuilderPanel');
const libraryNavigationPanel = document.getElementById('libraryNavigationPanel');
const playbackPanelsEnabled = Boolean(
    nowPlayingPanel && liveQueuePanel && queueBuilderPanel && libraryNavigationPanel
);
let playbackPanelFilter = 'all';
let playbackPanelTimer = null;

function escapeHtml(value) {
    return String(value ?? '')
        .replace(/&/g, '&amp;')
        .replace(/</g, '&lt;')
        .replace(/>/g, '&gt;')
        .replace(/"/g, '&quot;')
        .replace(/'/g, '&#39;');
}

function formatDuration(seconds) {
    const total = Number(seconds);
    if (Number.isNaN(total) || total <= 0) return '—';
    const mins = Math.floor(total / 60).toString().padStart(2, '0');
    const secs = Math.floor(total % 60).toString().padStart(2, '0');
    return `${mins}:${secs}`;
}

function formatTime(isoString) {
    if (!isoString) return '—';
    const parsed = new Date(isoString);
    if (Number.isNaN(parsed.getTime())) return '—';
    return parsed.toLocaleTimeString([], { hour: '2-digit', minute: '2-digit', second: '2-digit' });
}

function renderNowPlaying(nowPlaying, session) {
    if (!nowPlayingPanel) return;
    if (!nowPlaying) {
        nowPlayingPanel.innerHTML = '<div class="text-muted">Nothing playing right now.</div>';
        return;
    }
    const title = escapeHtml(nowPlaying.title || 'Untitled');
    const artist = escapeHtml(nowPlaying.artist || 'Unknown artist');
    const kind = escapeHtml((nowPlaying.kind || nowPlaying.type || 'item').toUpperCase());
    const status = escapeHtml((nowPlaying.status || 'playing').toUpperCase());
    const showName = escapeHtml(session?.show_name || '—');
    const djName = escapeHtml(session?.dj_name || '—');
    const notes = session?.notes ? `<div class="text-muted small">Notes: ${escapeHtml(session.notes)}</div>` : '';

    nowPlayingPanel.innerHTML = `
        <div class="d-flex justify-content-between align-items-start flex-wrap gap-3">
            <div>
                <div class="fw-semibold">${title}</div>
                <div class="text-muted small">${artist}</div>
                <div class="text-muted small">Type: ${kind}</div>
            </div>
            <div class="text-end">
                <div class="badge text-bg-primary">${status}</div>
                <div class="text-muted small mt-1">Duration: ${formatDuration(nowPlaying.duration)}</div>
                <div class="text-muted small">Started: ${formatTime(nowPlaying.started_at)}</div>
            </div>
        </div>
        <div class="text-muted small mt-2">Show: ${showName} &middot; DJ: ${djName}</div>
        ${notes}
    `;
}

function renderLiveQueue(queueItems, nowPlaying) {
    if (!liveQueuePanel) return;
    if (!queueItems || !queueItems.length) {
        liveQueuePanel.innerHTML = '<div class="text-muted">Queue empty.</div>';
        return;
    }
    const list = document.createElement('ul');
    list.className = 'list-group';
    queueItems.forEach(item => {
        const li = document.createElement('li');
        const isCurrent = nowPlaying && nowPlaying.queue_item_id === item.id;
        li.className = `list-group-item d-flex justify-content-between align-items-center${isCurrent ? ' list-group-item-info' : ''}`;
        const title = escapeHtml(item.title || 'Untitled');
        const artist = escapeHtml(item.artist || '');
        const kind = escapeHtml(item.kind || item.type || 'item');
        const meta = [
            artist ? `Artist: ${artist}` : null,
            `Type: ${kind}`,
            `Len: ${formatDuration(item.duration)}`,
        ].filter(Boolean).join(' · ');
        li.innerHTML = `
            <div>
                <div class="fw-semibold">${title}${isCurrent ? ' <span class="badge text-bg-success ms-2">On Air</span>' : ''}</div>
                <div class="text-muted small">${meta}</div>
            </div>
            <span class="text-muted small">#${item.position ?? '—'}</span>
        `;
        list.appendChild(li);
    });
    liveQueuePanel.innerHTML = '';
    liveQueuePanel.appendChild(list);
}

function renderQueueBuilder(queueItems) {
    if (!queueBuilderPanel) return;
    if (!queueItems || !queueItems.length) {
        queueBuilderPanel.innerHTML = '<div class="text-muted">No upcoming items in the playback queue.</div>';
        return;
    }
    const rows = queueItems.map(item => {
        const notes = item.metadata?.notes || item.metadata?.usage_rules || item.metadata?.raw || '—';
        return `
            <tr>
                <td>${escapeHtml(item.title || 'Untitled')}</td>
                <td>${escapeHtml(item.kind || item.type || 'item')}</td>
                <td>${formatDuration(item.duration)}</td>
                <td>${escapeHtml(notes)}</td>
            </tr>
        `;
    }).join('');
    queueBuilderPanel.innerHTML = `
        <p class="text-muted small">Upcoming items staged in the playback queue.</p>
        <div class="table-responsive">
            <table class="table table-sm align-middle">
                <thead>
                    <tr>
                        <th>Item</th>
                        <th>Type</th>
                        <th>Length</th>
                        <th>Notes</th>
                    </tr>
                </thead>
                <tbody>
                    ${rows}
                </tbody>
            </table>
        </div>
    `;
}

function renderLibraryNavigation(queueItems, session) {
    if (!libraryNavigationPanel) return;
    const items = queueItems || [];
    const counts = items.reduce((acc, item) => {
        const kind = (item.kind || item.type || 'item').toLowerCase();
        acc[kind] = (acc[kind] || 0) + 1;
        return acc;
    }, {});
    const kinds = Object.keys(counts).sort();
    if (playbackPanelFilter !== 'all' && !counts[playbackPanelFilter]) {
        playbackPanelFilter = 'all';
    }
    const navButtons = ['all', ...kinds].map(kind => {
        const label = kind === 'all' ? 'All' : kind.toUpperCase();
        const count = kind === 'all' ? items.length : counts[kind];
        const active = playbackPanelFilter === kind ? 'active' : '';
        return `<button class="btn btn-sm btn-outline-primary ${active}" data-kind="${kind}">${label} (${count})</button>`;
    }).join('');
    const filtered = playbackPanelFilter === 'all'
        ? items
        : items.filter(item => (item.kind || item.type || 'item').toLowerCase() === playbackPanelFilter);
    const rows = filtered.map(item => `
        <tr>
            <td>${escapeHtml(item.title || 'Untitled')}</td>
            <td>${escapeHtml(item.artist || '—')}</td>
            <td>${escapeHtml(item.kind || item.type || 'item')}</td>
            <td>${formatDuration(item.duration)}</td>
        </tr>
    `).join('') || `<tr><td colspan="4" class="text-muted">No items in this view.</td></tr>`;
    const showName = escapeHtml(session?.show_name || '—');
    const djName = escapeHtml(session?.dj_name || '—');
    libraryNavigationPanel.innerHTML = `
        <div class="d-flex flex-wrap gap-2 mb-3">${navButtons}</div>
        <div class="text-muted small mb-2">Session: ${showName} &middot; DJ: ${djName}</div>
        <div class="table-responsive">
            <table class="table table-sm align-middle">
                <thead>
                    <tr>
                        <th>Title</th>
                        <th>Artist</th>
                        <th>Type</th>
                        <th>Length</th>
                    </tr>
                </thead>
                <tbody>
                    ${rows}
                </tbody>
            </table>
        </div>
    `;
    libraryNavigationPanel.querySelectorAll('[data-kind]').forEach(btn => {
        btn.addEventListener('click', () => {
            playbackPanelFilter = btn.dataset.kind;
            renderLibraryNavigation(queueItems, session);
        });
    });
}

async function loadPlaybackPanels() {
    if (!playbackPanelsEnabled) return;
    try {
        const [sessionRes, queueRes] = await Promise.all([
            fetch('/api/playback/session'),
            fetch('/api/playback/queue'),
        ]);
        if (!sessionRes.ok || !queueRes.ok) {
            throw new Error('Unable to load playback panels');
        }
        const session = await sessionRes.json();
        const queueData = await queueRes.json();
        renderNowPlaying(queueData.now_playing, session);
        renderLiveQueue(queueData.queue || [], queueData.now_playing);
        renderQueueBuilder(queueData.queue || []);
        renderLibraryNavigation(queueData.queue || [], session);
    } catch (err) {
        if (nowPlayingPanel) nowPlayingPanel.innerHTML = '<div class="text-danger">Unable to load playback session.</div>';
        if (liveQueuePanel) liveQueuePanel.innerHTML = '<div class="text-danger">Unable to load queue.</div>';
        if (queueBuilderPanel) queueBuilderPanel.innerHTML = '<div class="text-danger">Unable to load queue builder.</div>';
        if (libraryNavigationPanel) libraryNavigationPanel.innerHTML = '<div class="text-danger">Unable to load library view.</div>';
    }
}

if (playbackPanelsEnabled) {
    loadPlaybackPanels();
    playbackPanelTimer = setInterval(loadPlaybackPanels, 5000);
    window.addEventListener('beforeunload', () => {
        if (playbackPanelTimer) {
            clearInterval(playbackPanelTimer);
            playbackPanelTimer = null;
        }
    });
}

let library = [];
const psaList = document.getElementById('psaList');
const queueList = document.getElementById('queueList');
const timerEls = [document.getElementById('timer'), document.getElementById('timerTop')];
const categoryFilter = document.getElementById('categoryFilter');
const libraryMeta = document.getElementById('libraryMeta');
const libraryPrev = document.getElementById('libraryPrev');
const libraryNext = document.getElementById('libraryNext');
const psaSearch = document.getElementById('psaSearch');
const metadataKinds = new Set(['psa', 'imaging']);
const top40 = document.getElementById('top40');
const loopBetween = document.getElementById('loopBetween');
const togglePauseBtn = document.getElementById('togglePause');
const playNextBtn = document.getElementById('playNext');
const fadeOutBtn = document.getElementById('fadeOut');
const addStopBtn = document.getElementById('addStop');
const modeBadgeTop = document.getElementById('modeBadgeTop');
const modeToggleButtons = document.querySelectorAll('[data-automation-mode]');
const showLogExport = document.getElementById('showLogExport');
const introBadge = document.getElementById('introBadge');
const outroBadge = document.getElementById('outroBadge');
const talkOverlay = document.getElementById('talkOverlay');
const talkOverlayIntro = document.getElementById('talkOverlayIntro');
const talkOverlayOutro = document.getElementById('talkOverlayOutro');
const queue = [];
const players = [
    { el: document.getElementById('psaAudioA'), item: null },
    { el: document.getElementById('psaAudioB'), item: null }
];
const overlayPlayer = document.getElementById('psaAudioOverlay');
const cuePlayer = document.getElementById('cuePlayer');
const cueWave = document.getElementById('cueWave');
const cueWaveInner = document.getElementById('cueWaveInner');
const cueWaveCanvas = document.getElementById('cueWaveCanvas');
const cueMarkers = document.getElementById('cueMarkers');
const cueNeedle = document.getElementById('cueNeedle');
const cueItemLabel = document.getElementById('cueItemLabel');
const cueFields = ['cue_in', 'intro', 'loop_in', 'loop_out', 'start_next', 'outro', 'cue_out'];
const cueInputs = {};
const cueColorMap = {
    cue_in: '#198754',
    intro: '#0d6efd',
    loop_in: '#20c997',
    loop_out: '#0dcaf0',
    start_next: '#6f42c1',
    outro: '#fd7e14',
    cue_out: '#dc3545',
};
let currentIdx = 0;
let currentItem = null;
let fadeTimer = null;
let timerInterval = null;
let recorder = null;
let recordChunks = [];
let autoNextTriggered = false;
let prestartedIdx = null;
let cueItem = null;
let cueSelected = 'cue_in';
let cueDragNeedle = false;
let cueDragField = null;
let cueBaseWidth = 0;
let talkUpMode = false;
let editingItem = null;
let pendingVT = null;
let pendingVTUrl = null;
let pendingVTBlob = null;
let libraryPage = 1;
let libraryTotal = 0;
const libraryPerPage = 50;
let libraryQuery = '';
let automationMode = 'manual';
let automationPollTimer = null;
let automationPlan = null;
let automationOverlayTimer = null;
let automationFadeTimer = null;
let automationContext = null;
let automationPlanKey = null;
let playbackSession = null;
let activeShowRunId = null;
let activeLogSheetId = null;
let showRunStartPromise = null;
let queueSyncInProgress = false;

function activePlayer() { return players[currentIdx].el; }
function otherPlayer() { return players[1 - currentIdx].el; }

const metadataDialog = document.getElementById('metadataDialog');
const metadataTitle = document.getElementById('metadataTitle');
const metadataCategory = document.getElementById('metadataCategory');
const metadataExpiry = document.getElementById('metadataExpiry');
const metadataUsage = document.getElementById('metadataUsage');
const metadataPath = document.getElementById('metadataPath');
const bulkDialog = document.getElementById('bulkDialog');
const bulkKind = document.getElementById('bulkKind');
const bulkStatus = document.getElementById('bulkStatus');
const legacyPlayerEnabled = Boolean(queueList && psaList);

function openDialog(dialog) {
    if (!dialog) return;
    if (dialog.showModal) {
        dialog.showModal();
    } else {
        dialog.setAttribute('open', 'open');
    }
}

function closeDialog(dialog) {
    if (!dialog) return;
    if (dialog.close) {
        dialog.close();
    } else {
        dialog.removeAttribute('open');
    }
}

async function openMetadataEditor(item) {
    editingItem = item;
    metadataTitle.value = item.title || '';
    metadataCategory.value = item.category || '';
    metadataExpiry.value = item.expires_on || '';
    metadataUsage.value = item.usage_rules || '';
    metadataPath.textContent = item.name ? `File: ${item.name}` : '';
    if (item.token) {
        try {
            const res = await fetch(`/api/psa/metadata?token=${encodeURIComponent(item.token)}`);
            const data = await res.json();
            if (data.status === 'ok' && data.metadata) {
                metadataTitle.value = data.metadata.title || '';
                metadataCategory.value = data.metadata.category || item.category || '';
                metadataExpiry.value = data.metadata.expires_on || '';
                metadataUsage.value = data.metadata.usage_rules || '';
            }
        } catch (err) {
            // ignore
        }
    }
    openDialog(metadataDialog);
}

function renderCategories(cats) {
    const options = cats || [];
    categoryFilter.innerHTML = '<option value="">All categories</option>' + options.map(c => `<option value="${c}">${c}</option>`).join('');
}

function renderLibrary(filter = '') {
    psaList.innerHTML = '';
    const term = filter.toLowerCase();
    const cat = categoryFilter.value;
    library.filter(item => {
        const title = (item.title || item.name || '').toLowerCase();
        return title.includes(term) && (!cat || item.category === cat);
    }).forEach(item => {
        const li = document.createElement('li');
        li.className = 'list-group-item';
        const meta = [];
        if (item.duration) meta.push(`${item.duration}s`);
        if (item.loop) meta.push('loop');
        if (item.category) meta.push(item.category);
        if (item.expires_on) meta.push(`expires ${item.expires_on}`);
        const displayName = item.title || item.name;
        const subtitle = item.title ? `<small class="text-muted">${item.name}</small><br>` : '';
        const usage = item.usage_rules ? `<small class="text-muted">Usage: ${item.usage_rules}</small><br>` : '';
        li.innerHTML = `<div class="d-flex justify-content-between align-items-center">
            <div>
                <strong>${displayName}</strong><br>
                ${subtitle}
                ${usage}
                ${meta.length ? `<small class='text-muted'>${meta.join(' • ')}</small>` : ''}
            </div>
            <div class="d-flex gap-2 align-items-center">
                <button class="btn btn-sm btn-outline-primary" data-add>Queue</button>
                <button class="btn btn-sm btn-outline-secondary" data-cue>Cues</button>
                ${item.loop ? '<span class="badge text-bg-info">Loop</span>' : ''}
            </div>
        </div>`;
        li.querySelector('[data-add]').addEventListener('click', () => addToQueue(item));
        li.querySelector('[data-cue]').addEventListener('click', () => selectCueItem(item));
        psaList.appendChild(li);
    });
    if (!psaList.children.length) {
        const li = document.createElement('li');
        li.className = 'list-group-item text-muted';
        li.textContent = 'No items found.';
        psaList.appendChild(li);
    }
}

function renderQueue() {
    queueList.innerHTML = '';
    const displayItems = [];
    if (currentItem) {
        displayItems.push({ item: currentItem, isCurrent: true });
    }
    queue.forEach((item, idx) => displayItems.push({ item, isCurrent: false, idx }));
    if (!displayItems.length) {
        queueList.innerHTML = '<li class="text-muted">Queue empty.</li>';
        resetTimers();
        return;
    }
    displayItems.forEach(entry => {
        const item = entry.item;
        const li = document.createElement('li');
        const meta = [];
        if (item.duration) meta.push(`${item.duration}s`);
        if (item.category) meta.push(item.category);
        if (item.metadata && item.metadata.host) meta.push(item.metadata.host);
        if (item.metadata && item.metadata.notes) meta.push(item.metadata.notes);
        const introMeta = item.cues && item.cues.intro ? ` (${item.cues.intro.toFixed(1)}s)` : '';
        const displayName = item.title || item.name;
        const currentBadge = entry.isCurrent ? '<span class="badge text-bg-success ms-2">Now Playing</span>' : '';
        li.innerHTML = `<div class="d-flex justify-content-between align-items-center">
            <div>${item.kind === 'voicetrack' ? '<span class="badge text-bg-primary me-1">VT</span>' : ''}${displayName}${introMeta}${currentBadge} ${meta.length ? `<small class="text-muted">(${meta.join(' • ')})</small>` : ''}</div>
            ${entry.isCurrent ? '' : `<div class="d-flex gap-2">
                <button class="btn btn-sm btn-outline-secondary" data-cue>Edit</button>
                <button class="btn btn-sm btn-outline-danger" data-remove>&times;</button>
            </div>`}
        </div>`;
        if (!entry.isCurrent) {
            li.dataset.index = entry.idx;
            li.tabIndex = 0;
            li.addEventListener('click', (ev) => {
                if (!ev.target.dataset.remove) {
                    startFrom(entry.idx, currentIdx, 'manual');
                }
            });
            li.addEventListener('keydown', (ev) => {
                if (ev.key === 'Enter') {
                    startFrom(entry.idx, currentIdx, 'manual');
                }
            });
            li.querySelector('[data-remove]').addEventListener('click', (ev) => { ev.stopPropagation(); removeFromQueue(entry.idx); });
        }
        queueList.appendChild(li);
    });
    updateVTInsertOptions();
}

function updateAutomationModeUI() {
    const isAutomation = automationMode === 'automation';
    if (modeBadgeTop) {
        modeBadgeTop.textContent = isAutomation ? 'Automation' : 'Manual';
        modeBadgeTop.className = `badge ${isAutomation ? 'text-bg-warning' : 'text-bg-success'}`;
    }
    modeToggleButtons.forEach(btn => {
        const mode = btn.dataset.automationMode;
        const active = mode === automationMode;
        const activeClass = mode === 'automation' ? 'text-bg-warning' : 'text-bg-success';
        btn.className = `badge rounded-pill ${active ? activeClass : 'text-bg-light border'}`;
        btn.setAttribute('aria-pressed', active ? 'true' : 'false');
    });
}

function updateShowLogExport() {
    if (!showLogExport) return;
    if (!activeLogSheetId) {
        showLogExport.classList.add('disabled');
        showLogExport.setAttribute('aria-disabled', 'true');
        showLogExport.href = '#';
        return;
    }
    showLogExport.classList.remove('disabled');
    showLogExport.removeAttribute('aria-disabled');
    showLogExport.href = `/logs/download/csv?sheet_id=${encodeURIComponent(activeLogSheetId)}`;
}

async function ensureShowRun() {
    if (activeShowRunId) return activeShowRunId;
    if (showRunStartPromise) return showRunStartPromise;
    const payload = {
        show_name: playbackSession?.show_name || undefined,
        dj_name: playbackSession?.dj_name || undefined,
    };
    showRunStartPromise = fetch('/api/playback/show/start', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify(payload),
    }).then(res => res.json()).then(data => {
        if (data.status === 'ok' && data.show_run_id) {
            activeShowRunId = data.show_run_id;
            activeLogSheetId = data.log_sheet_id;
            updateShowLogExport();
        }
        return activeShowRunId;
    }).catch(() => null).finally(() => { showRunStartPromise = null; });
    return showRunStartPromise;
}

function buildLogPayload(event, item, extra = {}) {
    if (!item) return null;
    return {
        show_run_id: activeShowRunId,
        log_sheet_id: activeLogSheetId,
        event,
        type: item.kind || item.category || item.type || 'item',
        title: item.title || item.name || null,
        artist: item.artist || null,
        duration: item.duration || null,
        metadata: item.metadata || null,
        reason: extra.reason,
        timestamp: new Date().toISOString(),
    };
}

async function logPlaybackEvent(event, item, extra = {}) {
    if (!item) return;
    await ensureShowRun();
    if (!activeShowRunId) return;
    const payload = buildLogPayload(event, item, extra);
    if (!payload) return;
    fetch('/api/playback/log', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify(payload),
    }).catch(() => {});
}

function ensureLogState(item) {
    if (!item) return null;
    if (!item._logState) {
        item._logState = { started: false, ended: false, inserted: false };
    }
    return item._logState;
}

function logItemStart(item, reason = 'play') {
    const state = ensureLogState(item);
    if (!state || state.started) return;
    state.started = true;
    logPlaybackEvent('start', item, { reason });
}

function logItemEnd(item, reason = 'end') {
    const state = ensureLogState(item);
    if (!state || state.ended) return;
    state.ended = true;
    logPlaybackEvent('end', item, { reason });
}

function logItemInsert(item, reason = 'manual') {
    const state = ensureLogState(item);
    if (!state || state.inserted) return;
    state.inserted = true;
    logPlaybackEvent('insert', item, { reason });
}

function resetTimers() {
    const text = '00:00.00';
    timerEls.forEach(el => {
        if (!el) return;
        el.textContent = text;
        el.style.color = '';
        el.style.fontWeight = '400';
    });
    introBadge.style.display = 'inline-block';
    introBadge.textContent = 'Intro: 0.0s';
    introBadge.classList.remove('countdown-flash', 'text-bg-danger');
    outroBadge.style.display = 'inline-block';
    outroBadge.textContent = 'Outro: 0.0s';
    outroBadge.classList.remove('countdown-flash', 'text-bg-danger');
    updateTalkOverlay(null);
    loopBetween.checked = false;
    loopBetween.disabled = true;
    togglePauseBtn.textContent = 'Pause';
}

async function loadAutomationMode() {
    try {
        const res = await fetch('/api/playback/session');
        const data = await res.json();
        if (res.ok && data.automation_mode) {
            automationMode = data.automation_mode;
        }
        playbackSession = data;
        if (data.show_run_id) {
            activeShowRunId = data.show_run_id;
        }
        if (data.log_sheet_id) {
            activeLogSheetId = data.log_sheet_id;
        }
        updateShowLogExport();
    } catch (err) {
        // ignore
    }
    updateAutomationModeUI();
    toggleAutomationPolling();
}

async function setAutomationMode(mode) {
    if (!['manual', 'automation'].includes(mode)) return;
    try {
        const res = await fetch('/api/playback/session', {
            method: 'POST',
            headers: {'Content-Type': 'application/json'},
            body: JSON.stringify({automation_mode: mode})
        });
        const data = await res.json();
        if (res.ok) {
            automationMode = data.automation_mode || mode;
        }
    } catch (err) {
        automationMode = mode;
    }
    updateAutomationModeUI();
    toggleAutomationPolling();
}

function toggleAutomationPolling() {
    if (automationMode === 'automation') {
        startAutomationPolling();
    } else {
        stopAutomationPolling();
    }
}

function startAutomationPolling() {
    if (automationPollTimer) return;
    automationPollTimer = setInterval(automationTick, 1000);
    automationTick();
}

function stopAutomationPolling() {
    if (automationPollTimer) {
        clearInterval(automationPollTimer);
        automationPollTimer = null;
    }
    automationPlan = null;
    automationContext = null;
    automationPlanKey = null;
    clearAutomationTimers();
}

function clearAutomationTimers() {
    if (automationOverlayTimer) {
        clearTimeout(automationOverlayTimer);
        automationOverlayTimer = null;
    }
    if (automationFadeTimer) {
        clearTimeout(automationFadeTimer);
        automationFadeTimer = null;
    }
}

function automationKindFor(item) {
    if (!item) return '';
    if (item.kind) return item.kind;
    if (item.stop) return 'stop';
    const category = (item.category || '').toLowerCase();
    if (category === 'music') return 'music';
    if (category) return category;
    return 'music';
}

function queueTypeFor(item) {
    if (!item) return 'music';
    if (item.stop) return 'stop';
    const kind = (item.kind || '').toLowerCase();
    if (['music', 'psa', 'imaging', 'voicetrack', 'stop'].includes(kind)) {
        return kind;
    }
    if (kind === 'overlay') return 'voicetrack';
    const category = (item.category || '').toLowerCase();
    if (category.includes('voice')) return 'voicetrack';
    if (category.includes('imaging')) return 'imaging';
    if (category.includes('psa')) return 'psa';
    return 'music';
}

function buildQueueMetadata(item) {
    return {
        url: item.url || null,
        name: item.name || item.title || null,
        category: item.category || null,
        token: item.token || null,
        loop: item.loop || null,
        stop: Boolean(item.stop),
        kind: item.kind || null,
        metadata: item.metadata || null,
    };
}

function buildLocalQueueItem(payload = {}) {
    const metadata = payload.metadata || {};
    const extra = metadata.metadata || {};
    const kind = metadata.kind || payload.kind || payload.type || queueTypeFor(metadata);
    return {
        id: payload.id || payload.queue_item_id || null,
        name: metadata.name || payload.title || 'Untitled',
        title: payload.title || metadata.name || 'Untitled',
        artist: payload.artist || metadata.artist || null,
        duration: payload.duration || metadata.duration || null,
        url: metadata.url || extra.url || null,
        category: metadata.category || extra.category || null,
        token: metadata.token || extra.token || null,
        loop: metadata.loop ?? extra.loop ?? null,
        stop: Boolean(metadata.stop || payload.kind === 'stop' || payload.type === 'stop'),
        kind,
        metadata: extra,
        cues: payload.cues || metadata.cues || extra.cues || null,
    };
}

async function syncNowPlaying(item, status = 'playing') {
    const payload = { status };
    if (item && item.id) {
        payload.item_id = item.id;
    } else if (item) {
        payload.type = queueTypeFor(item);
        payload.kind = item.kind || queueTypeFor(item);
        payload.title = item.title || item.name || '';
        payload.artist = item.artist || null;
        payload.duration = item.duration || null;
        payload.metadata = buildQueueMetadata(item);
        payload.cues = item.cues || null;
    }
    try {
        await fetch('/api/playback/now-playing', {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify(payload),
        });
    } catch (err) {
        // ignore
    }
}

async function syncSkip() {
    try {
        await fetch('/api/playback/queue/skip', { method: 'POST' });
    } catch (err) {
        // ignore
    }
}

async function enqueueQueueItem(item, position = null) {
    const payload = {
        type: queueTypeFor(item),
        kind: item.kind || queueTypeFor(item),
        title: item.title || item.name || 'Untitled',
        artist: item.artist || null,
        duration: item.duration || null,
        metadata: buildQueueMetadata(item),
        cues: item.cues || null,
    };
    if (position != null) payload.position = position;
    try {
        const res = await fetch('/api/playback/queue/enqueue', {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify(payload),
        });
        const data = await res.json();
        if (!res.ok || data.status !== 'ok' || !data.item) {
            return null;
        }
        return buildLocalQueueItem(data.item);
    } catch (err) {
        return null;
    }
}

async function dequeueQueueItem(item) {
    if (!item || !item.id) return;
    try {
        await fetch('/api/playback/queue/dequeue', {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({ item_id: item.id }),
        });
    } catch (err) {
        // ignore
    }
}

async function moveQueueItem(item, position) {
    if (!item || !item.id) return;
    try {
        await fetch('/api/playback/queue/move', {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({ item_id: item.id, position }),
        });
    } catch (err) {
        // ignore
    }
}

async function loadPlaybackQueue() {
    if (queueSyncInProgress) return;
    queueSyncInProgress = true;
    try {
        const res = await fetch('/api/playback/queue');
        if (!res.ok) throw new Error('Unable to load queue');
        const data = await res.json();
        queue.length = 0;
        (data.queue || []).forEach(item => {
            queue.push(buildLocalQueueItem(item));
        });
        currentItem = data.now_playing ? buildLocalQueueItem(data.now_playing) : null;
        renderQueue();
        updateVTInsertOptions();
        if (currentItem) {
            previewCues(currentItem);
        } else if (queue.length) {
            previewCues(queue[0]);
        } else {
            resetTimers();
        }
    } catch (err) {
        // ignore
    } finally {
        queueSyncInProgress = false;
    }
}

function buildAutomationItem(item, durationOverride = null) {
    if (!item) return null;
    const cues = item.cues || {};
    const duration = durationOverride != null && !isNaN(durationOverride)
        ? durationOverride
        : (item.duration || 0);
    return {
        kind: automationKindFor(item),
        title: item.title || item.name || '',
        duration,
        cues: {
            cue_in: cues.cue_in,
            cue_out: cues.cue_out,
            intro: cues.intro,
            outro: cues.outro,
            start_next: cues.start_next,
        },
    };
}

function automationItemKey(item) {
    if (!item) return '';
    return item.token || item.id || item.name || item.title || item.url || '';
}

function buildAutomationPlanKey(plan, context) {
    if (!plan || !context) return '';
    return [
        automationItemKey(context.currentItem),
        automationItemKey(context.overlayItem),
        automationItemKey(context.nextItem),
        plan.paused ? 'paused' : 'active',
        plan.fade ? plan.fade.action : '',
        plan.overlay ? plan.overlay.status : '',
    ].join('|');
}

function getAutomationQueueContext() {
    if (!currentItem) return null;
    const remaining = queue.slice();
    const overlayItem = remaining.find(item => overlayEligible(item)) || null;
    const nextItem = remaining.find(item => !overlayEligible(item) && !item.stop) || null;
    const currentDuration = activePlayer().duration;
    return {
        currentItem,
        overlayItem,
        nextItem,
        currentPayload: buildAutomationItem(currentItem, currentDuration),
        overlayPayload: buildAutomationItem(overlayItem),
        nextPayload: buildAutomationItem(nextItem),
    };
}

async function automationTick() {
    try {
        await fetch('/api/show-automator/state');
    } catch (err) {
        // ignore
    }

    if (automationMode !== 'automation') return;
    if (!currentItem) return;
    const context = getAutomationQueueContext();
    if (!context || !context.currentPayload) return;
    const player = activePlayer();
    const position = (!player || isNaN(player.currentTime)) ? 0 : player.currentTime;
    const payload = {
        current_position: position,
        current: context.currentPayload,
        next: context.nextPayload,
        overlay: context.overlayPayload,
    };
    try {
        const res = await fetch('/api/show-automator/plan', {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify(payload),
        });
        const data = await res.json();
        if (res.ok && data.plan) {
            automationPlan = data.plan;
            automationContext = context;
            scheduleAutomationPlan(data.plan, context);
        }
    } catch (err) {
        // ignore
    }
}

function scheduleAutomationPlan(plan, context) {
    if (!plan || automationMode !== 'automation' || !context) return;
    const key = buildAutomationPlanKey(plan, context);
    if (automationPlanKey === key) return;
    automationPlanKey = key;
    clearAutomationTimers();
    if (plan.paused) return;

    if (plan.overlay && plan.overlay.status === 'scheduled' && context.overlayItem) {
        const delay = Math.max(0, (plan.overlay.start_in || 0) * 1000);
        automationOverlayTimer = setTimeout(() => {
            if (automationMode !== 'automation') return;
            if (currentItem !== context.currentItem) return;
            playOverlayItem(context.overlayItem);
        }, delay);
    }

    if (plan.fade && plan.fade.start_in != null && ['crossfade', 'fade_out'].includes(plan.fade.action)) {
        const delay = Math.max(0, plan.fade.start_in * 1000);
        automationFadeTimer = setTimeout(() => {
            if (automationMode !== 'automation') return;
            if (currentItem !== context.currentItem) return;
            if (queue.length > 0) {
                fadeAndNext('automation');
            } else {
                fadeOutCurrent();
            }
        }, delay);
    }
}

function playOverlayItem(item) {
    if (!item || !item.url) return;
    const idx = queue.indexOf(item);
    if (idx !== -1) {
        const [removed] = queue.splice(idx, 1);
        dequeueQueueItem(removed);
        renderQueue();
    }
    overlayPlayer.src = item.url;
    overlayPlayer.currentTime = 0;
    overlayPlayer.volume = 1;
    try { overlayPlayer.play(); } catch (e) { /* ignore */ }
}

function fadeOutCurrent() {
    const player = activePlayer();
    if (!player || !player.src) return;
    stopFade();
    fadeTimer = setInterval(() => {
        player.volume = Math.max(0, player.volume - 0.06);
        if (player.volume <= 0.02) {
            stopFade();
            player.pause();
            player.volume = 1;
            player.removeAttribute('src');
            currentItem = null;
            syncNowPlaying(null, 'idle');
            resetTimers();
            renderQueue();
        }
    }, 80);
}

function previewCues(item) {
    if (!item) { resetTimers(); return; }
    const cues = item.cues || {};
    timerEls.forEach(el => {
        if (!el) return;
        el.textContent = '00:00.00';
        el.style.color = '';
        el.style.fontWeight = '400';
    });
    const introVal = cues.intro ? cues.intro.toFixed(1) : '0.0';
    introBadge.style.display = 'inline-block';
    introBadge.textContent = `Intro: ${introVal}s`;
    introBadge.classList.remove('countdown-flash', 'text-bg-danger');

    const outroVal = cues.outro ? cues.outro.toFixed(1) : '0.0';
    outroBadge.style.display = 'inline-block';
    outroBadge.textContent = `Outro: ${outroVal}s`;
    outroBadge.classList.remove('countdown-flash', 'text-bg-danger');
    updateTalkOverlay(cues, 0, 0);

    const hasLoop = cues.loop_in != null && cues.loop_out != null;
    loopBetween.disabled = !hasLoop;
    if (!hasLoop) loopBetween.checked = false;
}

function stopAllAudio({ sync = true } = {}) {
    players.forEach(p => { p.el.pause(); p.el.removeAttribute('src'); p.el.volume = 1; });
    currentItem = null;
    if (sync) {
        syncNowPlaying(null, 'idle');
    }
    resetTimers();
}

async function addToQueue(item) {
    const queuedItem = await enqueueQueueItem(item, queue.length);
    if (!queuedItem) return;
    queue.push(queuedItem);
    logItemInsert(queuedItem);
    updateVTInsertOptions();
    if (!currentItem && queue.length === 1 && activePlayer().paused) {
        startFrom(0, currentIdx, 'auto');
    }
    renderQueue();
}

async function insertToQueue(item, idx) {
    const position = Math.max(0, Math.min(queue.length, idx));
    const queuedItem = await enqueueQueueItem(item, position);
    if (!queuedItem) return;
    queue.splice(position, 0, queuedItem);
    logItemInsert(queuedItem);
    updateVTInsertOptions();
    if (!currentItem && position === 0 && activePlayer().paused) {
        startFrom(0, currentIdx, 'auto');
    }
    renderQueue();
}

async function removeFromQueue(idx) {
    if (idx < 0 || idx >= queue.length) return;
    const [removed] = queue.splice(idx, 1);
    dequeueQueueItem(removed);
    updateVTInsertOptions();
    renderQueue();
}

function addStopCue() {
    addToQueue({ name: 'STOP', title: 'STOP', stop: true, kind: 'stop' });
}

const TOP40_RATE = Math.pow(2, 0.5 / 12);
const OVERLAY_KINDS = ['voicetrack', 'overlay'];

function preservesPitchOff(el) {
    if (!el) return;
    el.preservesPitch = false;
    el.mozPreservesPitch = false;
    el.webkitPreservesPitch = false;
}

function computePlaybackRate(item) {
    const isMusic = item && (item.kind === 'music' || (item.category || '').toLowerCase() === 'music');
    if (top40.checked && isMusic) {
        return TOP40_RATE;
    }
    return 1.0;
}

function applyPlaybackRate(player = activePlayer(), item = currentItem) {
    preservesPitchOff(player);
    player.playbackRate = computePlaybackRate(item);
}

function overlayEligible(item) {
    // Only use the overlay deck for dedicated voice tracks/overlays to avoid
    // fighting the main deck when rolling into music intros.
    return item && OVERLAY_KINDS.includes(item.kind);
}

function startFrom(idx, preferredIdx = currentIdx, reason = 'manual', { syncNowPlayingState = true } = {}) {
    const item = queue[idx];
    if (!item) return;
    if (item.stop) {
        const removed = queue.splice(0, idx + 1);
        removed.forEach(entry => dequeueQueueItem(entry));
        if (currentItem) {
            logItemEnd(currentItem, 'stop');
        }
        stopAllAudio();
        renderQueue();
        if (queue.length) previewCues(queue[0]);
        return;
    }
    stopFade();
    const playerObj = players[preferredIdx];
    const otherObj = players[1 - preferredIdx];
    const player = playerObj.el;
    const other = otherObj.el;
    if (currentItem && currentItem !== item) {
        logItemEnd(currentItem, 'skip');
    }
    other.pause();
    other.removeAttribute('src');
    other.volume = 1;
    otherObj.item = null;
    currentIdx = preferredIdx;
    currentItem = item;
    playerObj.item = item;
    const cues = item.cues || {};
    const hasLoop = cues.loop_in != null && cues.loop_out != null;
    loopBetween.disabled = !hasLoop;
    if (!hasLoop) loopBetween.checked = false;
    autoNextTriggered = false;
    prestartedIdx = null;
    if (idx > 0) {
        const removed = queue.splice(0, idx);
        removed.forEach(entry => dequeueQueueItem(entry));
    }
    queue.shift();
    if (syncNowPlayingState) {
        syncNowPlaying(item, 'playing');
    }
    player.src = item.url;
    player.volume = 1;
    applyPlaybackRate(player, item);
    player.play();
    logItemStart(item, reason);
    togglePauseBtn.textContent = 'Pause';
    renderQueue();
}

function shiftPastStops() {
    while (queue.length && queue[0].stop) {
        const removed = queue.shift();
        dequeueQueueItem(removed);
    }
}

function playNext(reason = 'skip') {
    if (!queue.length && !currentItem) return;
    if (currentItem) {
        logItemEnd(currentItem, reason);
    }
    stopFade();
    stopAllAudio({ sync: false });
    autoNextTriggered = false;
    prestartedIdx = null;
    if (queue.length) {
        syncSkip();
        startFrom(0, currentIdx, 'auto', { syncNowPlayingState: false });
    } else {
        syncNowPlaying(null, 'idle');
        renderQueue();
    }
}

function startNextWithOverlay(reason = 'auto') {
    if (queue.length <= 0) {
        fadeAndNext(reason);
        return;
    }
    if (queue[0] && queue[0].stop) {
        playNext(reason);
        return;
    }

    const current = activePlayer();
    const currentPlaying = currentItem;
    const nextItem = queue[0];

    try {
        overlayPlayer.pause();
        overlayPlayer.src = current.src || '';
        overlayPlayer.currentTime = current.currentTime || 0;
        overlayPlayer.playbackRate = computePlaybackRate(currentPlaying);
        overlayPlayer.volume = current.volume;
        if (overlayPlayer.src) {
            overlayPlayer.play().catch(() => {});
        }
    } catch (e) {
        // ignore overlay errors
    }

    current.pause();
    current.removeAttribute('src');

    shiftPastStops();

    const nextIdx = 1 - currentIdx;
    const nextObj = players[nextIdx];
    const nextPlayer = nextObj.el;
    stopFade();
    nextObj.item = nextItem;
    nextPlayer.src = nextItem.url;
    nextPlayer.volume = 1;
    currentIdx = nextIdx;
    currentItem = nextItem;
    queue.shift();
    prestartedIdx = null;
    autoNextTriggered = false;
    applyPlaybackRate(nextPlayer, nextItem);
    nextPlayer.play();
    if (reason !== 'auto') {
        syncSkip();
    } else {
        syncNowPlaying(nextItem, 'playing');
    }
    if (currentPlaying) {
        logItemEnd(currentPlaying, reason);
    }
    logItemStart(nextItem, reason);
    startTimer();
    renderQueue();
}

function fadeAndNext(reason = 'auto') {
    if (queue.length <= 0 || (queue[0] && queue[0].stop)) {
        playNext(reason);
        return;
    }
    const current = activePlayer();
    const nextIdx = 1 - currentIdx;
    const nextItem = queue[0];
    const nextPlayerObj = players[nextIdx];
    const nextPlayer = nextPlayerObj.el;
    stopFade();
    nextPlayerObj.item = nextItem;
    nextPlayer.src = nextItem.url;
    nextPlayer.volume = 1;
    applyPlaybackRate(nextPlayer, nextItem);
    nextPlayer.play();
    if (reason !== 'auto') {
        syncSkip();
    } else {
        syncNowPlaying(nextItem, 'playing');
    }
    if (currentItem) {
        logItemEnd(currentItem, reason);
    }
    logItemStart(nextItem, reason);
    fadeTimer = setInterval(() => {
        current.volume = Math.max(0, current.volume - 0.06);
        if (current.volume <= 0.02) {
            stopFade();
            current.pause();
            current.volume = 1;
            current.removeAttribute('src');
            shiftPastStops();
            currentIdx = nextIdx;
            currentItem = nextItem;
            queue.shift();
            prestartedIdx = null;
            applyPlaybackRate();
            startTimer();
            renderQueue();
        }
    }, 80);
}

function stopFade() { if (fadeTimer) { clearInterval(fadeTimer); fadeTimer = null; } }

function updateTimer() {
    const player = activePlayer();
    if (!currentItem || !player.src || !player.duration || isNaN(player.duration)) {
        resetTimers();
        return;
    }

    const cues = currentItem && currentItem.cues ? currentItem.cues : {};
    let remaining = Math.max(0, player.duration - player.currentTime);
    if (queue.length > 0 && cues.start_next) {
        remaining = Math.max(0, cues.start_next - player.currentTime);
    }

    const minutes = Math.floor(remaining / 60);
    const seconds = Math.floor(remaining % 60);
    const ms = Math.floor((remaining - Math.floor(remaining)) * 100);
    const text = `${String(minutes).padStart(2,'0')}:${String(seconds).padStart(2,'0')}.${String(ms).padStart(2,'0')}`;
    const urgent = remaining > 0 && remaining <= 15;
    timerEls.forEach(el => {
        if (!el) return;
        el.textContent = text;
        el.style.color = urgent ? '#dc3545' : '';
        el.style.fontWeight = urgent ? '700' : '400';
    });

    if (cues.intro) {
        const introRem = Math.max(0, cues.intro - player.currentTime);
        introBadge.style.display = 'inline-block';
        introBadge.textContent = `Intro: ${introRem.toFixed(1)}s`;
        const flash = introRem > 0 && introRem <= 5;
        introBadge.classList.toggle('countdown-flash', flash);
        introBadge.classList.toggle('text-bg-danger', flash);
    } else {
        introBadge.style.display = 'inline-block';
        introBadge.textContent = 'Intro: 0.0s';
        introBadge.classList.remove('countdown-flash','text-bg-danger');
    }

    if (cues.outro) {
        const outroRem = Math.max(0, cues.outro - player.currentTime);
        outroBadge.style.display = 'inline-block';
        outroBadge.textContent = `Outro: ${outroRem.toFixed(1)}s`;
        const flash = outroRem > 0 && outroRem <= 5;
        outroBadge.classList.toggle('countdown-flash', flash);
        outroBadge.classList.toggle('text-bg-danger', flash);
    } else {
        outroBadge.style.display = 'inline-block';
        outroBadge.textContent = 'Outro: 0.0s';
        outroBadge.classList.remove('countdown-flash','text-bg-danger');
    }
    updateTalkOverlay(cues, player.currentTime, player.duration);

    if (automationMode !== 'automation') {
        // Auto-advance when start_next cue is hit.
        if (!autoNextTriggered && cues.start_next && player.currentTime >= cues.start_next) {
            autoNextTriggered = true;
            resetTimers();
            if (queue.length > 0) {
                if (overlayEligible(currentItem)) {
                    startNextWithOverlay('auto');
                } else {
                    fadeAndNext('auto');
                }
            }
        }
    }

    if (loopBetween.checked && cues.loop_in != null && cues.loop_out != null) {
        const loopIn = cues.loop_in;
        const loopOut = cues.loop_out;
        if (player.currentTime >= loopOut - 0.05) {
            player.currentTime = loopIn;
            try { player.play(); } catch (e) { /* ignore */ }
        }
    }

    // Voice track smart start for next track intro/outro.
    if (automationMode !== 'automation' && !prestartedIdx && currentItem && currentItem.kind === 'voicetrack' && queue.length > 0) {
        const nextItem = queue[0];
        const nextCues = nextItem.cues || {};
        const target = nextCues.intro || nextCues.outro;
        if (target && remaining <= target + 0.25) {
            const nextIdxDeck = 1 - currentIdx;
            const nextPlayerObj = players[nextIdxDeck];
            const nextPlayer = nextPlayerObj.el;
            nextPlayerObj.item = nextItem;
            nextPlayer.src = nextItem.url;
            nextPlayer.volume = 1;
            applyPlaybackRate(nextPlayer, nextItem);
            try { nextPlayer.play(); } catch (e) { /* ignore */ }
            prestartedIdx = nextIdxDeck;
        }
    }
}

function startTimer() { stopTimer(); timerInterval = setInterval(updateTimer, 80); }
function stopTimer() { if (timerInterval) { clearInterval(timerInterval); timerInterval = null; } }

function handleEnded(idx) {
    if (idx !== currentIdx) return;
    stopTimer();
    if (currentItem) {
        logItemEnd(currentItem, 'ended');
    }
    shiftPastStops();
    if (queue.length) {
        if (prestartedIdx !== null && players[prestartedIdx].item === queue[0]) {
            currentIdx = prestartedIdx;
            currentItem = queue[0];
            prestartedIdx = null;
            applyPlaybackRate();
            queue.shift();
            syncNowPlaying(currentItem, 'playing');
            logItemStart(currentItem, 'auto');
            startTimer();
            renderQueue();
        } else {
            startFrom(0, currentIdx, 'auto');
        }
    } else {
        stopAllAudio();
        renderQueue();
    }
}

document.getElementById('clearQueue').addEventListener('click', () => {
    const removed = queue.splice(0, queue.length);
    removed.forEach(entry => dequeueQueueItem(entry));
    stopFade();
    stopAllAudio();
    renderQueue();
});
togglePauseBtn.addEventListener('click', () => {
    const player = activePlayer();
    if (!currentItem || !player || !player.src) return;
    if (player.paused) {
        player.play();
        togglePauseBtn.textContent = 'Pause';
    } else {
        player.pause();
        togglePauseBtn.textContent = 'Play';
    }
});
playNextBtn.addEventListener('click', () => playNext('manual'));
addStopBtn.addEventListener('click', addStopCue);
fadeOutBtn.addEventListener('click', () => fadeAndNext('manual'));
document.getElementById('overlayPlay').addEventListener('click', () => {
    const url = document.getElementById('overlayUrl').value.trim();
    if (!url) return;
    overlayPlayer.src = url;
    overlayPlayer.play();
});
document.getElementById('toggleTalkup').addEventListener('click', () => {
    talkUpMode = !talkUpMode;
    document.getElementById('toggleTalkup').classList.toggle('btn-dark', talkUpMode);
    document.getElementById('toggleTalkup').classList.toggle('btn-outline-dark', !talkUpMode);
    updateTalkOverlay(currentItem ? (currentItem.cues || {}) : null, activePlayer().currentTime || 0, activePlayer().duration || 0);
});

modeToggleButtons.forEach(btn => {
    btn.addEventListener('click', () => setAutomationMode(btn.dataset.automationMode));
});

document.getElementById('psaSearch').addEventListener('input', (e) => renderLibrary(e.target.value || ''));
categoryFilter.addEventListener('change', () => renderLibrary(document.getElementById('psaSearch').value || ''));
document.getElementById('refreshPsa').addEventListener('click', () => loadPSAs());
top40.addEventListener('change', () => {
    players.forEach((p) => {
        preservesPitchOff(p.el);
        if (p.item) {
            p.el.playbackRate = computePlaybackRate(p.item);
        }
    });
});

players.forEach((p, idx) => {
    preservesPitchOff(p.el);
    p.el.addEventListener('play', () => { if (idx === currentIdx) { startTimer(); applyPlaybackRate(p.el, p.item); togglePauseBtn.textContent = 'Pause'; } });
    p.el.addEventListener('loadedmetadata', () => { if (p.item) { applyPlaybackRate(p.el, p.item); } });
    p.el.addEventListener('pause', () => { if (idx === currentIdx) { stopTimer(); togglePauseBtn.textContent = 'Play'; } });
    p.el.addEventListener('ended', () => handleEnded(idx));
});

overlayPlayer.addEventListener('ended', () => {
    overlayPlayer.pause();
    overlayPlayer.removeAttribute('src');
    overlayPlayer.currentTime = 0;
});

async function loadPSAs() {
    try {
        const params = new URLSearchParams({
            page: libraryPage,
            per_page: libraryPerPage,
        });
        if (categoryFilter.value) params.set('category', categoryFilter.value);
        if (libraryQuery) params.set('q', libraryQuery);
        const res = await fetch(`/api/psa/library?${params.toString()}`);
        const data = await res.json();
        library = data.items || [];
        libraryTotal = data.total || 0;
        renderCategories(data.categories || []);
        renderLibrary();
        const start = libraryTotal ? ((data.page - 1) * data.per_page) + 1 : 0;
        const end = Math.min(libraryTotal, data.page * data.per_page);
        libraryMeta.textContent = libraryTotal
            ? `Showing ${start}-${end} of ${libraryTotal}`
            : 'No items found.';
        libraryPrev.disabled = data.page <= 1;
        libraryNext.disabled = end >= libraryTotal;
    } catch (e) {
        psaList.innerHTML = '<li class="list-group-item text-danger">Unable to load media.</li>';
    }
}

if (!legacyPlayerEnabled) {
    return;
}
loadPSAs();
loadPlaybackQueue();
loadAutomationMode().then(() => ensureShowRun());
updateShowLogExport();

window.addEventListener('beforeunload', () => {
    if (!activeShowRunId) return;
    const payload = new Blob([JSON.stringify({ show_run_id: activeShowRunId, log_sheet_id: activeLogSheetId })], { type: 'application/json' });
    navigator.sendBeacon('/api/playback/show/stop', payload);
});

libraryPrev.addEventListener('click', async () => {
    if (libraryPage <= 1) return;
    libraryPage -= 1;
    await loadPSAs();
});
libraryNext.addEventListener('click', async () => {
    libraryPage += 1;
    await loadPSAs();
});
categoryFilter.addEventListener('change', async () => {
    libraryPage = 1;
    await loadPSAs();
});
let searchTimer = null;
psaSearch.addEventListener('input', () => {
    if (searchTimer) clearTimeout(searchTimer);
    searchTimer = setTimeout(async () => {
        libraryQuery = psaSearch.value.trim();
        libraryPage = 1;
        await loadPSAs();
    }, 250);
});

// Voice track recording
const startRecBtn = document.getElementById('startRecord');
const stopRecBtn = document.getElementById('stopRecord');
const vtFile = document.getElementById('vtFile');
const vtImport = document.getElementById('vtImport');
const vtMeta = document.getElementById('vtMeta');
const vtWaveCanvas = document.getElementById('vtWaveCanvas');
const vtWaveInner = document.getElementById('vtWaveInner');
const vtInsertPosition = document.getElementById('vtInsertPosition');
const vtTitle = document.getElementById('vtTitle');
const vtHost = document.getElementById('vtHost');
const vtNotes = document.getElementById('vtNotes');

function vtDisplayMeta(text) {
    vtMeta.textContent = text;
}

function updateVTInsertOptions() {
    if (!vtInsertPosition) return;
    const options = [];
    options.push({ value: 'next', label: 'Next up' });
    options.push({ value: 'end', label: 'End of queue' });
    if (currentItem) {
        options.push({ value: 'after-current', label: `After: ${currentItem.name}` });
    }
    queue.forEach((item, idx) => {
        options.push({ value: `after-${idx}`, label: `After: ${item.name}` });
    });
    vtInsertPosition.innerHTML = options.map(opt => `<option value="${opt.value}">${opt.label}</option>`).join('');
}

function collectVTMeta() {
    return {
        title: vtTitle.value.trim(),
        host: vtHost.value.trim(),
        notes: vtNotes.value.trim(),
    };
}

function syncPendingVTMeta() {
    if (!pendingVT) return;
    const meta = collectVTMeta();
    const title = meta.title || 'Voice Track';
    pendingVT.name = title;
    pendingVT.metadata = meta;
    vtDisplayMeta(`${title}${pendingVT.duration ? ` • ${pendingVT.duration.toFixed(1)}s` : ''}`);
    renderQueue();
}

async function decodeAudioBufferFromBlob(blob) {
    const arrayBuffer = await blob.arrayBuffer();
    const ctx = new (window.AudioContext || window.webkitAudioContext)();
    return await ctx.decodeAudioData(arrayBuffer);
}

function drawWaveform(buffer, canvas, container) {
    if (!buffer || !canvas || !container) return;
    const width = Math.max(container.clientWidth || 600, 600);
    const height = canvas.height;
    canvas.width = width;
    canvas.style.width = `${width}px`;
    container.style.width = `${width}px`;
    const ctx = canvas.getContext('2d');
    ctx.clearRect(0, 0, width, height);
    const data = buffer.getChannelData(0);
    const step = Math.ceil(data.length / width);
    const amp = height / 2;
    ctx.fillStyle = '#f1f3f5';
    ctx.fillRect(0, 0, width, height);
    ctx.strokeStyle = '#0d6efd';
    ctx.beginPath();
    for (let i = 0; i < width; i++) {
        let min = 1.0;
        let max = -1.0;
        for (let j = 0; j < step; j++) {
            const datum = data[(i * step) + j];
            if (datum < min) min = datum;
            if (datum > max) max = datum;
        }
        ctx.moveTo(i, (1 + min) * amp);
        ctx.lineTo(i, (1 + max) * amp);
    }
    ctx.stroke();
}

function updatePendingVTFromBlob(blob, duration = null) {
    pendingVTBlob = blob;
    if (pendingVTUrl) URL.revokeObjectURL(pendingVTUrl);
    pendingVTUrl = URL.createObjectURL(blob);
    const meta = collectVTMeta();
    const title = meta.title || 'Voice Track';
    pendingVT = {
        name: title,
        url: pendingVTUrl,
        category: 'Voice Tracks',
        kind: 'voicetrack',
        duration,
        metadata: meta,
    };
    document.getElementById('vtPending').classList.remove('d-none');
    vtDisplayMeta(`${title}${duration ? ` • ${duration.toFixed(1)}s` : ''}`);
}

vtImport.addEventListener('click', () => vtFile.click());
vtFile.addEventListener('change', async (e) => {
    const file = e.target.files[0];
    if (!file) return;
    try {
        const buffer = await decodeAudioBufferFromBlob(file);
        drawWaveform(buffer, vtWaveCanvas, vtWaveInner);
        updatePendingVTFromBlob(file, buffer.duration);
    } catch (err) {
        alert('Unable to import this audio file.');
    }
});

[vtTitle, vtHost, vtNotes].forEach(field => {
    field.addEventListener('input', syncPendingVTMeta);
});

startRecBtn.addEventListener('click', async () => {
    if (recorder) return;
    try {
        const stream = await navigator.mediaDevices.getUserMedia({ audio: true });
        recorder = new MediaRecorder(stream);
        recordChunks = [];
        recorder.ondataavailable = (e) => { if (e.data.size > 0) recordChunks.push(e.data); };
        recorder.onstop = () => {
            const blob = new Blob(recordChunks, { type: 'audio/webm' });
            decodeAudioBufferFromBlob(blob).then(buffer => {
                drawWaveform(buffer, vtWaveCanvas, vtWaveInner);
                updatePendingVTFromBlob(blob, buffer.duration);
            }).catch(() => updatePendingVTFromBlob(blob, null));
            recorder = null;
            stopRecBtn.disabled = true;
            startRecBtn.disabled = false;
        };
        recorder.start();
        startRecBtn.disabled = true;
        stopRecBtn.disabled = false;
    } catch (err) {
        alert('Unable to access microphone for recording.');
        recorder = null;
    }
});
stopRecBtn.addEventListener('click', () => { if (recorder) recorder.stop(); });

document.getElementById('vtConfirm').addEventListener('click', () => {
    if (pendingVT) {
        const value = vtInsertPosition.value;
        if (value === 'next') {
            insertToQueue(pendingVT, 0);
        } else if (value === 'end') {
            addToQueue(pendingVT);
        } else if (value === 'after-current') {
            insertToQueue(pendingVT, 0);
        } else if (value.startsWith('after-')) {
            const idx = parseInt(value.replace('after-', ''), 10);
            insertToQueue(pendingVT, idx + 1);
        } else {
            addToQueue(pendingVT);
        }
    }
    document.getElementById('vtPending').classList.add('d-none');
    pendingVT = null;
});
document.getElementById('vtDelete').addEventListener('click', () => {
    if (pendingVTUrl) URL.revokeObjectURL(pendingVTUrl);
    pendingVTUrl = null;
    pendingVTBlob = null;
    pendingVT = null;
    document.getElementById('vtPending').classList.add('d-none');
});

function updateTalkOverlay(cues, currentTime = 0, duration = 0) {
    if (!talkUpMode || !cues) {
        talkOverlay.classList.add('d-none');
        return;
    }
    const intro = cues.intro || 0;
    const outro = cues.outro || 0;
    const introRemaining = Math.max(0, intro - currentTime);
    const outroRemaining = Math.max(0, outro - currentTime);
    talkOverlayIntro.textContent = `${introRemaining.toFixed(1)}s`;
    talkOverlayOutro.textContent = `${outroRemaining.toFixed(1)}s`;
    talkOverlay.classList.toggle('d-none', !intro && !outro);
    talkOverlayIntro.classList.toggle('text-warning', introRemaining > 0 && introRemaining <= 5);
    talkOverlayOutro.classList.toggle('text-warning', outroRemaining > 0 && outroRemaining <= 5);
}

function fmtCueTime(t) {
    if (t === '' || t === null || t === undefined || isNaN(t)) return '';
    const ms = Math.floor((t % 1) * 100);
    const total = Math.floor(t);
    const min = Math.floor(total/60).toString().padStart(2,'0');
    const sec = (total % 60).toString().padStart(2,'0');
    return `${min}:${sec}.${ms.toString().padStart(2,'0')}`;
}

function paintCueSwatches() {
    document.querySelectorAll('.cue-swatch').forEach(el => {
        const key = el.dataset.cue;
        if (key && cueColorMap[key]) {
            el.style.color = cueColorMap[key];
        }
    });
}

function selectCueButton(key) {
    cueSelected = key;
    cueFields.forEach(f => {
        const btn = document.getElementById(`btn-${f}`);
        if (btn) btn.classList.toggle('active', f === key);
    });
    updateCueMarkers();
}

function updateCueMarkers() {
    cueMarkers.innerHTML = '';
    cueFields.forEach(field => {
        const val = parseFloat(cueInputs[field]?.value);
        const label = document.getElementById(`time-${field}`);
        if (label) label.textContent = val ? fmtCueTime(val) : '';
        if (!cuePlayer.duration || isNaN(val)) return;
        const pct = (val / cuePlayer.duration) * 100;
        if (pct < 0 || pct > 100) return;
        const marker = document.createElement('div');
        marker.className = 'marker handle';
        marker.dataset.cue = field;
        marker.style.left = `${pct}%`;
        marker.style.background = cueColorMap[field] || '#0d6efd';
        cueMarkers.appendChild(marker);
    });
}

function updateCueNeedle() {
    if (!cuePlayer || !cuePlayer.duration || !cueNeedle) return;
    const pct = (cuePlayer.currentTime / cuePlayer.duration) * 100;
    cueNeedle.style.left = `${pct}%`;
}

function nudgeCue(delta) {
    if (!cuePlayer || isNaN(cuePlayer.currentTime)) return;
    const wasPlaying = !cuePlayer.paused;
    cuePlayer.currentTime = Math.max(0, Math.min(cuePlayer.duration || 0, cuePlayer.currentTime + delta));
    updateCueNeedle();
    if (wasPlaying) cuePlayer.play().catch(() => {});
}

async function drawCueWaveformFromUrl(url) {
    try {
        const res = await fetch(url);
        const arrayBuffer = await res.arrayBuffer();
        const ctx = new (window.AudioContext || window.webkitAudioContext)();
        const buffer = await ctx.decodeAudioData(arrayBuffer);
        cueBaseWidth = Math.max(cueWave.clientWidth || 600, 600);
        cueWaveCanvas.width = cueBaseWidth;
        cueWaveCanvas.style.width = `${cueBaseWidth}px`;
        cueWaveInner.style.width = `${cueBaseWidth}px`;
        cueMarkers.style.width = `${cueBaseWidth}px`;
        drawWaveform(buffer, cueWaveCanvas, cueWaveInner);
    } catch (err) {
        const ctx = cueWaveCanvas.getContext('2d');
        ctx.clearRect(0,0,cueWaveCanvas.width, cueWaveCanvas.height);
        ctx.fillStyle = '#6c757d';
        ctx.fillText('Waveform unavailable', 10, cueWaveCanvas.height/2);
    }
}

function selectCueItem(item) {
    if (!item) return;
    cueItem = item;
    cueItemLabel.textContent = `${item.name} (${item.category || 'media'})`;
    cuePlayer.disabled = false;
    cuePlayer.src = item.url;
    cuePlayer.load();
    cueFields.forEach(field => {
        cueInputs[field].value = item.cues && item.cues[field] != null ? Number(item.cues[field]).toFixed(2) : '';
    });
    updateCueMarkers();
    drawCueWaveformFromUrl(item.url);
}

cueFields.forEach(field => {
    cueInputs[field] = document.createElement('input');
});

document.querySelectorAll('.cue-btn').forEach(btn => {
    btn.addEventListener('click', () => selectCueButton(btn.dataset.cue));
});
document.getElementById('cueStepBack').addEventListener('click', () => nudgeCue(-0.05));
document.getElementById('cueStepForward').addEventListener('click', () => nudgeCue(0.05));
document.getElementById('cueToggle').addEventListener('click', () => {
    if (cuePlayer.paused) cuePlayer.play(); else cuePlayer.pause();
});
document.getElementById('cueSet').addEventListener('click', () => {
    if (!cuePlayer || isNaN(cuePlayer.currentTime)) return;
    cueInputs[cueSelected].value = cuePlayer.currentTime.toFixed(2);
    updateCueMarkers();
});
document.getElementById('cueRemove').addEventListener('click', () => {
    cueInputs[cueSelected].value = '';
    updateCueMarkers();
});
document.getElementById('cueSave').addEventListener('click', async () => {
    if (!cueItem || !cueItem.token) return;
    const payload = {};
    cueFields.forEach(field => {
        const val = parseFloat(cueInputs[field].value);
        if (!isNaN(val)) payload[field] = val;
    });
    try {
        const res = await fetch('/api/psa/cue', {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({ token: cueItem.token, cue: payload }),
        });
        const data = await res.json();
        if (data.status === 'ok') {
            cueItem.cues = data.cue || payload;
            renderQueue();
            renderLibrary(document.getElementById('psaSearch').value || '');
        } else {
            alert('Unable to save cues.');
        }
    } catch (err) {
        alert('Unable to save cues.');
    }
});

cuePlayer.addEventListener('timeupdate', updateCueNeedle);
cuePlayer.addEventListener('loadedmetadata', updateCueMarkers);

cueWave.addEventListener('mousedown', (e) => {
    if (!cuePlayer.duration) return;
    const rect = cueWave.getBoundingClientRect();
    const x = e.clientX - rect.left + cueWave.scrollLeft;
    const pct = Math.max(0, Math.min(1, x / cueWave.clientWidth));
    cuePlayer.currentTime = pct * cuePlayer.duration;
    updateCueNeedle();
});

cueMarkers.addEventListener('mousedown', (e) => {
    if (!e.target.dataset.cue) return;
    cueDragField = e.target.dataset.cue;
    cueDragNeedle = false;
});
cueNeedle.addEventListener('mousedown', () => { cueDragNeedle = true; cueDragField = null; });
document.addEventListener('mouseup', () => { cueDragNeedle = false; cueDragField = null; });
document.addEventListener('mousemove', (e) => {
    if ((!cueDragNeedle && !cueDragField) || !cuePlayer.duration) return;
    const rect = cueWave.getBoundingClientRect();
    const x = e.clientX - rect.left + cueWave.scrollLeft;
    const pct = Math.max(0, Math.min(1, x / cueWave.clientWidth));
    const seconds = pct * cuePlayer.duration;
    if (cueDragNeedle) {
        cuePlayer.currentTime = seconds;
    } else if (cueDragField) {
        cueInputs[cueDragField].value = seconds.toFixed(2);
    }
    updateCueNeedle();
    updateCueMarkers();
});

paintCueSwatches();
selectCueButton('cue_in');
updateVTInsertOptions();
})();
