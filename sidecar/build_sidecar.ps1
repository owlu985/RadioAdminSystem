Set-StrictMode -Version Latest
$ErrorActionPreference = "Stop"

$root = Split-Path -Parent $PSScriptRoot
Set-Location $root

pip install -r requirements.txt
pip install pyinstaller

pyinstaller --noconfirm --onefile --name rams-sidecar `
  --paths . `
  --add-data "app;app" `
  sidecar/app.py

Write-Host "Executable written to: $root\dist\rams-sidecar.exe"
