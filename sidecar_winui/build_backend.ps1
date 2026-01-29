Param(
    [string]$Python = "python"
)

$root = Split-Path -Parent $PSScriptRoot
$backend = Join-Path $root "sidecar" "app.py"
$dist = Join-Path $PSScriptRoot "dist"

if (!(Test-Path $backend)) {
    Write-Error "Could not find sidecar/app.py at $backend"
    exit 1
}

& $Python -m pip install --upgrade pip
& $Python -m pip install -r (Join-Path $root "requirements.txt")
& $Python -m pip install pyinstaller

& $Python -m PyInstaller --onefile --name rams-sidecar-backend $backend --distpath $dist

Write-Host "Backend exe written to $dist\\rams-sidecar-backend.exe"
