; Installer for Game Updater, built by the GitHub workflow with Inno Setup 6:
;   iscc /DAppVersion=1.0.5 installer\GameUpdater.iss
; It installs for the current user (no admin needed) and installs over an older
; version when run again (which is how the app updates itself).

#ifndef AppVersion
  #define AppVersion "0.0.0"
#endif

[Setup]
AppId={{4B0A44EB-6B2E-4F21-AEB7-B1E1B6EBE262}
AppName=Game Updater
AppVersion={#AppVersion}
AppVerName=Game Updater {#AppVersion}
AppPublisher=AleonSC
DefaultDirName={autopf}\GameUpdater
DefaultGroupName=Game Updater
DisableProgramGroupPage=yes
DisableDirPage=auto
PrivilegesRequired=lowest
OutputDir=..\dist
OutputBaseFilename=GameUpdater-Setup
SetupIconFile=..\assets\icon.ico
UninstallDisplayIcon={app}\GameUpdater.exe
Compression=lzma2/max
SolidCompression=yes
WizardStyle=modern
CloseApplications=yes
RestartApplications=no
ArchitecturesAllowed=x64compatible
ArchitecturesInstallIn64BitMode=x64compatible

[Tasks]
Name: "desktopicon"; Description: "Create a desktop shortcut"; GroupDescription: "Shortcuts:"

[InstallDelete]
; Files from the previous version that the new one may no longer have.
Type: filesandordirs; Name: "{app}\_internal"

[Files]
Source: "..\dist\GameUpdater\*"; DestDir: "{app}"; Flags: ignoreversion recursesubdirs createallsubdirs

[INI]
; Tells the app it was installed, so it can update itself.
Filename: "{app}\install.ini"; Section: "GameUpdater"; Key: "version"; String: "{#AppVersion}"

[Icons]
Name: "{group}\Game Updater"; Filename: "{app}\GameUpdater.exe"
Name: "{autodesktop}\Game Updater"; Filename: "{app}\GameUpdater.exe"; Tasks: desktopicon

[Run]
; A normal install offers to start the app; a silent one (an update from inside
; the app) just starts it again.
Filename: "{app}\GameUpdater.exe"; Description: "Start Game Updater"; Flags: nowait postinstall skipifsilent
Filename: "{app}\GameUpdater.exe"; Flags: nowait; Check: ShouldRelaunch

[Code]
// After a silent update, start the app again (unless /NOLAUNCH, used by the build test).
function ShouldRelaunch: Boolean;
begin
  Result := WizardSilent and (Pos('/NOLAUNCH', UpperCase(GetCmdTail)) = 0);
end;
