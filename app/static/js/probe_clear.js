document.querySelectorAll('.probe-clear-form').forEach((form) => {
    const button = form.querySelector('button');
    const status = form.querySelector('[role="status"]');
    form.addEventListener('submit', async (event) => {
        event.preventDefault();
        if (button.disabled) return;
        button.disabled = true;
        status.textContent = 'Saving…';
        try {
            const response = await fetch(form.action, {
                method: 'POST', body: new FormData(form), credentials: 'same-origin',
                headers: {Accept: 'application/json'},
            });
            if (!response.ok || !(await response.json()).cleared) throw new Error('Save failed');
            button.textContent = 'Automated flag cleared';
            status.textContent = 'Refresh the page to update the audit totals. Manual Missed decisions are unchanged.';
        } catch (error) {
            status.textContent = 'Unable to confirm the save. Reload the page to check before trying again.';
            button.disabled = false;
        }
    });
});
