(() => {
    const STATE_KEY = 'showAutomatorState';
    const TOP40_RATE = Math.pow(2, 0.5 / 12);
    const top40Toggle = document.getElementById('showTop40Boost');
    const deckAudio = document.getElementById('showDeckAudio');
    const nowPlayingCard = document.getElementById('nowPlayingCard');
    const queueList = document.getElementById('showQueue');

    const sessionState = {
        top40Boost: false,
        queue: {
            items: [],
            currentItem: null,
        },
    };

    function loadState() {
        try {
            const stored = sessionStorage.getItem(STATE_KEY);
            if (stored) {
                const parsed = JSON.parse(stored);
                if (typeof parsed.top40Boost === 'boolean') {
                    sessionState.top40Boost = parsed.top40Boost;
                }
            }
        } catch (err) {
            // ignore storage issues
        }
    }

    function saveState() {
        try {
            sessionStorage.setItem(STATE_KEY, JSON.stringify({
                top40Boost: sessionState.top40Boost,
            }));
        } catch (err) {
            // ignore storage issues
        }
    }

    function normalizeType(value) {
        return (value || '').toLowerCase();
    }

    function refreshQueueState() {
        const items = [];
        if (queueList) {
            queueList.querySelectorAll('[data-queue-item]').forEach((el) => {
                items.push({
                    type: normalizeType(el.dataset.itemType),
                });
            });
        }
        const currentItemType = normalizeType(nowPlayingCard?.dataset.itemType);
        sessionState.queue.items = items;
        sessionState.queue.currentItem = currentItemType ? { type: currentItemType } : items[0] || null;
    }

    function preservesPitchOff(el) {
        if (!el) return;
        el.preservesPitch = false;
        el.mozPreservesPitch = false;
        el.webkitPreservesPitch = false;
    }

    function isMusicItem(item) {
        return item && item.type === 'music';
    }

    function computePlaybackRate(item) {
        if (sessionState.top40Boost && isMusicItem(item)) {
            return TOP40_RATE;
        }
        return 1.0;
    }

    function applyPlaybackRate() {
        if (!deckAudio) return;
        preservesPitchOff(deckAudio);
        deckAudio.playbackRate = computePlaybackRate(sessionState.queue.currentItem);
    }

    function handleToggleChange() {
        sessionState.top40Boost = !!top40Toggle?.checked;
        saveState();
        applyPlaybackRate();
    }

    loadState();
    refreshQueueState();

    if (top40Toggle) {
        top40Toggle.checked = sessionState.top40Boost;
        top40Toggle.addEventListener('change', handleToggleChange);
    }

    if (deckAudio) {
        preservesPitchOff(deckAudio);
        deckAudio.addEventListener('loadedmetadata', applyPlaybackRate);
        deckAudio.addEventListener('play', applyPlaybackRate);
    }

    applyPlaybackRate();
    const bootstrapEl = document.getElementById("show-automator-bootstrap");
    if (!bootstrapEl) {
        return;
    }

    const bootstrap = JSON.parse(bootstrapEl.textContent || "{}");
    const stateUrl = bootstrap?.endpoints?.state;
    const pollIntervalMs = bootstrap?.config?.pollIntervalMs ?? 5000;
    const fallbackArt = bootstrap?.assets?.fallbackArt || "";

    const elements = {
        modeBadge: document.getElementById("show-automator-mode"),
        deckLabel: document.getElementById("now-playing-deck"),
        art: document.getElementById("now-playing-art"),
        note: document.getElementById("now-playing-note"),
        title: document.getElementById("now-playing-title"),
        artist: document.getElementById("now-playing-artist"),
        album: document.getElementById("now-playing-album"),
        positionLabel: document.getElementById("playback-position-label"),
        positionRange: document.getElementById("playback-position-range"),
        liveQueue: document.getElementById("live-queue"),
        deckControlMode: document.getElementById("deck-control-mode"),
        queueBuilderBody: document.getElementById("queue-builder-body"),
        musicBody: document.getElementById("library-music-body"),
        psaBody: document.getElementById("library-psa-body"),
        imagingBody: document.getElementById("library-imaging-body"),
        controlPlay: document.getElementById("control-play"),
        controlPause: document.getElementById("control-pause"),
        controlFade: document.getElementById("control-fade"),
    };

    const formatDuration = (seconds) => {
        if (seconds === null || seconds === undefined || Number.isNaN(Number(seconds))) {
            return "--:--";
        }
        const total = Math.max(0, Math.floor(Number(seconds)));
        const hrs = Math.floor(total / 3600);
        const mins = Math.floor((total % 3600) / 60);
        const secs = total % 60;
        if (hrs > 0) {
            return `${String(hrs).padStart(2, "0")}:${String(mins).padStart(2, "0")}:${String(secs).padStart(2, "0")}`;
        }
        return `${String(mins).padStart(2, "0")}:${String(secs).padStart(2, "0")}`;
    };

    const buildMetaLine = (label, value) => `${label}: ${value || "--"}`;

    const renderMode = (mode) => {
        if (!elements.modeBadge || !elements.deckControlMode) {
            return;
        }
        const normalized = (mode || "manual").toLowerCase();
        if (normalized === "auto" || normalized === "automation") {
            elements.modeBadge.textContent = "Automation Active";
            elements.modeBadge.classList.remove("bg-success");
            elements.modeBadge.classList.add("bg-primary");
            elements.deckControlMode.textContent = "Automation";
        } else {
            elements.modeBadge.textContent = "Manual Control Active";
            elements.modeBadge.classList.remove("bg-primary");
            elements.modeBadge.classList.add("bg-success");
            elements.deckControlMode.textContent = "Manual";
        }
    };

    const renderNowPlaying = (nowPlaying, serverTime) => {
        if (!elements.title) {
            return;
        }
        const metadata = nowPlaying?.metadata || {};
        const title = nowPlaying?.title || "No item loaded";
        const artist = nowPlaying?.artist || metadata.artist || "--";
        const album = metadata.album || metadata.release || "--";
        const year = metadata.year ? ` · ${metadata.year}` : "";
        const status = nowPlaying?.status || "idle";
        const artUrl = metadata.cover_url || metadata.art_url || metadata.artwork_url || metadata.image_url || fallbackArt;
        const deckLabel = metadata.deck || nowPlaying?.deck || "Deck A";

        if (elements.deckLabel) {
            elements.deckLabel.textContent = deckLabel;
        }
        elements.title.textContent = title;
        elements.artist.textContent = buildMetaLine("Artist", artist);
        elements.album.textContent = `Album: ${album}${year}`;
        elements.note.textContent = status === "playing" ? "On-air playback" : status === "paused" ? "Paused" : "Awaiting playback data";

        if (elements.art) {
            elements.art.src = artUrl || fallbackArt;
        }

        const startedAt = nowPlaying?.started_at ? new Date(nowPlaying.started_at) : null;
        const serverNow = serverTime ? new Date(serverTime) : new Date();
        const duration = nowPlaying?.duration;
        let positionSeconds = 0;
        if (startedAt && !Number.isNaN(startedAt.valueOf())) {
            positionSeconds = Math.max(0, (serverNow.getTime() - startedAt.getTime()) / 1000);
        }
        const displayPosition = formatDuration(positionSeconds);
        const displayDuration = formatDuration(duration);
        if (elements.positionLabel) {
            elements.positionLabel.textContent = `${displayPosition} / ${displayDuration}`;
        }
        if (elements.positionRange) {
            const max = duration ? Math.max(1, Math.floor(Number(duration))) : 0;
            elements.positionRange.max = String(max);
            elements.positionRange.value = String(Math.min(positionSeconds, max || 0));
            elements.positionRange.disabled = !duration;
        }

        if (elements.controlPlay && elements.controlPause) {
            if (status === "playing") {
                elements.controlPlay.classList.add("btn-primary");
                elements.controlPlay.classList.remove("btn-outline-primary");
                elements.controlPause.classList.add("btn-outline-primary");
                elements.controlPause.classList.remove("btn-primary");
            } else if (status === "paused") {
                elements.controlPlay.classList.add("btn-outline-primary");
                elements.controlPlay.classList.remove("btn-primary");
                elements.controlPause.classList.add("btn-primary");
                elements.controlPause.classList.remove("btn-outline-primary");
            } else {
                elements.controlPlay.classList.add("btn-primary");
                elements.controlPause.classList.add("btn-outline-primary");
            }
        }
    };

    const renderQueue = (queue, controlMode) => {
        if (!elements.liveQueue) {
            return;
        }
        if (!queue || queue.length === 0) {
            elements.liveQueue.innerHTML = '<li class="list-group-item text-muted small">Queue is empty.</li>';
            return;
        }
        const disabledAttr = controlMode !== "manual" ? "disabled" : "";
        const rows = queue.map((item, index) => {
            const title = item.title || "Untitled item";
            const type = item.type ? item.type.toUpperCase() : "Item";
            const duration = formatDuration(item.duration);
            return `
                <li class="list-group-item d-flex justify-content-between align-items-center">
                    <div>
                        <div class="fw-semibold">${index + 1}. ${title}</div>
                        <div class="small text-muted">${type} · ${duration}</div>
                    </div>
                    <div class="btn-group btn-group-sm" role="group" aria-label="Queue controls">
                        <button class="btn btn-outline-secondary" type="button" ${disabledAttr}>↑</button>
                        <button class="btn btn-outline-secondary" type="button" ${disabledAttr}>↓</button>
                        <button class="btn btn-outline-danger" type="button" ${disabledAttr}>Remove</button>
                    </div>
                </li>
            `;
        });
        elements.liveQueue.innerHTML = rows.join("");
    };

    const renderQueueBuilder = (queue) => {
        if (!elements.queueBuilderBody) {
            return;
        }
        if (!queue || queue.length === 0) {
            elements.queueBuilderBody.innerHTML = '<tr><td colspan="4" class="text-muted small">No queued items yet.</td></tr>';
            return;
        }
        const rows = queue.map((item) => {
            const notes = item.metadata?.note || item.metadata?.notes || "";
            return `
                <tr>
                    <td>${item.title || "Untitled item"}</td>
                    <td>${item.type || "--"}</td>
                    <td>${formatDuration(item.duration)}</td>
                    <td>${notes}</td>
                </tr>
            `;
        });
        elements.queueBuilderBody.innerHTML = rows.join("");
    };

    const renderLibrary = (sectionEl, items, columns) => {
        if (!sectionEl) {
            return;
        }
        if (!items || items.length === 0) {
            sectionEl.innerHTML = `<tr><td colspan="${columns}" class="text-muted small">No library data from automator state.</td></tr>`;
            return;
        }
        const rows = items.map((item) => {
            if (columns === 5) {
                return `
                    <tr>
                        <td>${item.title || "--"}</td>
                        <td>${item.artist || "--"}</td>
                        <td>${item.album || "--"}</td>
                        <td>${formatDuration(item.duration)}</td>
                        <td><button class="btn btn-sm btn-outline-primary" type="button">Add to Queue</button></td>
                    </tr>
                `;
            }
            return `
                <tr>
                    <td>${item.title || "--"}</td>
                    <td>${item.category || item.type || "--"}</td>
                    <td>${formatDuration(item.duration)}</td>
                    <td><button class="btn btn-sm btn-outline-primary" type="button">Add to Queue</button></td>
                </tr>
            `;
        });
        sectionEl.innerHTML = rows.join("");
    };

    const renderState = (payload) => {
        if (!payload) {
            return;
        }
        const controlMode = payload.controls?.mode || "manual";
        renderMode(controlMode);
        const nowPlaying = payload.now_playing;
        renderNowPlaying(nowPlaying, payload.server_time);
        renderQueue(payload.queue || [], controlMode);
        renderQueueBuilder(payload.queue || []);
        renderLibrary(elements.musicBody, payload.library?.music, 5);
        renderLibrary(elements.psaBody, payload.library?.psa, 4);
        renderLibrary(elements.imagingBody, payload.library?.imaging, 4);
    };

    const fetchState = async () => {
        if (!stateUrl) {
            return;
        }
        const res = await fetch(stateUrl, { headers: { "Accept": "application/json" } });
        if (!res.ok) {
            return;
        }
        const payload = await res.json();
        renderState(payload);
    };

    const start = () => {
        fetchState();
        if (pollIntervalMs > 0) {
            window.setInterval(fetchState, pollIntervalMs);
        }
    };

    if (document.readyState === "loading") {
        document.addEventListener("DOMContentLoaded", start);
    } else {
        start();
    }
})();
