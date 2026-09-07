; Inno Setup script for the Campus Captions Windows installer.
;
; Built by winapp/build.ps1, which passes AppVersion in on the command line
; from launcher/version.py - do not hard-code a version here.
;
;   iscc /DAppVersion=0.2.5 winapp\installer.iss
;
; Produces winapp\dist\CampusCaptions-Setup-<version>.exe: one file to
; download, one double-click to install. That is the whole point of this
; file. The audience for this project is a volunteer in a booth, and "unzip
; this folder, then find the .bat inside it, then click past the SmartScreen
; warning" is three chances to give up before anything has happened.

#ifndef AppVersion
  #define AppVersion "0.0.0"
#endif
; The numeric part of AppVersion, e.g. "0.3.0" for "0.3.0-beta". Inno derives
; VersionInfoVersion from AppVersion only when that parses as a version
; number, and this project tags releases with a -beta suffix - so it is
; passed separately rather than left to chance.
#ifndef AppVersionNumber
  #define AppVersionNumber AppVersion
#endif

#define AppName "Campus Captions"
#define AppPublisher "Campus Captions"
#define AppURL "https://github.com/koakley3/campus-captions"
#define AppExeName "Campus Captions.exe"

[Setup]
; A fixed GUID: this is how Windows recognises an upgrade of an existing
; install rather than a second copy alongside it. Never regenerate it.
AppId={{8E4C1F27-6D3A-4B92-A5E8-0C7B9D2F4A61}
AppName={#AppName}
AppVersion={#AppVersion}
AppVerName={#AppName} {#AppVersion}
VersionInfoVersion={#AppVersionNumber}
AppPublisher={#AppPublisher}
AppPublisherURL={#AppURL}
AppSupportURL={#AppURL}
AppUpdatesURL={#AppURL}/releases
DefaultDirName={autopf}\{#AppName}
DefaultGroupName={#AppName}
DisableProgramGroupPage=yes
; Per-user by default, so an operator with no administrator password can
; still install it. `lowest` means no UAC prompt at all when it lands in the
; user's own AppData; choosing an all-users location on the directory page
; elevates on its own.
PrivilegesRequiredOverridesAllowed=dialog
PrivilegesRequired=lowest
OutputDir=dist
OutputBaseFilename=CampusCaptions-Setup-{#AppVersion}
SetupIconFile=icons\app.ico
UninstallDisplayIcon={app}\{#AppExeName}
Compression=lzma2/max
SolidCompression=yes
WizardStyle=modern
; The app itself is x64 (PyInstaller freezes the interpreter it was built
; with), so refuse politely on a 32-bit machine rather than installing
; something that cannot start.
ArchitecturesAllowed=x64compatible
ArchitecturesInstallIn64BitMode=x64compatible
; Shown on the "ready to install" page and in Add or Remove Programs.
AppComments=Live captioning for campus events.

[Languages]
Name: "english"; MessagesFile: "compiler:Default.isl"

[Tasks]
Name: "desktopicon"; Description: "Create a &desktop shortcut"; \
    GroupDescription: "Shortcuts:"
; Most booth machines run this at the start of every service, and the person
; doing it is often not the person who installed it. Off by default all the
; same - a program that adds itself to startup without asking is the kind of
; thing IT departments uninstall.
Name: "startupicon"; Description: "Start Campus Captions when I sign in"; \
    GroupDescription: "Shortcuts:"; Flags: unchecked

[Files]
; The whole PyInstaller onedir output. `recursesubdirs` matters: the Python
; runtime, the viewer pages and the example config are all in subfolders.
Source: "dist\{#AppName}\*"; DestDir: "{app}"; \
    Flags: ignoreversion recursesubdirs createallsubdirs

[Icons]
Name: "{group}\{#AppName}"; Filename: "{app}\{#AppExeName}"
Name: "{group}\Uninstall {#AppName}"; Filename: "{uninstallexe}"
Name: "{autodesktop}\{#AppName}"; Filename: "{app}\{#AppExeName}"; \
    Tasks: desktopicon
Name: "{userstartup}\{#AppName}"; Filename: "{app}\{#AppExeName}"; \
    Tasks: startupicon

[Run]
; Straight into the app, which opens the first-run setup wizard in the
; browser by itself. Nothing to find, nothing to read first.
Filename: "{app}\{#AppExeName}"; \
    Description: "Start {#AppName} now"; \
    Flags: nowait postinstall skipifsilent

[UninstallDelete]
; PyInstaller writes __pycache__ folders next to the frozen modules at
; runtime; without this the install folder survives an uninstall as an empty
; shell of directories.
Type: filesandordirs; Name: "{app}\__pycache__"

[Code]
{ Settings, the Deepgram key and the transcript logs live in
  %LOCALAPPDATA%\Campus Captions (see relay/config.py), deliberately outside
  the install folder - so an upgrade keeps them and an uninstall leaves them.
  Offer to remove them, defaulting to no: someone reinstalling to fix a
  problem should not silently lose their event configuration. }
procedure CurUninstallStepChanged(CurUninstallStep: TUninstallStep);
var
  DataDir: String;
begin
  if CurUninstallStep = usPostUninstall then
  begin
    DataDir := ExpandConstant('{localappdata}\{#AppName}');
    if DirExists(DataDir) then
      if MsgBox('Also delete your Campus Captions settings, API keys and logs?'
                + #13#10 + #13#10 + DataDir + #13#10 + #13#10
                + 'Choose No to keep them for a future reinstall.',
                mbConfirmation, MB_YESNO or MB_DEFBUTTON2) = IDYES then
        DelTree(DataDir, True, True, True);
  end;
end;
