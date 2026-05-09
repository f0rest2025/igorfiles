#ifndef AppVersion
#define AppVersion "0.4.4"
#endif

[Setup]
AppId={{B64867F1-4472-4E63-9B26-ACF7B346CA7F}
AppName=Yandex Object Storage Manager
AppVersion={#AppVersion}
AppPublisher=f0rest2025
DefaultDirName={localappdata}\Programs\Yandex Object Storage Manager
DefaultGroupName=Yandex Object Storage Manager
DisableProgramGroupPage=yes
OutputDir=..\dist\installer
OutputBaseFilename=YandexStorageManagerSetup-{#AppVersion}
Compression=lzma2
SolidCompression=yes
WizardStyle=modern
PrivilegesRequired=lowest
UninstallDisplayIcon={app}\YandexStorageManager.exe

[Languages]
Name: "russian"; MessagesFile: "compiler:Languages\Russian.isl"
Name: "english"; MessagesFile: "compiler:Default.isl"

[Tasks]
Name: "desktopicon"; Description: "Создать ярлык на рабочем столе"; GroupDescription: "Ярлыки:"; Flags: unchecked

[Files]
Source: "..\dist\YandexStorageManager\*"; DestDir: "{app}"; Flags: ignoreversion recursesubdirs createallsubdirs

[Icons]
Name: "{autoprograms}\Yandex Object Storage Manager"; Filename: "{app}\YandexStorageManager.exe"
Name: "{autodesktop}\Yandex Object Storage Manager"; Filename: "{app}\YandexStorageManager.exe"; Tasks: desktopicon

[Run]
Filename: "{app}\YandexStorageManager.exe"; Description: "Запустить Yandex Object Storage Manager"; Flags: nowait postinstall skipifsilent
