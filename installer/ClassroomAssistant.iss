; 课堂助手 Windows 安装脚本（Inno Setup 6）
; 用法（在 Windows 上，先构建好 dist\ClassroomAssistant\ 后）：
;   1) 安装 Inno Setup 6（免费，https://jrsoftware.org/isdl.php）
;   2) 右键本文件 → Compile，或在命令行：
;      "C:\Program Files (x86)\Inno Setup 6\ISCC.exe" ClassroomAssistant.iss
;   产物： installer\ClassroomAssistant-Setup-x64.exe
;
; 设计要点（对应需求文档阶段十一）：
; - 仅本机用户安装： %LOCALAPPDATA%\Programs\ClassroomAssistant\
; - 开始菜单 + 桌面快捷方式均指向 ClassroomAssistant.exe（绝不指向 bat/ps1/python）
; - 安装包不含任何模型权重（模型首次运行时按需下载）
; - 卸载保留用户数据（数据在 %LOCALAPPDATA%\ClassroomAssistant\，与安装目录分离）
; - 安装前检测 WebView2 运行时，缺失则引导安装 Evergreen 引导器（中文提示）

#define MyAppName "课堂助手"
#define MyAppVersion "1.0.0"
#define MyAppPublisher "ClassroomAssistant"
#define MySource "..\dist\ClassroomAssistant"

[Setup]
AppId={{A1B2C3D4-0000-1111-2222-333344445555}
AppName={#MyAppName}
AppVersion={#MyAppVersion}
AppPublisher={#MyAppPublisher}
DefaultDirName={localappdata}\Programs\ClassroomAssistant
DefaultGroupName={#MyAppName}
; 仅当前用户（无需管理员），符合“双击即用、不污染系统”
PrivilegesRequired=lowest
PrivilegesRequiredOverridesAllowed=commandline
OutputDir=.
OutputBaseFilename=ClassroomAssistant-Setup-x64
Compression=lzma2
SolidCompression=yes
WizardStyle=modern
SetupIconFile=..\课堂助手.ico
; 仅 64 位 Windows 10/11
ArchitecturesInstallIn64BitMode=x64compatible
MinVersion=10.0.19041
UninstallDisplayIcon={app}\ClassroomAssistant.exe
; 升级时若已运行，先关闭
CloseApplications=yes

[Languages]
Name: "cn"; MessagesFile: "ChineseSimplified.isl"

[Files]
; 整个 PyInstaller 单文件夹产物原样复制（不含模型）
Source: "{#MySource}\*"; DestDir: "{app}"; Flags: ignoreversion recursesubdirs createallsubdirs

[Icons]
Name: "{group}\{#MyAppName}"; Filename: "{app}\ClassroomAssistant.exe"; WorkingDir: "{app}"
Name: "{autodesktop}\{#MyAppName}"; Filename: "{app}\ClassroomAssistant.exe"; WorkingDir: "{app}"; Tasks: desktopicon

[Tasks]
Name: "desktopicon"; Description: "创建桌面快捷方式（推荐）"; GroupDescription: "快捷方式："; Flags: checkedonce

[Run]
Filename: "{app}\ClassroomAssistant.exe"; Description: "立即启动 {#MyAppName}"; Flags: nowait postinstall skipifsilent

[Code]
const
  WebView2Reg = 'SOFTWARE\Microsoft\EdgeWebView\WebView2\Core';
  WebView2Bootstrapper = 'https://go.microsoft.com/fwlink/p/?LinkId=2124703';

function IsWebView2Installed(): Boolean;
var
  path: string;
begin
  Result := False;
  { WebView2 的 Evergreen Runtime 通常安装在该目录的版本号子目录中。 }
  if DirExists(ExpandConstant('{pf32}\Microsoft\EdgeWebView\Application')) then
    Result := True;
  if not Result then
    if DirExists(ExpandConstant('{localappdata}\Microsoft\EdgeWebView\Application')) then
      Result := True;
  if RegQueryStringValue(HKLM, WebView2Reg, 'EBWebView', path) then
    Result := True;
  if not Result then
    if RegQueryStringValue(HKCU, WebView2Reg, 'EBWebView', path) then
      Result := True;
end;

function InitializeSetup(): Boolean;
var
  Msg: string;
  Res: Integer;
  ErrorCode: Integer;
begin
  Result := True;
  if not IsWebView2Installed() then
  begin
    Msg := '未检测到 Microsoft WebView2 运行时，应用窗口需要它才能显示。' + #13#10 +
           '是否现在下载并安装 WebView2 运行时（约 1 分钟，需联网）？' + #13#10 +
           '（选择“否”也可继续安装，但首次启动会引导你安装。）';
    Res := MsgBox(Msg, mbConfirmation, MB_YESNOCANCEL);
    if Res = mrYes then
    begin
      if not ShellExec('open', WebView2Bootstrapper, '', '', SW_SHOWNORMAL, ewWaitUntilTerminated, ErrorCode) then
        MsgBox('WebView2 引导器启动失败，请手动安装：' + WebView2Bootstrapper, mbInformation, MB_OK);
    end
    else if Res = mrCancel then
      Result := False;
  end;
end;
