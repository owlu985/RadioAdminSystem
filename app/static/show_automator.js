(() => {
    const queueList = document.getElementById('liveQueue');
    const queueStatus = document.getElementById('queueStatus');
    if (!queueList) return;

    let queueItems = [];
    let dragging = null;

    const MARKER_LABELS = {
        stop: 'Stop',
        loop: 'Loop',
    };

    const overlayTypes = new Set(['overlay', 'voicetrack']);

    const setStatus = (message, tone = 'muted') => {
        if (!queueStatus) return;
        queueStatus.className = `text-${tone} small mb-2`;
        queueStatus.textContent = message;
    };

    const normalizeText = (value) => (value || '').toString().trim().toLowerCase();

    const isStopMarker = (item) => {
        const meta = item?.metadata || {};
        const marker = normalizeText(meta.marker || meta.marker_type);
        return meta.stop === true || normalizeText(item?.title) === 'stop' || marker === 'stop';
    };

    const isLoopMarker = (item) => {
        const meta = item?.metadata || {};
        const marker = normalizeText(meta.marker || meta.marker_type);
        return meta.loop_marker === true || marker === 'loop';
    };

    const isMarker = (item) => isStopMarker(item) || isLoopMarker(item);

    const isOverlayItem = (item) => {
        const meta = item?.metadata || {};
        if (meta.overlay === true) return true;
        const kind = normalizeText(meta.kind || meta.type);
        return overlayTypes.has(normalizeText(item?.type)) || overlayTypes.has(kind);
    };

    const isDraggable = (item) => !isMarker(item) && !isOverlayItem(item);

    const renderQueue = () => {
        queueList.innerHTML = '';
        if (!queueItems.length) {
            queueList.innerHTML = '<li class="list-group-item text-muted">Queue is empty.</li>';
            setStatus('Queue ready.');
            return;
        }

        queueItems.forEach((item, idx) => {
            const li = document.createElement('li');
            li.className = 'list-group-item d-flex gap-3 align-items-start queue-item';
            li.dataset.queueId = item.id;
            li.dataset.index = idx;
            li.draggable = isDraggable(item);

            const handle = document.createElement('span');
            handle.className = `queue-handle fw-semibold ${li.draggable ? '' : 'is-disabled'}`;
            handle.textContent = '≡';

            const body = document.createElement('div');
            body.className = 'flex-grow-1';

            const title = document.createElement('div');
            title.className = 'fw-semibold';
            title.textContent = item.title || 'Untitled item';

            const meta = document.createElement('div');
            meta.className = 'small text-muted';
            const details = [item.type ? item.type.toUpperCase() : 'QUEUE'];
            if (item.artist) details.push(item.artist);
            const duration = Number(item.duration);
            if (Number.isFinite(duration)) details.push(`${duration.toFixed(0)}s`);
            meta.textContent = details.join(' · ');

            const badges = document.createElement('div');
            badges.className = 'd-flex flex-wrap gap-1 mt-1';

            if (isStopMarker(item)) {
                const badge = document.createElement('span');
                badge.className = 'badge text-bg-danger';
                badge.textContent = MARKER_LABELS.stop;
                badges.appendChild(badge);
            }

            if (isLoopMarker(item)) {
                const badge = document.createElement('span');
                badge.className = 'badge text-bg-info';
                badge.textContent = MARKER_LABELS.loop;
                badges.appendChild(badge);
            }

            if (isOverlayItem(item)) {
                const badge = document.createElement('span');
                badge.className = 'badge text-bg-secondary';
                badge.textContent = 'Overlay';
                badges.appendChild(badge);
            }

            body.appendChild(title);
            body.appendChild(meta);
            if (badges.childNodes.length) body.appendChild(badges);

            li.appendChild(handle);
            li.appendChild(body);
            queueList.appendChild(li);
        });

        setStatus('Drag items to reorder. Stop/loop markers and overlays are locked.');
    };

    const refreshQueue = async () => {
        setStatus('Loading queue…');
        try {
            const res = await fetch('/api/playback/queue');
            if (!res.ok) throw new Error('Unable to load queue.');
            const data = await res.json();
            queueItems = data.queue || [];
            renderQueue();
        } catch (err) {
            queueList.innerHTML = '<li class="list-group-item text-danger">Unable to load queue.</li>';
            setStatus('Queue unavailable.', 'danger');
        }
    };

    const segmentBounds = (items, index) => {
        let start = 0;
        for (let i = index - 1; i >= 0; i -= 1) {
            if (isMarker(items[i])) {
                start = i + 1;
                break;
            }
        }
        let end = items.length;
        for (let i = index + 1; i < items.length; i += 1) {
            if (isMarker(items[i])) {
                end = i;
                break;
            }
        }
        return { start, end };
    };

    const overlayGroupLength = (items, index) => {
        let length = 1;
        for (let i = index + 1; i < items.length; i += 1) {
            if (isMarker(items[i]) || !isOverlayItem(items[i])) break;
            length += 1;
        }
        return length;
    };

    const computeDropIndex = (target, clientY) => {
        if (!target) return null;
        const rect = target.getBoundingClientRect();
        const index = Number(target.dataset.index);
        return clientY > rect.top + rect.height / 2 ? index + 1 : index;
    };

    const clearDropTargets = () => {
        queueList.querySelectorAll('.drop-target').forEach((el) => {
            el.classList.remove('drop-target');
        });
    };

    const canDropAt = (items, dragIndex, dropIndex) => {
        if (dropIndex == null) return false;
        const { start, end } = segmentBounds(items, dragIndex);
        return dropIndex >= start && dropIndex <= end;
    };

    const reorderItems = (items, dragIndex, dropIndex, groupLength) => {
        const updated = [...items];
        const group = updated.splice(dragIndex, groupLength);
        let insertIndex = dropIndex;
        if (insertIndex > dragIndex) {
            insertIndex -= groupLength;
        }
        updated.splice(insertIndex, 0, ...group);
        return updated;
    };

    const persistOrder = async (items) => {
        const updates = items.map((item, idx) => ({ id: item.id, position: idx }));
        for (const update of updates) {
            const res = await fetch('/api/playback/queue/move', {
                method: 'POST',
                headers: { 'Content-Type': 'application/json' },
                body: JSON.stringify({ item_id: update.id, position: update.position }),
            });
            if (!res.ok) throw new Error('queue_update_failed');
        }
    };

    queueList.addEventListener('dragstart', (ev) => {
        const li = ev.target.closest('li[data-queue-id]');
        if (!li || !li.draggable) return;
        dragging = {
            id: Number(li.dataset.queueId),
        };
        li.classList.add('dragging');
        ev.dataTransfer.effectAllowed = 'move';
    });

    queueList.addEventListener('dragend', () => {
        dragging = null;
        clearDropTargets();
        queueList.querySelectorAll('.dragging').forEach((el) => el.classList.remove('dragging'));
    });

    queueList.addEventListener('dragover', (ev) => {
        if (!dragging) return;
        const target = ev.target.closest('li[data-queue-id]');
        const dragIndex = queueItems.findIndex((item) => item.id === dragging.id);
        if (dragIndex < 0) return;
        if (isMarker(queueItems[dragIndex])) return;
        const dropIndex = target ? computeDropIndex(target, ev.clientY) : queueItems.length;
        if (!canDropAt(queueItems, dragIndex, dropIndex)) return;
        if (target) {
            const targetIndex = Number(target.dataset.index);
            if (isMarker(queueItems[targetIndex]) || isOverlayItem(queueItems[targetIndex])) return;
        }
        ev.preventDefault();
        clearDropTargets();
        if (target) target.classList.add('drop-target');
    });

    queueList.addEventListener('drop', async (ev) => {
        if (!dragging) return;
        const target = ev.target.closest('li[data-queue-id]');
        clearDropTargets();
        const dragIndex = queueItems.findIndex((item) => item.id === dragging.id);
        if (dragIndex < 0) return;
        const groupLength = overlayGroupLength(queueItems, dragIndex);
        const dropIndex = target ? computeDropIndex(target, ev.clientY) : queueItems.length;
        if (!canDropAt(queueItems, dragIndex, dropIndex)) return;
        if (target) {
            const targetIndex = Number(target.dataset.index);
            if (isMarker(queueItems[targetIndex]) || isOverlayItem(queueItems[targetIndex])) return;
        }
        ev.preventDefault();
        const reordered = reorderItems(queueItems, dragIndex, dropIndex, groupLength);
        queueItems = reordered;
        renderQueue();
        setStatus('Saving queue order…');
        try {
            await persistOrder(reordered);
            await refreshQueue();
        } catch (err) {
            setStatus('Unable to save queue order.', 'danger');
            await refreshQueue();
        }
    });

    refreshQueue();
})();
