# RAMS Sidecar Native (WinUI 3)

This is a separate, native Windows UI rewrite of the RAMS sidecar. It uses WinUI 3 for the desktop shell and talks to the existing Python sidecar backend for data and actions.

## Structure
- `SidecarWinUI.sln` / `SidecarWinUI.csproj`: WinUI 3 desktop app.
- `Pages/ArchivistPage`: native catalog search and album rip controls.
- `Pages/AuditPage`: native audit controls and recent runs.
- `Pages/ShowAutomatorPage`: embeds the show automator UI for exact feature parity.
- `Pages/OptionsPage`: native options page for music/spreadsheet paths.

## Notes
The WinUI app will first look for a bundled backend executable named `rams-sidecar-backend.exe` next to the WinUI app. If it is not found, it falls back to launching `sidecar/app.py` with `python`.

### Bundle the backend (no Python required at runtime)
Run the script below from PowerShell to generate a single-file backend exe:

```powershell
.\sidecar_winui\build_backend.ps1
```

Copy `sidecar_winui\dist\rams-sidecar-backend.exe` into the WinUI app output directory (next to `SidecarWinUI.exe`) so the WinUI launcher can start it automatically.

If you need `msgspec` (optional dependency listed in the main requirements), install the Microsoft C++ Build Tools or use a Python version with prebuilt wheels (for example 3.12) before running the script.

## Build the WinUI exe (no win10 RID required)
Run the helper script (recommended):
```powershell
.\sidecar_winui\build_winui.ps1
```

Or run the publish command directly:
```powershell
dotnet publish sidecar_winui\SidecarWinUI\SidecarWinUI.csproj -c Release -r win-x64
```

If the build fails during `XamlCompiler.exe`, ensure these are installed:
- .NET 8 SDK
- Windows 10/11 SDK (10.0.22621 or newer)
- Visual Studio 2022 Build Tools with the **.NET desktop build tools** workload

## Build an installer (Inno Setup)
1. Install Inno Setup.
2. Ensure the WinUI publish output exists and `sidecar_winui\dist\rams-sidecar-backend.exe` exists.
3. Open `sidecar_winui\installer\SidecarWinUI.iss` in Inno Setup and build the installer.
