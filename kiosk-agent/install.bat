@echo off
rem NetMonitor kiosk agent - one-shot installer (zero-touch kit)
set DEST=C:\NetMonAgent
if not exist "%DEST%" mkdir "%DEST%"
copy /Y "%~dp0NetMonAgent.exe" "%DEST%" >nul
copy /Y "%~dp0agent_payload.py" "%DEST%" >nul
rem keep an existing config (holds this kiosk's token) - only seed if missing
if not exist "%DEST%\netmon_agent.config.json" copy /Y "%~dp0netmon_agent.config.json" "%DEST%" >nul
if not exist "%DEST%\NetMonAgent.exe" ( echo Copy failed. & pause & exit /b 1 )
start "" "%DEST%\NetMonAgent.exe"
echo NetMonAgent installed to %DEST% and started - kiosk will appear on the dashboard shortly.
timeout /t 6 >nul
