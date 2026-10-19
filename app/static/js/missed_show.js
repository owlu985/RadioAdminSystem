(() => {
    const form = document.getElementById('missedShowForm');
    if (!form) return;
    const button = form.querySelector('button');
    const status = document.getElementById('missedSaveStatus');
    form.addEventListener('submit', async (event) => {
        event.preventDefault();
        if (button.disabled) return;
        button.disabled = true;
        status.textContent = 'Saving…';
        status.classList.remove('text-danger');
        try {
            const response = await fetch(form.action, {
                method: 'POST', body: new FormData(form), credentials: 'same-origin',
                headers: {Accept: 'application/json'},
            });
            if (!response.ok) throw new Error('Save failed');
            const data = await response.json();
            document.getElementById('missedStatusAlert').hidden = !data.flagged_missed;
            form.elements.flagged_missed.value = data.flagged_missed ? '0' : '1';
            button.textContent = data.flagged_missed ? 'Clear Missed status' : 'Mark as Missed (DJ did not show up)';
            status.textContent = 'Saved.';
        } catch (error) {
            status.textContent = 'Unable to confirm the save. Reload the page to check before trying again.';
            status.classList.add('text-danger');
        } finally {
            button.disabled = false;
        }
    });
})();
