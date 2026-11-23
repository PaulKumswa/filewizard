; Inno Setup script: per-user installer, no admin rights needed.
; Build: iscc installer\filewizard.iss  (after PyInstaller has produced dist\FileWizard)

#define AppVersion GetEnv("FW_VERSION")
#if AppVersion == ""
  #define AppVersion "0.3.0"
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
; FileWizard and its shell extension are 64-bit: install to "Program Files" (not "x86") and use
; the 64-bit registry view
ArchitecturesAllowed=x64compatible
ArchitecturesInstallIn64BitMode=x64compatible
OutputDir=..\dist
OutputBaseFilename=FileWizard-Setup
SetupIconFile=filewizard.ico
LicenseFile=..\LICENSE
UninstallDisplayIcon={app}\filewizardw.exe
Compression=lzma2/max
SolidCompression=yes
WizardStyle=modern
ChangesEnvironment=yes

[Tasks]
Name: "win11menu"; Description: "Windows 11: always show the full right-click menu, so FileWizard is one click away (you can change this later in FileWizard settings)"; Flags: unchecked; Check: IsWindows11

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

Filename: "{app}\filewizard.exe"; Parameters: "win11-menu on --restart-explorer"; Flags: runhidden waituntilterminated; Tasks: win11menu

[UninstallDelete]
; created by "filewizard install" after setup, so the uninstaller must be told about them
Type: filesandordirs; Name: "{app}\icons"
Type: files; Name: "{app}\menu.tsv"
Type: files; Name: "{app}\filewizard.ico"

[UninstallRun]
Filename: "{app}\filewizard.exe"; Parameters: "win11-menu off --only-if-ours"; Flags: runhidden waituntilterminated; RunOnceId: "RevertWin11Menu"
Filename: "{app}\filewizard.exe"; Parameters: "uninstall"; Flags: runhidden waituntilterminated; RunOnceId: "RemoveMenu"

[Registry]
; put filewizard.exe on the PATH for the command line
Root: HKLM; Subkey: "SYSTEM\CurrentControlSet\Control\Session Manager\Environment"; ValueType: expandsz; ValueName: "Path"; ValueData: "{olddata};{app}"; Check: NeedsAddPath(ExpandConstant('{app}'))

[Code]
function IsWindows11: Boolean;
begin
  Result := (GetWindowsVersion shr 24 >= 10) and ((GetWindowsVersion and $FFFF) >= 22000);
end;

procedure RemoveFromPath(Dir: string);
var
  Path: string;
  P: Integer;
begin
  if not RegQueryStringValue(HKEY_LOCAL_MACHINE, 'SYSTEM\CurrentControlSet\Control\Session Manager\Environment', 'Path', Path) then
    exit;
  P := Pos(';' + Uppercase(Dir), Uppercase(Path));
  if P = 0 then exit;
  Delete(Path, P, Length(Dir) + 1);
  RegWriteExpandStringValue(HKEY_LOCAL_MACHINE, 'SYSTEM\CurrentControlSet\Control\Session Manager\Environment', 'Path', Path);
end;

procedure CurUninstallStepChanged(CurUninstallStep: TUninstallStep);
begin
  if CurUninstallStep = usPostUninstall then
  begin
    RemoveFromPath(ExpandConstant('{app}'));
    // settings, log, recent actions, downloaded OCR languages (asked; silent uninstall removes them)
    if SuppressibleMsgBox('Also remove your FileWizard settings and downloaded OCR languages?',
                          mbConfirmation, MB_YESNO, IDYES) = IDYES then
      DelTree(ExpandConstant('{localappdata}\FileWizard'), True, True, True);
  end;
end;

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

[Messages]
FinishedLabel=FileWizard is ready.%n%nRight-click any file (or several) and choose FileWizard.%n%nWindows 11: it's under "Show more options", or hold Shift while you right-click to go straight there.%n%nTip: right-click empty space in a folder to paste a screenshot as a file.
WelcomeLabel2=This will add FileWizard to your right-click menu: convert, compress, merge, trim and fix files without uploading them anywhere.%n%nEverything runs on your PC.
