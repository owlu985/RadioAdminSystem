/* Explicit AM/PM controls: submit the existing canonical values to RAMS. */
(() => {
    'use strict';
    if (window.RamsTime) return;
    function formatClock(value) {
        const match = /^(\d{2}):(\d{2})(?::(\d{2})(?:\.\d+)?)?$/.exec(value || '');
        if (!match) return value || '';
        const hour = Number(match[1]);
        return `${hour % 12 || 12}:${match[2]}${match[3] ? ':' + match[3] : ''} ${hour < 12 ? 'AM' : 'PM'}`;
    }
    function parseClock(value) {
        const match = /^(1[0-2]|0?[1-9]):([0-5]\d)(?::([0-5]\d))?\s*(AM|PM)$/i.exec(value.trim());
        if (!match) return '';
        const hour = Number(match[1]) % 12 + (match[4].toUpperCase() === 'PM' ? 12 : 0);
        return `${String(hour).padStart(2, '0')}:${match[2]}${match[3] ? ':' + match[3] : ''}`;
    }
    window.RamsTime = {formatClock};
    let sequence = 0;
    function enhance(source) {
        if (source.dataset.timeSource) return;
        const type = source.type, required = source.required;
        const validator = source.cloneNode();
        const container = document.createElement('span');
        container.className = 'd-flex flex-wrap gap-2';
        const clock = document.createElement('input');
        clock.type = 'text';
        clock.className = source.className;
        clock.style.minWidth = '9em';
        clock.style.flex = '1';
        clock.id = `${source.id || 'rams-time-' + ++sequence}-display`;
        clock.placeholder = 'h:mm AM/PM';
        clock.autocomplete = 'off';
        clock.required = required;
        clock.disabled = source.disabled;
        clock.readOnly = source.readOnly;
        const label = source.id && Array.from(document.querySelectorAll('label[for]')).find(el => el.htmlFor === source.id);
        if (label) label.htmlFor = clock.id;
        const labelText = source.getAttribute('aria-label') || (label && label.textContent.trim()) || source.name || 'Time';
        clock.setAttribute('aria-label', `${labelText} (12-hour time, AM/PM)`);
        clock.title = 'Enter a 12-hour time, for example 1:30 PM';
        if (source.hasAttribute('aria-describedby')) clock.setAttribute('aria-describedby', source.getAttribute('aria-describedby'));
        let date;
        if (type === 'datetime-local') {
            date = document.createElement('input');
            date.type = 'date';
            date.className = source.className;
            date.required = required;
            date.disabled = source.disabled;
            date.readOnly = source.readOnly;
            date.setAttribute('aria-label', `${labelText} date`);
            container.append(date);
        }
        container.append(clock);
        source.after(container);
        source.dataset.timeSource = type;
        // Text inputs retain a separate defaultValue, so form reset still works.
        source.type = 'text';
        source.hidden = true;
        source.style.display = 'none';
        source.required = false;
        function syncDisplay() {
            const parts = source.value.split('T');
            if (date) date.value = parts.length === 2 ? parts[0] : '';
            clock.value = formatClock(date ? parts[1] : source.value);
            clock.setCustomValidity('');
        }
        // Existing actions such as log "Now" and restored drafts set .value directly.
        const descriptor = Object.getOwnPropertyDescriptor(HTMLInputElement.prototype, 'value');
        Object.defineProperty(source, 'value', {
            configurable: true,
            get() { return descriptor.get.call(this); },
            set(value) { descriptor.set.call(this, value); syncDisplay(); }
        });
        function update() {
            const parsed = parseClock(clock.value);
            const canonical = parsed && (!date || date.value) ? (date ? `${date.value}T` : '') + parsed : '';
            // The detached native control checks step/min/max without locale-dependent UI.
            validator.value = canonical;
            clock.setCustomValidity(clock.value && !parsed ? 'Enter a time such as 1:30 PM.' : validator.validationMessage);
            descriptor.set.call(source, canonical);
            source.dispatchEvent(new Event('input', {bubbles: true}));
            source.dispatchEvent(new Event('change', {bubbles: true}));
        }
        clock.addEventListener('input', update);
        clock.addEventListener('blur', () => { if (clock.validity.valid && clock.value) clock.value = formatClock(parseClock(clock.value)); });
        if (date) date.addEventListener('input', update);
        source.form?.addEventListener('reset', () => setTimeout(syncDisplay, 0));
        syncDisplay();
    }
    function scan(root) {
        if (root.matches?.('input[type="time"],input[type="datetime-local"]')) enhance(root);
        root.querySelectorAll?.('input[type="time"],input[type="datetime-local"]').forEach(enhance);
    }
    function init() {
        scan(document);
        new MutationObserver(records => records.forEach(record => record.addedNodes.forEach(node => {
            if (node.nodeType === Node.ELEMENT_NODE) scan(node);
        }))).observe(document.body, {childList: true, subtree: true});
    }
    if (document.readyState === 'loading') document.addEventListener('DOMContentLoaded', init);
    else init();
})();
