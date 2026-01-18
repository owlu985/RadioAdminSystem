const state = {
    artists: [],
    genres: [],
    activeArtist: null,
    activeGenre: null,
};

const searchInput = document.getElementById('djLibrarySearch');
const searchResults = document.getElementById('searchResults');
const searchMeta = document.getElementById('searchMeta');
const artistList = document.getElementById('artistList');
const artistDetail = document.getElementById('artistDetail');
const artistFilter = document.getElementById('artistFilter');
const genreList = document.getElementById('genreList');
const genreDetail = document.getElementById('genreDetail');
const genreFilter = document.getElementById('genreFilter');
const playlistInput = document.getElementById('playlistInput');
const playlistFile = document.getElementById('playlistFile');
const playlistConvert = document.getElementById('playlistConvert');
const playlistStatus = document.getElementById('playlistStatus');
const playlistMatches = document.getElementById('playlistMatches');
const playlistMissing = document.getElementById('playlistMissing');
const youtubeUrl = document.getElementById('youtubeUrl');
const youtubeConvert = document.getElementById('youtubeConvert');
const youtubeStatus = document.getElementById('youtubeStatus');
const playlistText = document.getElementById('playlistText');
const playlistSave = document.getElementById('playlistSave');
const playlistSaveStatus = document.getElementById('playlistSaveStatus');

