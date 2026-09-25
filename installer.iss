#define MyAppName "Цифровой рейтинг"
#define MyAppVersion "0.9.0"
#define MyAppExeName "DigitalRating.exe"

[Setup]
AppId={{70F34756-42C1-4E4F-A08F-5640357C7202}
AppName={#MyAppName}
AppVersion={#MyAppVersion}
DefaultDirName={autopf}\DigitalRating
DefaultGroupName={#MyAppName}
OutputDir=installer_output
OutputBaseFilename=DigitalRating_Setup_{#MyAppVersion}
Compression=lzma2
SolidCompression=yes
WizardStyle=modern
ArchitecturesAllowed=x64compatible
ArchitecturesInstallIn64BitMode=x64compatible
PrivilegesRequired=lowest
UninstallDisplayIcon={app}\{#MyAppExeName}

[Languages]
Name: "russian"; MessagesFile: "compiler:Languages\Russian.isl"

[Files]
Source: "dist\DigitalRating.exe"; DestDir: "{app}"; Flags: ignoreversion

[Icons]
Name: "{autoprograms}\{#MyAppName}"; Filename: "{app}\{#MyAppExeName}"
Name: "{autodesktop}\{#MyAppName}"; Filename: "{app}\{#MyAppExeName}"; Tasks: desktopicon

[Tasks]
Name: "desktopicon"; Description: "Создать ярлык на рабочем столе"; GroupDescription: "Дополнительные значки:"

[Run]
Filename: "{app}\{#MyAppExeName}"; Description: "Запустить {#MyAppName}"; Flags: nowait postinstall skipifsilent
