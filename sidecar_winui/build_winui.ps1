Set-StrictMode -Version Latest
$ErrorActionPreference = "Stop"

$root = Split-Path -Parent $PSScriptRoot
$project = Join-Path $PSScriptRoot "SidecarWinUI" "SidecarWinUI.csproj"

Write-Host "Building WinUI project: $project"
dotnet --info

dotnet clean $project
dotnet restore $project
dotnet publish $project -c Release -r win-x64

$publishDir = Join-Path $PSScriptRoot "SidecarWinUI" "bin" "Release" "net8.0-windows10.0.19041.0" "win-x64" "publish"
Write-Host "Publish output: $publishDir"
