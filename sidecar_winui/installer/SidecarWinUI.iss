[Setup]
AppName=RAMS Sidecar (WinUI)
AppVersion=1.0.0
DefaultDirName={pf}\RAMS Sidecar
DefaultGroupName=RAMS Sidecar
OutputDir=.\dist
OutputBaseFilename=RAMS-Sidecar-Setup
Compression=lzma
SolidCompression=yes

[Files]
Source: "..\SidecarWinUI\bin\Release\net8.0-windows10.0.19041.0\win-x64\publish\*"; DestDir: "{app}"; Flags: recursesubdirs createallsubdirs
Source: "..\dist\rams-sidecar-backend.exe"; DestDir: "{app}"; Flags: ignoreversion

[Icons]
Name: "{group}\RAMS Sidecar"; Filename: "{app}\SidecarWinUI.exe"
