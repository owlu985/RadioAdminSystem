Param(
    [string]$Python = "python"
)

$root = Split-Path -Parent $PSScriptRoot
$backend = Join-Path (Join-Path $root "sidecar") "app.py"
$requirements = Join-Path (Join-Path $root "sidecar") "requirements-backend.txt"
$dist = Join-Path $PSScriptRoot "dist"

if (!(Test-Path $backend)) {
    Write-Error "Could not find sidecar/app.py at $backend"
    exit 1
}

if (!(Test-Path $requirements)) {
    Write-Error "Could not find $requirements"
    exit 1
}

& $Python -m pip install --upgrade pip
if ($LASTEXITCODE -ne 0) { exit $LASTEXITCODE }

& $Python -m pip install -r $requirements
if ($LASTEXITCODE -ne 0) { exit $LASTEXITCODE }

& $Python -m pip install pyinstaller
if ($LASTEXITCODE -ne 0) { exit $LASTEXITCODE }

& $Python -m PyInstaller --onefile --name rams-sidecar-backend $backend --distpath $dist
if ($LASTEXITCODE -ne 0) { exit $LASTEXITCODE }

$exePath = Join-Path $dist "rams-sidecar-backend.exe"
if (Test-Path $exePath) {
    Write-Host "Backend exe written to $exePath"
} else {
    Write-Error "PyInstaller did not produce $exePath"
    exit 1
}
