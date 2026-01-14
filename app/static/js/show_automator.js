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
})();
