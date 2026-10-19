const {test} = require('node:test');
const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const vm = require('node:vm');
const source = fs.readFileSync(path.join(__dirname, '../app/static/js/missed_show.js'), 'utf8');

function page(fetch) {
    const button = {disabled: false};
    const status = {textContent: '', classList: {add() {}, remove() {}}};
    const alert = {hidden: true};
    let submit;
    const form = {action: '/recordings/missed/token', elements: {flagged_missed: {value: '1'}},
        querySelector: () => button, addEventListener: (name, handler) => { submit = handler; }};
    vm.runInNewContext(source, {
        document: {getElementById: id => ({missedShowForm: form, missedSaveStatus: status, missedStatusAlert: alert}[id])},
        fetch, FormData: class { constructor(form) { this.flagged = form.elements.flagged_missed.value; } },
    });
    return {form, button, status, alert, submit: () => submit({preventDefault() {}})};
}

test('save updates in place, prevents duplicate requests, and supports clearing', async () => {
    let finish;
    let calls = 0;
    const pageState = page((url, options) => {
        calls++;
        assert.equal(options.method, 'POST');
        assert.equal(options.headers.Accept, 'application/json');
        assert.equal(options.credentials, 'same-origin');
        return new Promise(resolve => { finish = () => resolve({ok: true, json: async () => ({flagged_missed: options.body.flagged === '1'})}); });
    });
    const pending = pageState.submit();
    assert.equal(pageState.button.disabled, true);
    assert.equal(pageState.status.textContent, 'Saving…');
    await pageState.submit();
    assert.equal(calls, 1);
    finish();
    await pending;
    assert.equal(pageState.button.disabled, false);
    assert.equal(pageState.alert.hidden, false);
    assert.equal(pageState.form.elements.flagged_missed.value, '0');
    assert.equal(pageState.button.textContent, 'Clear Missed status');
    assert.equal(pageState.status.textContent, 'Saved.');
    const clearing = pageState.submit();
    finish();
    await clearing;
    assert.equal(pageState.alert.hidden, true);
    assert.equal(pageState.form.elements.flagged_missed.value, '1');
});

for (const failure of ['http', 'network']) {
    test(`${failure} failure preserves displayed status and allows recovery`, async () => {
        const pageState = page(async () => {
            if (failure === 'network') throw new Error('offline');
            return {ok: false};
        });
        await pageState.submit();
        assert.equal(pageState.button.disabled, false);
        assert.equal(pageState.alert.hidden, true);
        assert.equal(pageState.form.elements.flagged_missed.value, '1');
        assert.match(pageState.status.textContent, /Unable to confirm/);
    });
}
