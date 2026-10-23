const {test} = require('node:test');
const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const vm = require('node:vm');
const source = fs.readFileSync(path.join(__dirname, '../app/static/js/probe_clear.js'), 'utf8');

function page(fetch) {
    let submit;
    const button = {disabled: false, textContent: 'Clear automated Missed flag'};
    const status = {textContent: ''};
    const form = {action: '/shows/audit/clear-probe-missed/1',
        querySelector: selector => selector === 'button' ? button : status,
        addEventListener: (_, callback) => { submit = callback; }};
    vm.runInNewContext(source, {document: {querySelectorAll: () => [form]}, fetch, FormData: class {}});
    return {button, status, submit: () => submit({preventDefault() {}})};
}

test('confirmed clear prevents duplicate submits and saves without a page reload', async () => {
    let resolve;
    let calls = 0;
    const state = page((url, options) => {
        calls++;
        assert.equal(options.headers.Accept, 'application/json');
        assert.equal(options.credentials, 'same-origin');
        return new Promise(done => { resolve = done; });
    });
    const saving = state.submit();
    await state.submit();
    assert.equal(calls, 1);
    assert.equal(state.button.disabled, true);
    resolve({ok: true, json: async () => ({cleared: true})});
    await saving;
    assert.equal(state.button.textContent, 'Automated flag cleared');
    assert.equal(state.button.disabled, true);
    assert.match(state.status.textContent, /Refresh the page/);
});

for (const response of [{ok: false}, {ok: true, json: async () => ({cleared: false})}]) {
    test(`unconfirmed response ${response.ok} does not claim success`, async () => {
        const state = page(async () => response);
        await state.submit();
        assert.equal(state.button.disabled, false);
        assert.equal(state.button.textContent, 'Clear automated Missed flag');
        assert.match(state.status.textContent, /Unable to confirm/);
    });
}
