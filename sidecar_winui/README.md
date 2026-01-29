# RAMS Sidecar Native (WinUI 3)

This is a separate, native Windows UI rewrite of the RAMS sidecar. It uses WinUI 3 for the desktop shell and talks to the existing Python sidecar backend for data and actions.

## Structure
- `SidecarWinUI.sln` / `SidecarWinUI.csproj`: WinUI 3 desktop app.
- `Pages/ArchivistPage`: native catalog search and album rip controls.
- `Pages/AuditPage`: native audit controls and recent runs.
- `Pages/ShowAutomatorPage`: embeds the show automator UI for exact feature parity.
- `Pages/OptionsPage`: native options page for music/spreadsheet paths.

## Notes
The WinUI app expects Python to be available and will attempt to start `sidecar/app.py` as its backend. Ensure dependencies from the main repo are installed before running the WinUI app.
