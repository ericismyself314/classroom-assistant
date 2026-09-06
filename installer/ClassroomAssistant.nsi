; 课堂助手 Windows 安装脚本（NSIS，作为 Inno Setup 的备选方案）
; 用法（Windows，已构建好 dist\ClassroomAssistant\ 后）：
;   1) 安装 NSIS（https://nsis.sourceforge.io/Download）
;   2) 右键本文件 → Compile NSIS，或命令行：
;      makensis ClassroomAssistant.nsi
;   产物： installer\ClassroomAssistant-Setup-x64.exe
;
; 要点：仅当前用户（无需管理员）；快捷方式指向 ClassroomAssistant.exe；
; 不含模型；卸载保留用户数据；安装前检测并引导 WebView2。

!define APPNAME "课堂助手"
!define APPVERSION "1.0.0"
!define INSTDIR "$LOCALAPPDATA\Programs\ClassroomAssistant"
!define WEBVIEW2_BOOTSTRAPPER "https://go.microsoft.com/fwlink/p/?LinkId=2124703"

RequestExecutionLevel user
InstallDir "${INSTDIR}"
Name "${APPNAME}"
OutFile "ClassroomAssistant-Setup-x64.exe"
Icon "..\课堂助手.ico"
Unicode True

!include "LogicLib.nsh"
!include "FileFunc.nsh"

Section "安装" SEC_INSTALL
  SetOutPath "$INSTDIR"
  File /r "..\dist\ClassroomAssistant\*"

  ; 开始菜单 + 桌面快捷方式（均指向 exe，绝不指向 bat/ps1/python）
  CreateDirectory "$SMPROGRAMS\${APPNAME}"
  CreateShortCut "$SMPROGRAMS\${APPNAME}\${APPNAME}.lnk" "$INSTDIR\ClassroomAssistant.exe" "" "$INSTDIR\ClassroomAssistant.exe" 0
  CreateShortCut "$DESKTOP\${APPNAME}.lnk" "$INSTDIR\ClassroomAssistant.exe" "" "$INSTDIR\ClassroomAssistant.exe" 0

  WriteUninstaller "$INSTDIR\Uninstall.exe"
SectionEnd

Section "WebView2 检测" SEC_WV
  ReadRegStr $0 HKLM "SOFTWARE\Microsoft\EdgeWebView\WebView2\Core" "EBWebView"
  ReadRegStr $1 HKCU "SOFTWARE\Microsoft\EdgeWebView\WebView2\Core" "EBWebView"
  ${If} $0 == "" And $1 == ""
    MessageBox MB_YESNO|MB_ICONINFORMATION "未检测到 WebView2 运行时（窗口显示需要）。$\n是否现在下载安装（需联网）？选否也可稍后在首启向导中安装。" IDNO skip
    ExecShell "open" "${WEBVIEW2_BOOTSTRAPPER}"
    skip:
  ${EndIf}
SectionEnd

Section "Uninstall" SEC_UNINSTALL
  ; 仅删除安装目录与快捷方式；用户数据（%LOCALAPPDATA%\ClassroomAssistant）保留
  Delete "$SMPROGRAMS\${APPNAME}\${APPNAME}.lnk"
  Delete "$DESKTOP\${APPNAME}.lnk"
  RMDir "$SMPROGRAMS\${APPNAME}"
  RMDir /r "$INSTDIR"
SectionEnd
