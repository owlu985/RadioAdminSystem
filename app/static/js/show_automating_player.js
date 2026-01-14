(() => {
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
const controlModeLabel = document.getElementById('controlModeLabel');
const radiodjOverrideBtn = document.getElementById('radiodjOverride');
const pushQueueBtn = document.getElementById('pushQueue');
const radiodjStatus = document.getElementById('radiodjStatus');
const radiodjTrack = document.getElementById('radiodjTrack');
const radiodjQueueStatus = document.getElementById('radiodjQueueStatus');
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
let controlMode = 'rams';

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
    if (!queue.length) {
        queueList.innerHTML = '<li class="text-muted">Queue empty.</li>';
        resetTimers();
        if (pushQueueBtn) {
            pushQueueBtn.disabled = true;
        }
        return;
    }
    queue.forEach((item, idx) => {
        const li = document.createElement('li');
        const meta = [];
        if (item.duration) meta.push(`${item.duration}s`);
        if (item.category) meta.push(item.category);
        if (item.metadata && item.metadata.host) meta.push(item.metadata.host);
        if (item.metadata && item.metadata.notes) meta.push(item.metadata.notes);
        const introMeta = item.cues && item.cues.intro ? ` (${item.cues.intro.toFixed(1)}s)` : '';
        const displayName = item.title || item.name;
        li.innerHTML = `<div class="d-flex justify-content-between align-items-center">
            <div>${item.kind === 'voicetrack' ? '<span class="badge text-bg-primary me-1">VT</span>' : ''}${item.name}${introMeta} ${meta.length ? `<small class="text-muted">(${meta.join(' • ')})</small>` : ''}</div>
            <div class="d-flex gap-2">
                <button class="btn btn-sm btn-outline-secondary" data-cue>Edit</button>
                <button class="btn btn-sm btn-outline-danger" data-remove>&times;</button>
            </div>
        </div>`;
        li.dataset.index = idx;
        li.tabIndex = 0;
        li.addEventListener('click', (ev) => {
            if (!ev.target.dataset.remove && controlMode === 'rams') {
                startFrom(idx);
            }
        });
        li.addEventListener('keydown', (ev) => {
            if (ev.key === 'Enter' && controlMode === 'rams') {
                startFrom(idx);
            }
        });
        li.querySelector('[data-remove]').addEventListener('click', (ev) => { ev.stopPropagation(); removeFromQueue(idx); });
        queueList.appendChild(li);
    });
    if (pushQueueBtn) {
        pushQueueBtn.disabled = controlMode !== 'radiodj' || !queue.length;
    }
}

function updateControlModeUI() {
    const isRadioDJ = controlMode === 'radiodj';
    if (controlModeLabel) {
        controlModeLabel.textContent = isRadioDJ ? 'RadioDJ' : 'RAMS';
        controlModeLabel.className = `badge ${isRadioDJ ? 'text-bg-warning' : 'text-bg-light border'}`;
    }
    if (radiodjOverrideBtn) {
        radiodjOverrideBtn.textContent = isRadioDJ ? 'Return to RAMS' : 'RadioDJ Override';
    }
    if (pushQueueBtn) {
        pushQueueBtn.disabled = !isRadioDJ || !queue.length;
    }
    [togglePauseBtn, playNextBtn, fadeOutBtn, addStopBtn].forEach(btn => {
        if (btn) btn.disabled = isRadioDJ;
    });
    if (isRadioDJ) {
        stopFade();
        stopAllAudio();
        stopTimer();
        togglePauseBtn.textContent = 'Pause';
    }
}

function setRadioDJAvailability(enabled) {
    if (radiodjOverrideBtn) {
        radiodjOverrideBtn.disabled = !enabled;
    }
    if (!enabled && controlMode === 'radiodj') {
        controlMode = 'rams';
        updateControlModeUI();
        renderQueue();
    }
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

async function setNowPlayingOverride(enabled) {
    try {
        const res = await fetch('/api/now/override', {
            method: 'POST',
            headers: {'Content-Type': 'application/json'},
            body: JSON.stringify({enabled})
        });
        if (!res.ok) {
            return false;
        }
        return true;
    } catch (err) {
        return false;
    }
}

async function loadOverrideState() {
    try {
        const res = await fetch('/api/now/override');
        const data = await res.json();
        if (res.ok && typeof data.override_enabled === 'boolean') {
            controlMode = data.override_enabled ? 'radiodj' : 'rams';
            updateControlModeUI();
            renderQueue();
        }
    } catch (err) {
        // ignore
    }
}

async function loadRadioDJNowPlaying() {
    if (!radiodjStatus || !radiodjTrack) return;
    try {
        const res = await fetch('/api/radiodj/now-playing');
        const data = await res.json();
        if (res.ok && data.track) {
            const track = data.track || {};
            const artist = track.artist || '';
            const title = track.title || '';
            const album = track.album || '';
            const label = [artist, title].filter(Boolean).join(' - ') || album || 'Now playing';
            radiodjTrack.textContent = label;
            radiodjStatus.classList.remove('text-danger');
            setRadioDJAvailability(true);
        } else if (data.status === 'disabled') {
            radiodjTrack.textContent = 'RadioDJ integration disabled.';
            radiodjStatus.classList.add('text-danger');
            setRadioDJAvailability(false);
        } else {
            radiodjTrack.textContent = 'No now-playing data.';
            radiodjStatus.classList.remove('text-danger');
            setRadioDJAvailability(true);
        }
    } catch (err) {
        radiodjTrack.textContent = 'Unable to reach RadioDJ.';
        radiodjStatus.classList.add('text-danger');
        setRadioDJAvailability(false);
    }
}

async function pushQueueToRadioDJ() {
    if (controlMode !== 'radiodj') return;
    if (!radiodjQueueStatus) return;
    const items = queue.filter(item => item.token).map(item => ({
        token: item.token,
        name: item.name,
    }));
    if (!items.length) {
        radiodjQueueStatus.textContent = 'No queue items with file references to push.';
        return;
    }
    radiodjQueueStatus.textContent = 'Pushing queue to RadioDJ...';
    try {
        const res = await fetch('/api/radiodj/queue', {
            method: 'POST',
            headers: {'Content-Type': 'application/json'},
            body: JSON.stringify({items})
        });
        const data = await res.json();
        if (!res.ok || data.status !== 'ok') {
            radiodjQueueStatus.textContent = data.message || 'Unable to push queue.';
            return;
        }
        const results = data.results || [];
        const okCount = results.filter(r => r.status === 'ok').length;
        const errCount = results.filter(r => r.status !== 'ok').length;
        radiodjQueueStatus.textContent = errCount
            ? `Pushed ${okCount}/${results.length} items; ${errCount} failed.`
            : `Pushed ${okCount} item${okCount === 1 ? '' : 's'} to RadioDJ.`;
    } catch (err) {
        radiodjQueueStatus.textContent = 'Unable to push queue.';
    }
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

function stopAllAudio() {
    players.forEach(p => { p.el.pause(); p.el.removeAttribute('src'); p.el.volume = 1; });
    currentItem = null;
    resetTimers();
}

function addToQueue(item) {
    queue.push(item);
    if (controlMode === 'rams' && queue.length === 1 && activePlayer().paused) {
        startFrom(0);
    }
    renderQueue();
}

function insertToQueue(item, idx) {
    const position = Math.max(0, Math.min(queue.length, idx));
    queue.splice(position, 0, item);
    if (controlMode === 'rams' && position === 0 && activePlayer().paused) {
        startFrom(0);
    }
    renderQueue();
}

function removeFromQueue(idx) {
    if (idx < 0 || idx >= queue.length) return;
    queue.splice(idx, 1);
    renderQueue();
}

function addStopCue() { queue.push({ name: 'STOP', stop: true }); renderQueue(); }

const TOP40_RATE = Math.pow(2, 0.5 / 12);
const OVERLAY_KINDS = ['voicetrack', 'overlay'];

function preservesPitchOff(el) {
    if (!el) return;
    el.preservesPitch = false;
    el.mozPreservesPitch = false;
    el.webkitPreservesPitch = false;
}

function computePlaybackRate(item) {
    const isMusic = item && ((item.kind && item.kind === 'music') || (item.category || '').toLowerCase().includes('music'));
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

function startFrom(idx, preferredIdx = currentIdx) {
    if (controlMode !== 'rams') {
        return;
    }
    const item = queue[idx];
    if (!item) return;
    if (item.stop) {
        queue.splice(0, idx + 1);
        shiftPastStops();
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
    if (idx > 0) queue.splice(0, idx);
    player.src = item.url;
    player.volume = 1;
    applyPlaybackRate(player, item);
    player.play();
    togglePauseBtn.textContent = 'Pause';
    renderQueue();
}

function shiftPastStops() {
    while (queue.length && queue[0].stop) {
        queue.shift();
    }
}

function playNext() {
    if (!queue.length) return;
    queue.shift();
    shiftPastStops();
    stopFade();
    stopAllAudio();
    autoNextTriggered = false;
    prestartedIdx = null;
    if (queue.length) {
        startFrom(0);
    } else {
        renderQueue();
    }
}

function startNextWithOverlay() {
    if (queue.length <= 1) {
        fadeAndNext();
        return;
    }

    const current = activePlayer();
    const currentPlaying = currentItem;
    const nextItem = queue[1];

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

    queue.shift();
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
    prestartedIdx = null;
    autoNextTriggered = false;
    applyPlaybackRate(nextPlayer, nextItem);
    nextPlayer.play();
    startTimer();
    renderQueue();
}

function fadeAndNext() {
    if (queue.length <= 1 || (queue[1] && queue[1].stop)) {
        playNext();
        return;
    }
    const current = activePlayer();
    const nextIdx = 1 - currentIdx;
    const nextItem = queue[1];
    const nextPlayerObj = players[nextIdx];
    const nextPlayer = nextPlayerObj.el;
    stopFade();
    nextPlayerObj.item = nextItem;
    nextPlayer.src = nextItem.url;
    nextPlayer.volume = 1;
    applyPlaybackRate(nextPlayer, nextItem);
    nextPlayer.play();
    fadeTimer = setInterval(() => {
        current.volume = Math.max(0, current.volume - 0.06);
        if (current.volume <= 0.02) {
            stopFade();
            current.pause();
            current.volume = 1;
            current.removeAttribute('src');
            queue.shift();
            shiftPastStops();
            currentIdx = nextIdx;
            currentItem = nextItem;
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
    if (queue.length > 1 && cues.start_next) {
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

    // Auto-advance when start_next cue is hit.
    if (!autoNextTriggered && cues.start_next && player.currentTime >= cues.start_next) {
        autoNextTriggered = true;
        resetTimers();
        if (queue.length > 1) {
            if (overlayEligible(currentItem)) {
                startNextWithOverlay();
            } else {
                fadeAndNext();
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
    if (!prestartedIdx && currentItem && currentItem.kind === 'voicetrack' && queue.length > 1) {
        const nextItem = queue[1];
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
    queue.shift();
    shiftPastStops();
    if (queue.length) {
        if (prestartedIdx !== null && players[prestartedIdx].item === queue[0]) {
            currentIdx = prestartedIdx;
            currentItem = queue[0];
            prestartedIdx = null;
            applyPlaybackRate();
            startTimer();
            renderQueue();
        } else {
            startFrom(0);
        }
    } else {
        stopAllAudio();
        renderQueue();
    }
}

document.getElementById('clearQueue').addEventListener('click', () => {
    queue.length = 0;
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
playNextBtn.addEventListener('click', playNext);
addStopBtn.addEventListener('click', addStopCue);
fadeOutBtn.addEventListener('click', fadeAndNext);
pushQueueBtn.addEventListener('click', pushQueueToRadioDJ);
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

radiodjOverrideBtn.addEventListener('click', async () => {
    const nextMode = controlMode === 'rams' ? 'radiodj' : 'rams';
    const ok = await setNowPlayingOverride(nextMode === 'radiodj');
    if (!ok) {
        if (radiodjQueueStatus) {
            radiodjQueueStatus.textContent = 'Unable to update now-playing override.';
        }
        return;
    }
    controlMode = nextMode;
    updateControlModeUI();
    renderQueue();
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
loadPSAs();
updateControlModeUI();
loadOverrideState();
loadRadioDJNowPlaying();
setInterval(loadRadioDJNowPlaying, 15000);

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
            insertToQueue(pendingVT, 1);
        } else if (value === 'end') {
            addToQueue(pendingVT);
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
