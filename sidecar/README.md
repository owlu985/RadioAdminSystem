# RAMS Sidecar (Desktop GUI)

This sidecar launches a lightweight desktop window using `pywebview` to manage the small set of options the sidecar itself needs. It runs as a local Windows-style desktop app and can be packaged with PyInstaller without bundling the main RAMS backend.

The WinUI/.NET implementation has been removed; use the PyInstaller workflow below for a simpler Windows executable.

## Run locally

```bash
python -m sidecar
```

The app starts a local server and opens it inside a desktop window. If the GUI cannot be initialized, it falls back to opening the app in a browser.

Backend dependencies come from the repo `requirements.txt`; `sidecar/requirements.txt` only lists the GUI dependency (`pywebview`), and there is no separate sidecar backend requirements file.

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
pyinstaller --noconfirm --onefile --name rams-sidecar --add-data "sidecar/templates;sidecar/templates" sidecar/app.py
```

The executable will be written to `dist/rams-sidecar.exe`. Use this exe for distribution if you want to avoid the WinUI/.NET build path entirely.
