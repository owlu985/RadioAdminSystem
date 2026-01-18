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
    if (Number.isNaN(total) || total <= 0) return '00:00';
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
    ensureLibraryNavigation();
    updateLibraryQueueList(queueItems || []);
}

function updatePlaybackPanels() {
    if (!playbackPanelsEnabled) return;
    const nowPlaying = currentItem ? { ...currentItem, queue_item_id: currentItem.id } : null;
    const upcoming = queue.map((item, idx) => ({ ...item, position: idx + 1 }));
    const snapshotQueue = currentItem
        ? [{ ...currentItem, position: 0 }, ...upcoming]
        : upcoming;
    renderNowPlaying(nowPlaying, null);
    renderLiveQueue(snapshotQueue, nowPlaying);
    renderQueueBuilder(snapshotQueue);
    renderLibraryNavigation(queue, null);
}

const libraryNavState = {
    mode: 'music',
    query: '',
    page: 1,
    perPage: 20,
    total: 0,
    items: [],
    loading: false,
    error: null,
    lastKey: null,
    lastFetchedAt: 0,
    pendingKey: null,
};
let libraryNavInitialized = false;
let libraryNavElements = {};
let libraryNavDrag = null;

function base64UrlEncode(value) {
    if (!value) return '';
    const bytes = new TextEncoder().encode(value);
    let binary = '';
    bytes.forEach((byte) => { binary += String.fromCharCode(byte); });
    const encoded = btoa(binary);
    return encoded.replace(/\+/g, '-').replace(/\//g, '_').replace(/=+$/, '');
}

function buildLibraryQueuePayload(item, mode) {
    if (mode === 'music') {
        const title = item.title || item.path || 'Untitled';
        const token = item.path ? base64UrlEncode(item.path) : null;
        return {
            title,
            name: title,
            artist: item.artist || null,
            duration: item.duration_seconds || null,
            url: token ? `/media/file/${token}` : null,
            category: item.genre || 'Music',
            kind: 'music',
            metadata: {
                album: item.album || null,
                genre: item.genre || null,
                year: item.year || null,
                folder: item.folder || null,
                path: item.path || null,
            },
        };
    }
    return {
        title: item.title || item.name || 'Untitled',
        name: item.name || item.title || 'Untitled',
        artist: item.artist || null,
        duration: item.duration || null,
        url: item.url || null,
        category: item.category || null,
        token: item.token || null,
        loop: item.loop || null,
        kind: item.kind || mode,
        metadata: {
            usage_rules: item.usage_rules || null,
            library_category: item.library_category || null,
        },
        cues: item.cues || null,
    };
}

function libraryItemDescription(item, mode) {
    if (mode === 'music') {
        const parts = [item.artist, item.album, item.genre].filter(Boolean);
        return parts.join(' • ') || 'Music track';
    }
    const parts = [item.category, item.usage_rules].filter(Boolean);
    return parts.join(' • ') || mode.toUpperCase();
}

function ensureLibraryNavigation() {
    if (!libraryNavigationPanel || libraryNavInitialized) return;
    libraryNavigationPanel.innerHTML = `
        <div class="d-flex flex-wrap gap-2 align-items-center mb-3">
            <div class="btn-group btn-group-sm" role="group" aria-label="Library types">
                <button class="btn btn-outline-primary" data-library-mode="music">Music</button>
                <button class="btn btn-outline-primary" data-library-mode="psa">PSA</button>
                <button class="btn btn-outline-primary" data-library-mode="imaging">Imaging</button>
            </div>
            <div class="input-group input-group-sm" style="max-width: 320px;">
                <span class="input-group-text">Search</span>
                <input type="text" class="form-control" id="libraryNavSearch" placeholder="Title, artist, or filename">
            </div>
            <button class="btn btn-outline-secondary btn-sm" id="libraryNavRefresh">Refresh</button>
            <span class="text-muted small" id="libraryNavMeta"></span>
        </div>
        <div class="row g-3">
            <div class="col-lg-7">
                <div class="border rounded p-2 bg-body-tertiary">
                    <div id="libraryNavStatus" class="text-muted small mb-2">Loading library…</div>
                    <ul id="libraryNavResults"></ul>
                    <div class="d-flex justify-content-between align-items-center mt-2">
                        <button class="btn btn-outline-secondary btn-sm" id="libraryNavPrev">Prev</button>
                        <button class="btn btn-outline-secondary btn-sm" id="libraryNavNext">Next</button>
                    </div>
                </div>
            </div>
            <div class="col-lg-5">
                <div class="d-flex justify-content-between align-items-center mb-2">
                    <strong>Queue Builder</strong>
                    <span class="text-muted small">Drag items here</span>
                </div>
                <ul id="libraryNavQueue"></ul>
            </div>
        </div>
    `;

    libraryNavElements = {
        modeButtons: Array.from(libraryNavigationPanel.querySelectorAll('[data-library-mode]')),
        searchInput: libraryNavigationPanel.querySelector('#libraryNavSearch'),
        refreshBtn: libraryNavigationPanel.querySelector('#libraryNavRefresh'),
        meta: libraryNavigationPanel.querySelector('#libraryNavMeta'),
        status: libraryNavigationPanel.querySelector('#libraryNavStatus'),
        results: libraryNavigationPanel.querySelector('#libraryNavResults'),
        prevBtn: libraryNavigationPanel.querySelector('#libraryNavPrev'),
        nextBtn: libraryNavigationPanel.querySelector('#libraryNavNext'),
        queue: libraryNavigationPanel.querySelector('#libraryNavQueue'),
    };

    libraryNavElements.modeButtons.forEach(btn => {
        btn.addEventListener('click', () => {
            setLibraryNavMode(btn.dataset.libraryMode);
        });
    });

    if (libraryNavElements.searchInput) {
        let searchTimer = null;
        libraryNavElements.searchInput.addEventListener('input', (e) => {
            if (searchTimer) clearTimeout(searchTimer);
            searchTimer = setTimeout(() => {
                libraryNavState.query = e.target.value.trim();
                libraryNavState.page = 1;
                fetchLibraryNav();
            }, 250);
        });
    }

    if (libraryNavElements.refreshBtn) {
        libraryNavElements.refreshBtn.addEventListener('click', () => fetchLibraryNav({ refresh: true }));
    }

    if (libraryNavElements.prevBtn) {
        libraryNavElements.prevBtn.addEventListener('click', () => {
            if (libraryNavState.page <= 1) return;
            libraryNavState.page -= 1;
            fetchLibraryNav();
        });
    }
    if (libraryNavElements.nextBtn) {
        libraryNavElements.nextBtn.addEventListener('click', () => {
            libraryNavState.page += 1;
            fetchLibraryNav();
        });
    }

    if (libraryNavElements.queue) {
        libraryNavElements.queue.addEventListener('dragover', (ev) => {
            if (!libraryNavDrag) return;
            ev.preventDefault();
            const target = ev.target.closest('li[data-queue-index]');
            if (target) target.classList.add('drop-target');
        });
        libraryNavElements.queue.addEventListener('dragleave', (ev) => {
            const target = ev.target.closest('li[data-queue-index]');
            if (target) target.classList.remove('drop-target');
        });
        libraryNavElements.queue.addEventListener('drop', async (ev) => {
            if (!libraryNavDrag) return;
            ev.preventDefault();
            const target = ev.target.closest('li[data-queue-index]');
            const rect = target ? target.getBoundingClientRect() : null;
            let dropIndex = queue.length;
            if (target && rect) {
                const idx = Number(target.dataset.queueIndex);
                dropIndex = ev.clientY > rect.top + rect.height / 2 ? idx + 1 : idx;
                target.classList.remove('drop-target');
            }

            if (libraryNavDrag.source === 'library') {
                await insertLibraryItem(libraryNavDrag.item, dropIndex);
            } else if (libraryNavDrag.source === 'queue') {
                const fromIndex = libraryNavDrag.index;
                if (fromIndex != null && fromIndex !== dropIndex) {
                    const [moved] = queue.splice(fromIndex, 1);
                    const normalizedIndex = dropIndex > fromIndex ? dropIndex - 1 : dropIndex;
                    queue.splice(normalizedIndex, 0, moved);
                    renderQueue();
                }
            }
            libraryNavDrag = null;
        });
    }

    libraryNavInitialized = true;
    setLibraryNavMode(libraryNavState.mode);
}

function setLibraryNavMode(mode) {
    if (!mode) return;
    libraryNavState.mode = mode;
    libraryNavState.page = 1;
    fetchLibraryNav();
    updateLibraryNavButtons();
}

function updateLibraryNavButtons() {
    if (!libraryNavElements.modeButtons) return;
    libraryNavElements.modeButtons.forEach(btn => {
        const active = btn.dataset.libraryMode === libraryNavState.mode;
        btn.classList.toggle('active', active);
        btn.classList.toggle('btn-primary', active);
        btn.classList.toggle('btn-outline-primary', !active);
    });
}

async function fetchLibraryNav({ refresh = false } = {}) {
    if (!libraryNavInitialized) return;
    const requestKey = [
        libraryNavState.mode,
        libraryNavState.query || '',
        libraryNavState.page,
        libraryNavState.perPage,
    ].join('|');
    const now = Date.now();
    if (!refresh) {
        if (libraryNavState.loading && libraryNavState.pendingKey === requestKey) {
            return;
        }
        if (libraryNavState.lastKey === requestKey && now - libraryNavState.lastFetchedAt < 15000) {
            return;
        }
    }
    libraryNavState.loading = true;
    libraryNavState.pendingKey = requestKey;
    if (libraryNavElements.status) {
        libraryNavElements.status.textContent = 'Loading library…';
    }
    const mode = libraryNavState.mode;
    try {
        let data = null;
        if (mode === 'music') {
            const params = new URLSearchParams({
                q: libraryNavState.query || '%',
                page: libraryNavState.page,
                per_page: libraryNavState.perPage,
            });
            if (refresh) params.set('refresh', '1');
            const res = await fetch(`/api/music/search?${params.toString()}`);
            data = await res.json();
        } else {
            const params = new URLSearchParams({
                page: libraryNavState.page,
                per_page: libraryNavState.perPage,
                kind: mode,
            });
            if (libraryNavState.query) params.set('q', libraryNavState.query);
            const res = await fetch(`/api/psa/library?${params.toString()}`);
            data = await res.json();
        }
        libraryNavState.items = data.items || [];
        libraryNavState.total = data.total || 0;
        libraryNavState.page = data.page || libraryNavState.page;
        libraryNavState.perPage = data.per_page || libraryNavState.perPage;
        libraryNavState.error = null;
        libraryNavState.lastKey = requestKey;
        libraryNavState.lastFetchedAt = now;
    } catch (err) {
        libraryNavState.items = [];
        libraryNavState.total = 0;
        libraryNavState.error = 'Unable to load library.';
    } finally {
        libraryNavState.loading = false;
        if (libraryNavState.pendingKey === requestKey) {
            libraryNavState.pendingKey = null;
        }
    }
    renderLibraryResults();
}

function renderLibraryResults() {
    if (!libraryNavInitialized) return;
    if (!libraryNavElements.results) return;
    libraryNavElements.results.innerHTML = '';

    if (libraryNavState.error) {
        libraryNavElements.results.innerHTML = `<li class="list-group-item text-danger">${libraryNavState.error}</li>`;
    } else if (!libraryNavState.items.length) {
        libraryNavElements.results.innerHTML = '<li class="list-group-item text-muted">No items found.</li>';
    } else {
        libraryNavState.items.forEach((item, idx) => {
            const payload = buildLibraryQueuePayload(item, libraryNavState.mode);
            const li = document.createElement('li');
            li.className = 'list-group-item d-flex justify-content-between align-items-start gap-2';
            li.draggable = Boolean(payload.url);
            li.dataset.libraryIndex = idx;
            li.innerHTML = `
                <div>
                    <div class="fw-semibold">${escapeHtml(payload.title || payload.name || 'Untitled')}</div>
                    <div class="small text-muted">${escapeHtml(libraryItemDescription(item, libraryNavState.mode))}</div>
                    <div class="small text-muted">${escapeHtml((payload.kind || '').toUpperCase())} · ${formatDuration(payload.duration)}</div>
                </div>
                <button class="btn btn-sm btn-outline-primary" ${payload.url ? '' : 'disabled'}>Add</button>
            `;
            if (payload.url) {
                li.addEventListener('dragstart', (ev) => {
                    libraryNavDrag = { source: 'library', item: payload };
                    ev.dataTransfer.effectAllowed = 'copy';
                });
            }
            const addBtn = li.querySelector('button');
            if (addBtn && payload.url) {
                addBtn.addEventListener('click', async () => {
                    await insertLibraryItem(payload, null);
                });
            }
            libraryNavElements.results.appendChild(li);
        });
    }

    const total = libraryNavState.total || 0;
    const start = total ? ((libraryNavState.page - 1) * libraryNavState.perPage) + 1 : 0;
    const end = Math.min(total, libraryNavState.page * libraryNavState.perPage);
    if (libraryNavElements.meta) {
        libraryNavElements.meta.textContent = total
            ? `Showing ${start}-${end} of ${total}`
            : 'No library items loaded.';
    }
    if (libraryNavElements.prevBtn) {
        libraryNavElements.prevBtn.disabled = libraryNavState.page <= 1;
    }
    if (libraryNavElements.nextBtn) {
        libraryNavElements.nextBtn.disabled = end >= total;
    }
    if (libraryNavElements.status) {
        libraryNavElements.status.textContent = libraryNavState.loading ? 'Loading library…' : '';
    }
    updateLibraryNavButtons();
}

function updateLibraryQueueList(queueItems) {
    if (!libraryNavInitialized || !libraryNavElements.queue) return;
    libraryNavElements.queue.innerHTML = '';
    if (!queueItems.length) {
        libraryNavElements.queue.innerHTML = '<li class="list-group-item text-muted">Drop items here to build the queue.</li>';
        return;
    }
    queueItems.forEach((item, idx) => {
        const li = document.createElement('li');
        const isCurrent = currentItem && item.id === currentItem.id;
        li.className = `list-group-item d-flex justify-content-between align-items-start gap-2${isCurrent ? ' list-group-item-info' : ''}`;
        li.dataset.queueIndex = idx;
        li.draggable = !isCurrent;
        li.innerHTML = `
            <div>
                <div class="fw-semibold">${escapeHtml(item.title || item.name || 'Untitled')}${isCurrent ? ' <span class="badge text-bg-success ms-2">Now</span>' : ''}</div>
                <div class="small text-muted">${escapeHtml((item.kind || '').toUpperCase())} · ${formatDuration(item.duration)}</div>
            </div>
            ${isCurrent ? '' : '<button class="btn btn-sm btn-outline-danger">Remove</button>'}
        `;
        if (!isCurrent) {
            li.addEventListener('dragstart', (ev) => {
                libraryNavDrag = { source: 'queue', index: idx };
                ev.dataTransfer.effectAllowed = 'move';
            });
            const removeBtn = li.querySelector('button');
            if (removeBtn) {
                removeBtn.addEventListener('click', () => {
                    removeFromQueue(idx);
                });
            }
        }
        libraryNavElements.queue.appendChild(li);
    });
}

async function insertLibraryItem(payload, position) {
    if (!payload || !payload.url) return;
    const queuedItem = await enqueueQueueItem(payload, position);
    if (!queuedItem) return;
    logItemInsert(queuedItem);
    updateVTInsertOptions();
    const player = activePlayer();
    if (!currentItem && queue.length === 1 && player && player.paused) {
        startFrom(0, currentIdx, 'auto');
    }
    renderQueue();
}

let library = [];
const queueList = document.getElementById('queueList');
const timerEls = [document.getElementById('timer'), document.getElementById('timerTop')];
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
let overlayStopAt = null;
let overlayStopForId = null;
let activeShowRunId = null;
let activeLogSheetId = null;
let showRunStartPromise = null;
const QUEUE_STORAGE_KEY = 'show_automator_queue_v1';
const AUTOMATION_STORAGE_KEY = 'show_automator_automation_v1';
const enablePlaybackLogging = false;
let localQueueId = 1;

if (playbackPanelsEnabled) {
    updatePlaybackPanels();
    playbackPanelTimer = setInterval(updatePlaybackPanels, 5000);
    window.addEventListener('beforeunload', () => {
        if (playbackPanelTimer) {
            clearInterval(playbackPanelTimer);
            playbackPanelTimer = null;
        }
    });
}

function nextQueueId() {
    const nextId = localQueueId;
    localQueueId += 1;
    return nextId;
}

function normalizeQueueItem(source = {}, { keepId = false } = {}) {
    const cues = source.cues || source.metadata?.cues || null;
    const metadata = source.metadata || {};
    const kind = source.kind || source.type || metadata.kind || queueTypeFor(source);
    const id = keepId && source.id ? source.id : nextQueueId();
    const sourceId = keepId ? source.source_id : (source.source_id || source.id || null);
    return {
        id,
        source_id: sourceId,
        name: source.name || source.title || metadata.name || 'Untitled',
        title: source.title || source.name || metadata.name || 'Untitled',
        artist: source.artist || metadata.artist || null,
        duration: source.duration || metadata.duration || null,
        url: source.url || metadata.url || null,
        category: source.category || metadata.category || null,
        token: source.token || metadata.token || null,
        loop: source.loop ?? metadata.loop ?? null,
        stop: Boolean(source.stop || kind === 'stop'),
        kind,
        metadata: metadata.metadata || metadata || null,
        cues,
        started_at: source.started_at || null,
    };
}

function persistQueueState() {
    try {
        const payload = {
            queue: queue.map(item => normalizeQueueItem(item, { keepId: true })),
            currentItem: currentItem ? normalizeQueueItem(currentItem, { keepId: true }) : null,
            saved_at: new Date().toISOString(),
        };
        localStorage.setItem(QUEUE_STORAGE_KEY, JSON.stringify(payload));
    } catch (err) {
        // ignore storage errors
    }
    updatePlaybackPanels();
}

function loadQueueState() {
    try {
        const raw = localStorage.getItem(QUEUE_STORAGE_KEY);
        if (!raw) return;
        const payload = JSON.parse(raw);
        queue.length = 0;
        if (payload.currentItem) {
            queue.push(normalizeQueueItem(payload.currentItem, { keepId: true }));
        }
        (payload.queue || []).forEach(item => {
            queue.push(normalizeQueueItem(item, { keepId: true }));
        });
        currentItem = null;
        const maxId = queue.reduce((max, item) => Math.max(max, item.id || 0), 0);
        localQueueId = Math.max(1, maxId + 1);
    } catch (err) {
        // ignore invalid storage
    }
}

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
const legacyPlayerEnabled = Boolean(queueList);

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

function renderQueue() {
    if (!queueList) {
        persistQueueState();
        updatePlaybackPanels();
        return;
    }
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
    persistQueueState();
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
    if (!enablePlaybackLogging) {
        showLogExport.classList.add('disabled');
        showLogExport.setAttribute('aria-disabled', 'true');
        showLogExport.href = '#';
        return;
    }
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
    if (!enablePlaybackLogging) return null;
    if (activeShowRunId) return activeShowRunId;
    if (showRunStartPromise) return showRunStartPromise;
    const payload = {};
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
    if (!enablePlaybackLogging) return;
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

function markItemStarted(item) {
    if (!item) return;
    item.started_at = new Date().toISOString();
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
        const stored = localStorage.getItem(AUTOMATION_STORAGE_KEY);
        if (stored) {
            const data = JSON.parse(stored);
            if (data && data.mode) {
                automationMode = data.mode;
            }
        }
    } catch (err) {
        // ignore
    }
    updateAutomationModeUI();
    toggleAutomationPolling();
}

async function setAutomationMode(mode) {
    if (!['manual', 'automation'].includes(mode)) return;
    automationMode = mode;
    try {
        localStorage.setItem(AUTOMATION_STORAGE_KEY, JSON.stringify({ mode }));
    } catch (err) {
        // ignore
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

async function enqueueQueueItem(item, position = null) {
    const queuedItem = normalizeQueueItem(item);
    if (position != null) {
        queue.splice(position, 0, queuedItem);
    } else {
        queue.push(queuedItem);
    }
    return queuedItem;
}

async function dequeueQueueItem() {
    // local-only queue; state is updated by callers
}

async function moveQueueItem(item, position) {
    if (!item) return;
    const idx = queue.indexOf(item);
    if (idx === -1) return;
    queue.splice(idx, 1);
    queue.splice(position, 0, item);
}

function loadPlaybackQueue() {
    loadQueueState();
    renderQueue();
    updateVTInsertOptions();
    if (queue.length) {
        previewCues(queue[0]);
    } else {
        resetTimers();
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

function stopOverlayPlayback() {
    if (!overlayPlayer) return;
    overlayPlayer.pause();
    overlayPlayer.removeAttribute('src');
    overlayPlayer.currentTime = 0;
    overlayStopAt = null;
    overlayStopForId = null;
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
            resetTimers();
            renderQueue();
            persistQueueState();
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
    stopOverlayPlayback();
    currentItem = null;
    resetTimers();
    if (sync) {
        persistQueueState();
    }
}

async function addToQueue(item) {
    const queuedItem = await enqueueQueueItem(item, queue.length);
    if (!queuedItem) return;
    logItemInsert(queuedItem);
    updateVTInsertOptions();
    const player = activePlayer();
    if (!currentItem && queue.length === 1 && player && player.paused) {
        startFrom(0, currentIdx, 'auto');
    }
    renderQueue();
}

async function insertToQueue(item, idx) {
    const position = Math.max(0, Math.min(queue.length, idx));
    const queuedItem = await enqueueQueueItem(item, position);
    if (!queuedItem) return;
    logItemInsert(queuedItem);
    updateVTInsertOptions();
    const player = activePlayer();
    if (!currentItem && position === 0 && player && player.paused) {
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

function startFrom(idx, preferredIdx = currentIdx, reason = 'manual') {
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
    markItemStarted(item);
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
        startFrom(0, currentIdx, 'auto');
    } else {
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
    markItemStarted(nextItem);
    queue.shift();
    prestartedIdx = null;
    autoNextTriggered = false;
    applyPlaybackRate(nextPlayer, nextItem);
    nextPlayer.play();
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
            markItemStarted(nextItem);
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
        const flash = introRem > 0 && introRem <= 8;
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
        const flash = outroRem > 0 && outroRem <= 8;
        outroBadge.classList.toggle('countdown-flash', flash);
        outroBadge.classList.toggle('text-bg-danger', flash);
    } else {
        outroBadge.style.display = 'inline-block';
        outroBadge.textContent = 'Outro: 0.0s';
        outroBadge.classList.remove('countdown-flash','text-bg-danger');
    }
    updateTalkOverlay(cues, player.currentTime, player.duration);

    if (automationMode !== 'automation' && overlayPlayer) {
        const overlayCandidate = queue.find(item => overlayEligible(item));
        const nextTrack = queue.find(item => !overlayEligible(item) && !item.stop);
        if (!overlayPlayer.src && overlayCandidate && currentItem && currentItem.kind === 'music') {
            if (cues.outro && player.currentTime >= cues.outro) {
                overlayStopAt = nextTrack?.cues?.intro ?? null;
                overlayStopForId = nextTrack?.id ?? null;
                playOverlayItem(overlayCandidate);
            }
        }
        if (overlayStopAt != null && overlayStopForId && currentItem && currentItem.id === overlayStopForId) {
            if (player.currentTime >= overlayStopAt) {
                stopOverlayPlayback();
            }
        }
    }

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
            markItemStarted(currentItem);
            prestartedIdx = null;
            applyPlaybackRate();
            queue.shift();
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

const clearQueueBtn = document.getElementById('clearQueue');
const overlayPlayBtn = document.getElementById('overlayPlay');
const overlayUrlInput = document.getElementById('overlayUrl');
const toggleTalkupBtn = document.getElementById('toggleTalkup');
const cueStepBackBtn = document.getElementById('cueStepBack');
const cueStepForwardBtn = document.getElementById('cueStepForward');
const cueToggleBtn = document.getElementById('cueToggle');
const cueSetBtn = document.getElementById('cueSet');
const cueRemoveBtn = document.getElementById('cueRemove');
const cueSaveBtn = document.getElementById('cueSave');
const vtConfirmBtn = document.getElementById('vtConfirm');
const vtDeleteBtn = document.getElementById('vtDelete');

if (clearQueueBtn) clearQueueBtn.addEventListener('click', () => {
    const removed = queue.splice(0, queue.length);
    removed.forEach(entry => dequeueQueueItem(entry));
    stopFade();
    stopAllAudio();
    renderQueue();
});
if (togglePauseBtn) togglePauseBtn.addEventListener('click', () => {
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
if (playNextBtn) playNextBtn.addEventListener('click', () => playNext('manual'));
if (addStopBtn) addStopBtn.addEventListener('click', addStopCue);
if (fadeOutBtn) fadeOutBtn.addEventListener('click', () => fadeAndNext('manual'));
if (overlayPlayBtn && overlayUrlInput && overlayPlayer) overlayPlayBtn.addEventListener('click', () => {
    const url = overlayUrlInput.value.trim();
    if (!url) return;
    overlayPlayer.src = url;
    overlayPlayer.play();
});
if (toggleTalkupBtn) toggleTalkupBtn.addEventListener('click', () => {
    talkUpMode = !talkUpMode;
    toggleTalkupBtn.classList.toggle('btn-dark', talkUpMode);
    toggleTalkupBtn.classList.toggle('btn-outline-dark', !talkUpMode);
    updateTalkOverlay(currentItem ? (currentItem.cues || {}) : null, activePlayer().currentTime || 0, activePlayer().duration || 0);
});

modeToggleButtons.forEach(btn => {
    btn.addEventListener('click', () => setAutomationMode(btn.dataset.automationMode));
});

if (top40) top40.addEventListener('change', () => {
    players.forEach((p) => {
        preservesPitchOff(p.el);
        if (p.el && p.item) {
            p.el.playbackRate = computePlaybackRate(p.item);
        }
    });
});

players.forEach((p, idx) => {
    if (!p.el) return;
    preservesPitchOff(p.el);
    p.el.addEventListener('play', () => { if (idx === currentIdx) { startTimer(); applyPlaybackRate(p.el, p.item); if (togglePauseBtn) togglePauseBtn.textContent = 'Pause'; } });
    p.el.addEventListener('loadedmetadata', () => { if (p.item) { applyPlaybackRate(p.el, p.item); } });
    p.el.addEventListener('pause', () => { if (idx === currentIdx) { stopTimer(); if (togglePauseBtn) togglePauseBtn.textContent = 'Play'; } });
    p.el.addEventListener('ended', () => handleEnded(idx));
});

if (overlayPlayer) {
    overlayPlayer.addEventListener('ended', () => {
        stopOverlayPlayback();
    });
}

if (!legacyPlayerEnabled) {
    return;
}
loadPlaybackQueue();
loadAutomationMode();
updateShowLogExport();

window.addEventListener('beforeunload', () => {
    if (!enablePlaybackLogging) return;
    if (!activeShowRunId) return;
    const payload = new Blob([JSON.stringify({ show_run_id: activeShowRunId, log_sheet_id: activeLogSheetId })], { type: 'application/json' });
    navigator.sendBeacon('/api/playback/show/stop', payload);
});


function vtDisplayMeta(text) {
    if (!vtMeta) return;
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
        title: vtTitle ? vtTitle.value.trim() : '',
        host: vtHost ? vtHost.value.trim() : '',
        notes: vtNotes ? vtNotes.value.trim() : '',
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

if (vtImport) vtImport.addEventListener('click', () => { if (vtFile) vtFile.click(); });
if (vtFile) vtFile.addEventListener('change', async (e) => {
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
    if (!field) return;
    field.addEventListener('input', syncPendingVTMeta);
});

if (startRecBtn) startRecBtn.addEventListener('click', async () => {
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
if (stopRecBtn) stopRecBtn.addEventListener('click', () => { if (recorder) recorder.stop(); });

if (vtConfirmBtn) vtConfirmBtn.addEventListener('click', () => {
    if (!vtInsertPosition) return;
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
if (vtDeleteBtn) vtDeleteBtn.addEventListener('click', () => {
    if (pendingVTUrl) URL.revokeObjectURL(pendingVTUrl);
    pendingVTUrl = null;
    pendingVTBlob = null;
    pendingVT = null;
    document.getElementById('vtPending').classList.add('d-none');
});

function updateTalkOverlay(cues, currentTime = 0, duration = 0) {
    if (!talkOverlay || !talkOverlayIntro || !talkOverlayOutro) return;
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
if (cueStepBackBtn) cueStepBackBtn.addEventListener('click', () => nudgeCue(-0.05));
if (cueStepForwardBtn) cueStepForwardBtn.addEventListener('click', () => nudgeCue(0.05));
if (cueToggleBtn) cueToggleBtn.addEventListener('click', () => {
    if (!cuePlayer) return;
    if (cuePlayer.paused) cuePlayer.play(); else cuePlayer.pause();
});
if (cueSetBtn) cueSetBtn.addEventListener('click', () => {
    if (!cuePlayer || isNaN(cuePlayer.currentTime)) return;
    cueInputs[cueSelected].value = cuePlayer.currentTime.toFixed(2);
    updateCueMarkers();
});
if (cueRemoveBtn) cueRemoveBtn.addEventListener('click', () => {
    cueInputs[cueSelected].value = '';
    updateCueMarkers();
});
if (cueSaveBtn) cueSaveBtn.addEventListener('click', async () => {
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
        } else {
            alert('Unable to save cues.');
        }
    } catch (err) {
        alert('Unable to save cues.');
    }
});

if (cuePlayer) {
    cuePlayer.addEventListener('timeupdate', updateCueNeedle);
    cuePlayer.addEventListener('loadedmetadata', updateCueMarkers);
}

if (cueWave) cueWave.addEventListener('mousedown', (e) => {
    if (!cuePlayer || !cuePlayer.duration) return;
    const rect = cueWave.getBoundingClientRect();
    const x = e.clientX - rect.left + cueWave.scrollLeft;
    const pct = Math.max(0, Math.min(1, x / cueWave.clientWidth));
    cuePlayer.currentTime = pct * cuePlayer.duration;
    updateCueNeedle();
});

if (cueMarkers) cueMarkers.addEventListener('mousedown', (e) => {
    if (!e.target.dataset.cue) return;
    cueDragField = e.target.dataset.cue;
    cueDragNeedle = false;
});
if (cueNeedle) cueNeedle.addEventListener('mousedown', () => { cueDragNeedle = true; cueDragField = null; });
document.addEventListener('mouseup', () => { cueDragNeedle = false; cueDragField = null; });
document.addEventListener('mousemove', (e) => {
    if (!cuePlayer || !cueWave) return;
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
