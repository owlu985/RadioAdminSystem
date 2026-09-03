# RadioDJ REST API reference (v1.4)

This document summarizes the RadioDJ REST API endpoints provided with the `Plugin_RestServer` plugin (v1.4) and how RAMS integrates with them. Use it alongside the RadioDJ documentation when configuring or extending RadioDJ-related features.

## Installation
1. Place `Plugin_RestServer.dll` and `Plugin_RestServer.xml` into the RadioDJ `Plugins` folder.
2. Copy `Newtonsoft.Json.dll` into the main RadioDJ folder (replace if present).
3. Restart RadioDJ.

## Authentication
All endpoints require `auth=[password]` as a query parameter. Protect this password carefully; RAMS expects it in the RadioDJ settings section.

## Endpoints
Each endpoint is accessed at:

```
http(s)://[IP]:[PORT]/<Endpoint>?auth=<password>&command=<command_item>&arg=<argument>
```

### RDJCommand (control)
`/RDJCommand?auth=...&command=...&arg=...` accepts the following commands. The
`arg` parameter is only required by commands that need a track, playlist, cart,
or explicit toggle state.

Examples: `PlayPlaylistTrack`, `RemovePlaylistTrack`, `PlayFromIntro`, `RestartPlayer`, `PausePlayer`, `StopPlayer`, `Loop`, `Record`, `EnableInput`, `LoadTrackToTop`, `LoadTrackToBottom`, `LoadPlaylist`, `PlayCartByNumber`, `EnableAutoDJ`, `EnableAssisted`, `ClearPlaylist`.

### RDJState (status)
`/RDJState?auth=...` returns `AutoDJ`, `Assisted`, `Input`, `Paused`, `Record`, `Loop`, and `QueueCount`.

### Now playing
`/RDJnp?auth=...` returns now-playing information as XML. `/RDJnpjson?auth=...`
returns the now-playing information and current playlist as JSON. RAMS uses the
focused XML endpoint first and falls back to JSON if XML is unavailable or invalid.

### Current playlist
`/RDJp?auth=...` returns the current playlist as XML. Add a zero-based playlist
position as `arg` to return a single item.

### Playlists
Commands: `Main`, `List`, `Item`, `Update`, `Insert`, `Delete`.

### Rotations
Commands: `List`, `Item`, `Update`, `Insert`, `Delete`, `Load` (sets main rotation).

### Events
Commands: `List`, `Item`, `Update`, `Insert`, `Delete`, `Run`, `Refresh`, `Enable` (toggle events engine).

### Categories
Commands: `Categories`, `Subcategories`, `Genres`, `UpdateCategory`, `InsertCategory`, `UpdateGenre`, `InsertGenre`, `DeleteCategory`, `DeleteGenre`.

### Tracks
Commands: `Search`, `Item`, `Update`, `Insert`, `Delete`.

## RAMS integration notes
- **Now-playing fallback**: RAMS calls `RDJnpjson` to populate automation/NowPlaying data when no scheduled show is active.
- **PSA/track management**: RadioDJ playlist/rotation/category/track endpoints support RAMS PSA and library workflows. Keep IDs and payload formats consistent with RadioDJ’s returned structures.
- **Security**: Restrict the plugin port to trusted networks; use firewall rules or reverse proxy authentication when possible.
