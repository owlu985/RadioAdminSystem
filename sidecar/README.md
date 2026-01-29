# RAMS Sidecar (Desktop GUI)

This sidecar launches the Archivist, Audit, and Show Automator tools in a lightweight desktop window using `pywebview`. The UI and behavior match the existing RAMS pages, but it runs as a local Windows-style desktop app and can be packaged with PyInstaller.

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