function escapeHtml(value) {
    return String(value ?? '')
        .replace(/&/g, '&amp;')
        .replace(/</g, '&lt;')
        .replace(/>/g, '&gt;')
        .replace(/"/g, '&quot;')
        .replace(/'/g, '&#39;');
}

async function loadLibraryIndex() {
    try {
        const res = await fetch('/dj/library/data');
        const data = await res.json();
        state.artists = data.artists || [];
        state.genres = data.genres || [];
        renderArtistList();
        renderGenreList();
    } catch (err) {
        artistList.innerHTML = '<div class="text-muted small p-2">Unable to load artists.</div>';
        genreList.innerHTML = '<div class="text-muted small p-2">Unable to load genres.</div>';
    }
}

function renderArtistList() {
    const filter = (artistFilter.value || '').toLowerCase();
    const items = state.artists.filter(artist => artist.name.toLowerCase().includes(filter));
    artistList.innerHTML = '';
    if (!items.length) {
        artistList.innerHTML = '<div class="text-muted small p-2">No artists found.</div>';
        return;
    }
    items.forEach(artist => {
        const button = document.createElement('button');
        button.type = 'button';
        button.className = `list-group-item list-group-item-action${state.activeArtist === artist.name ? ' active' : ''}`;
        button.textContent = artist.name;
        button.addEventListener('click', () => {
            state.activeArtist = artist.name;
            renderArtistList();
            renderArtistDetail(artist);
        });
        artistList.appendChild(button);
    });
}

function renderArtistDetail(artist) {
    if (!artist) {
        artistDetail.innerHTML = '<div class="text-muted">Select an artist to view albums and tracks.</div>';
        return;
    }
    const albums = artist.albums || [];
    if (!albums.length) {
        artistDetail.innerHTML = '<div class="text-muted">No albums found for this artist.</div>';
        return;
    }
    const albumBlocks = albums.map(album => {
        const tracks = (album.tracks || []).map(track => `
            <tr>
                <td>${escapeHtml(track.title)}</td>
                <td>${escapeHtml(track.album)}</td>
                <td>${escapeHtml(track.genre)}</td>
                <td>${escapeHtml(track.year || '—')}</td>
            </tr>
        `).join('') || '<tr><td colspan="4" class="text-muted">No tracks listed.</td></tr>';
        return `
            <div class="mb-3">
                <div class="fw-semibold">${escapeHtml(album.name)}</div>
                <div class="table-responsive">
                    <table class="table table-sm align-middle">
                        <thead>
                            <tr>
                                <th>Title</th>
                                <th>Album</th>
                                <th>Genre</th>
                                <th>Year</th>
                            </tr>
                        </thead>
                        <tbody>${tracks}</tbody>
                    </table>
                </div>
            </div>
        `;
    }).join('');
    artistDetail.innerHTML = `
        <div class="mb-2">
            <h5 class="mb-0">${escapeHtml(artist.name)}</h5>
            <div class="text-muted small">${albums.length} album(s)</div>
        </div>
        ${albumBlocks}
    `;
}

function renderGenreList() {
    const filter = (genreFilter.value || '').toLowerCase();
    const items = state.genres.filter(genre => genre.name.toLowerCase().includes(filter));
    genreList.innerHTML = '';
    if (!items.length) {
        genreList.innerHTML = '<div class="text-muted small p-2">No genres found.</div>';
        return;
    }
    items.forEach(genre => {
        const button = document.createElement('button');
        button.type = 'button';
        button.className = `list-group-item list-group-item-action${state.activeGenre === genre.name ? ' active' : ''}`;
        button.textContent = genre.name;
        button.addEventListener('click', () => {
            state.activeGenre = genre.name;
            renderGenreList();
            renderGenreDetail(genre);
        });
        genreList.appendChild(button);
    });
}

function renderGenreDetail(genre) {
    if (!genre) {
        genreDetail.innerHTML = '<div class="text-muted">Select a genre to view tracks.</div>';
        return;
    }
    const tracks = (genre.tracks || []).map(track => `
        <tr>
            <td>${escapeHtml(track.title)}</td>
            <td>${escapeHtml(track.artist)}</td>
            <td>${escapeHtml(track.album)}</td>
            <td>${escapeHtml(track.year || '—')}</td>
        </tr>
    `).join('') || '<tr><td colspan="4" class="text-muted">No tracks listed.</td></tr>';
    genreDetail.innerHTML = `
        <div class="mb-2">
            <h5 class="mb-0">${escapeHtml(genre.name)}</h5>
            <div class="text-muted small">${(genre.tracks || []).length} track(s)</div>
        </div>
        <div class="table-responsive">
            <table class="table table-sm align-middle">
                <thead>
                    <tr>
                        <th>Title</th>
                        <th>Artist</th>
                        <th>Album</th>
                        <th>Year</th>
                    </tr>
                </thead>
                <tbody>${tracks}</tbody>
            </table>
        </div>
    `;
}

let searchTimer = null;
async function performSearch() {
    const query = (searchInput.value || '').trim();
    if (query.length < 2) {
        searchResults.innerHTML = '<tr><td colspan="5" class="text-muted">Type at least 2 characters to search.</td></tr>';
        searchMeta.textContent = 'Type at least 2 characters to search.';
        return;
    }
    searchMeta.textContent = 'Searching...';
    try {
        const res = await fetch(`/dj/library/search?q=${encodeURIComponent(query)}`);
        const data = await res.json();
        const items = data.items || [];
        searchMeta.textContent = `${items.length} result(s)`;
        if (!items.length) {
            searchResults.innerHTML = '<tr><td colspan="5" class="text-muted">No matches found.</td></tr>';
            return;
        }
        searchResults.innerHTML = items.map(track => `
            <tr>
                <td>${escapeHtml(track.title)}</td>
                <td>${escapeHtml(track.artist)}</td>
                <td>${escapeHtml(track.album)}</td>
                <td>${escapeHtml(track.genre)}</td>
                <td>${escapeHtml(track.year || '—')}</td>
            </tr>
        `).join('');
    } catch (err) {
        searchMeta.textContent = 'Unable to search right now.';
        searchResults.innerHTML = '<tr><td colspan="5" class="text-muted">Search failed.</td></tr>';
    }
}

function renderPlaylistResults(data) {
    const matches = data.matches || [];
    const missing = data.missing || [];
    playlistMatches.innerHTML = matches.length ? matches.map(item => `
        <tr>
            <td>${escapeHtml(item.input_title || '')}<div class="text-muted small">${escapeHtml(item.input_artist || '')}</div></td>
            <td>${escapeHtml(item.library_title)}<div class="text-muted small">${escapeHtml(item.library_artist)}</div></td>
            <td>${escapeHtml(item.score)}</td>
        </tr>
    `).join('') : '<tr><td colspan="3" class="text-muted">No matches found.</td></tr>';
    playlistMissing.innerHTML = missing.length ? missing.map(item => `
        <li class="list-group-item">${escapeHtml(item.input_title || '')} <span class="text-muted">— ${escapeHtml(item.input_artist || '')}</span></li>
    `).join('') : '<li class="list-group-item text-muted">No missing tracks.</li>';
    playlistText.value = data.playlist_text || '';
    playlistSaveStatus.textContent = '';
    playlistSave.dataset.playlistName = data.name || 'playlist';
}

async function convertTextPlaylist() {
    const text = (playlistInput.value || '').trim();
    playlistStatus.textContent = '';
    if (!text) {
        playlistStatus.textContent = 'Paste some playlist text first.';
        return;
    }
    playlistConvert.disabled = true;
    playlistStatus.textContent = 'Matching tracks...';
    try {
        const res = await fetch('/dj/library/import', {
            method: 'POST',
            headers: {'Content-Type': 'application/json'},
            body: JSON.stringify({ text, name: 'Imported Playlist' })
        });
        const data = await res.json();
        if (!res.ok || data.error) {
            playlistStatus.textContent = data.error || 'Unable to convert playlist.';
            playlistMatches.innerHTML = '<tr><td colspan="3" class="text-muted">No matches.</td></tr>';
            playlistMissing.innerHTML = '<li class="list-group-item text-muted">No missing tracks reported yet.</li>';
            playlistText.value = '';
            return;
        }
        playlistStatus.textContent = `Playlist "${data.name}" loaded.`;
        renderPlaylistResults(data);
    } catch (err) {
        playlistStatus.textContent = 'Unable to reach playlist conversion service.';
    } finally {
        playlistConvert.disabled = false;
    }
}

async function convertYoutubePlaylist() {
    const url = (youtubeUrl.value || '').trim();
    youtubeStatus.textContent = '';
    if (!url) {
        youtubeStatus.textContent = 'Please enter a YouTube playlist URL.';
        return;
    }
    youtubeConvert.disabled = true;
    youtubeStatus.textContent = 'Fetching YouTube playlist...';
    try {
        const res = await fetch('/dj/library/youtube', {
            method: 'POST',
            headers: {'Content-Type': 'application/json'},
            body: JSON.stringify({ playlist_url: url })
        });
        const data = await res.json();
        if (!res.ok || data.error) {
            youtubeStatus.textContent = data.error || 'Unable to convert YouTube playlist.';
            playlistMatches.innerHTML = '<tr><td colspan="3" class="text-muted">No matches.</td></tr>';
            playlistMissing.innerHTML = '<li class="list-group-item text-muted">No missing tracks reported yet.</li>';
            playlistText.value = '';
            return;
        }
        youtubeStatus.textContent = `Playlist "${data.name}" loaded.`;
        renderPlaylistResults(data);
    } catch (err) {
        youtubeStatus.textContent = 'Unable to reach YouTube conversion service.';
    } finally {
        youtubeConvert.disabled = false;
    }
}

async function savePlaylist() {
    const content = (playlistText.value || '').trim();
    if (!content) {
        playlistSaveStatus.textContent = 'No playlist content to save yet.';
        return;
    }
    const name = playlistSave.dataset.playlistName || 'playlist';
    playlistSave.disabled = true;
    playlistSaveStatus.textContent = 'Saving playlist file...';
    try {
        const res = await fetch('/dj/library/playlist/save', {
            method: 'POST',
            headers: {'Content-Type': 'application/json'},
            body: JSON.stringify({ name, content })
        });
        const data = await res.json();
        if (!res.ok || data.error) {
            playlistSaveStatus.textContent = data.error || 'Unable to save playlist.';
            return;
        }
        playlistSaveStatus.innerHTML = `Saved. <a href="${data.download_url}" class="link-primary">Download playlist file</a>`;
    } catch (err) {
        playlistSaveStatus.textContent = 'Unable to save playlist file.';
    } finally {
        playlistSave.disabled = false;
    }
}

searchInput.addEventListener('input', () => {
    if (searchTimer) {
        clearTimeout(searchTimer);
    }
    searchTimer = setTimeout(performSearch, 300);
});

artistFilter.addEventListener('input', renderArtistList);
genreFilter.addEventListener('input', renderGenreList);
playlistConvert.addEventListener('click', convertTextPlaylist);
youtubeConvert.addEventListener('click', convertYoutubePlaylist);
playlistSave.addEventListener('click', savePlaylist);

playlistFile.addEventListener('change', () => {
    const file = playlistFile.files && playlistFile.files[0];
    if (!file) return;
    const reader = new FileReader();
    reader.onload = () => {
        playlistInput.value = String(reader.result || '');
    };
    reader.readAsText(file);
});

loadLibraryIndex();
