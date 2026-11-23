; Inno Setup script: per-user installer, no admin rights needed.
; Build: iscc installer\filewizard.iss  (after PyInstaller has produced dist\FileWizard)

#define AppVersion GetEnv("FW_VERSION")
#if AppVersion == ""
  #define AppVersion "0.2.0"
#endif

[Setup]
AppId={{6E2B7D59-3D4A-4C61-9F0E-FA11E3A1D0C7}
AppName=FileWizard
AppVersion={#AppVersion}
AppPublisher=FileWizard
DefaultDirName={autopf}\FileWizard
DefaultGroupName=FileWizard
DisableProgramGroupPage=yes
DisableDirPage=yes
; All users: Windows' shell reliably loads machine-registered menu handlers (measured in CI),
; so the installer asks for admin once, like most desktop apps.
PrivilegesRequired=admin
OutputDir=..\dist
OutputBaseFilename=FileWizard-Setup
SetupIconFile=filewizard.ico
UninstallDisplayIcon={app}\filewizardw.exe
Compression=lzma2/max
SolidCompression=yes
WizardStyle=modern
ChangesEnvironment=yes

[Files]
Source: "..\dist\FileWizard\*"; DestDir: "{app}"; Flags: recursesubdirs ignoreversion
; Explorer keeps the shell extension loaded (locked), so each version gets its own file name
; and old ones are removed at the next restart.
Source: "..\native\fwshell.dll"; DestDir: "{app}"; DestName: "fwshell-{#AppVersion}.dll"; Flags: ignoreversion uninsrestartdelete
Source: "machine-install"; DestDir: "{app}"; Flags: ignoreversion

[Icons]
Name: "{group}\FileWizard settings"; Filename: "{app}\filewizardw.exe"; Parameters: "settings"
Name: "{group}\Uninstall FileWizard"; Filename: "{uninstallexe}"

[Run]
Filename: "{app}\filewizard.exe"; Parameters: "install"; Flags: runhidden waituntilterminated; StatusMsg: "Adding FileWizard to the right-click menu..."

[UninstallDelete]
; created by "filewizard install" after setup, so the uninstaller must be told about them
Type: filesandordirs; Name: "{app}\icons"
Type: files; Name: "{app}\menu.tsv"
Type: files; Name: "{app}\filewizard.ico"

[UninstallRun]
Filename: "{app}\filewizard.exe"; Parameters: "uninstall"; Flags: runhidden waituntilterminated; RunOnceId: "RemoveMenu"

[Registry]
; put filewizard.exe on the PATH for the command line
Root: HKLM; Subkey: "SYSTEM\CurrentControlSet\Control\Session Manager\Environment"; ValueType: expandsz; ValueName: "Path"; ValueData: "{olddata};{app}"; Check: NeedsAddPath(ExpandConstant('{app}'))

[Code]
function NeedsAddPath(Param: string): boolean;
var
  OrigPath: string;
begin
  if not RegQueryStringValue(HKEY_LOCAL_MACHINE, 'SYSTEM\CurrentControlSet\Control\Session Manager\Environment', 'Path', OrigPath) then
  begin
    Result := True;
    exit;
  end;
  Result := Pos(';' + Uppercase(Param) + ';', ';' + Uppercase(OrigPath) + ';') = 0;
end;
