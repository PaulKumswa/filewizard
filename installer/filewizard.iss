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
DefaultDirName={localappdata}\Programs\FileWizard
DefaultGroupName=FileWizard
DisableProgramGroupPage=yes
DisableDirPage=yes
PrivilegesRequired=lowest
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

[Icons]
Name: "{group}\FileWizard settings"; Filename: "{app}\filewizardw.exe"; Parameters: "settings"
Name: "{group}\Uninstall FileWizard"; Filename: "{uninstallexe}"

[Run]
Filename: "{app}\filewizard.exe"; Parameters: "install"; Flags: runhidden waituntilterminated; StatusMsg: "Adding FileWizard to the right-click menu..."

[UninstallRun]
Filename: "{app}\filewizard.exe"; Parameters: "uninstall"; Flags: runhidden waituntilterminated; RunOnceId: "RemoveMenu"

[Registry]
; put filewizard.exe on the user's PATH for the command line
Root: HKCU; Subkey: "Environment"; ValueType: expandsz; ValueName: "Path"; ValueData: "{olddata};{app}"; Check: NeedsAddPath(ExpandConstant('{app}'))

[Code]
function NeedsAddPath(Param: string): boolean;
var
  OrigPath: string;
begin
  if not RegQueryStringValue(HKEY_CURRENT_USER, 'Environment', 'Path', OrigPath) then
  begin
    Result := True;
    exit;
  end;
  Result := Pos(';' + Uppercase(Param) + ';', ';' + Uppercase(OrigPath) + ';') = 0;
end;
