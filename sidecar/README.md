# RAMS Sidecar (Desktop GUI)

This sidecar launches the Archivist, Audit, and Show Automator tools in a lightweight desktop window using `pywebview`. The UI and behavior match the existing RAMS pages, but it runs as a local Windows-style desktop app and can be packaged with PyInstaller.

The WinUI/.NET implementation has been removed; use the PyInstaller workflow below for a simpler Windows executable.

## Run locally

```bash
python -m sidecar
```

The app starts a local server and opens it inside a desktop window. If the GUI cannot be initialized, it falls back to opening the app in a browser.

## Configuration

Use the **Options** menu inside the app to update:
- Music library folder
- MoneyMusic spreadsheet path

Settings persist to `sidecar/instance/sidecar_config.json`.

## Package as a Windows exe (PyInstaller)

From the repo root on Windows:

```powershell
pip install -r requirements.txt
pip install pyinstaller
pyinstaller --noconfirm --onefile --name rams-sidecar --paths . --add-data "app;app" sidecar/app.py
```

If you prefer, run the helper script:

```powershell
.\sidecar\build_sidecar.ps1
```

The executable will be written to `dist/rams-sidecar.exe`. Use this exe for distribution if you want to avoid the WinUI/.NET build path entirely.

If you hit `ModuleNotFoundError: No module named 'app'`, the build is missing the repo `app/` package. Rebuild using the command above or the helper script so PyInstaller bundles the `app` package.
