@echo off
REM Build the NetMonAgent.exe bootstrapper for kiosks.
REM Run this ONCE on any Windows PC that has Python. The .exe rarely needs
REM rebuilding — future agent changes ship via the server payload and kiosks
REM self-update. You only rebuild the .exe if the BOOTSTRAPPER itself changes.
setlocal
cd /d "%~dp0"

REM Refresh the seed payload from the server's canonical copy (best-effort).
if exist "..\cloud\app\agent_runtime\payload.py" copy /Y "..\cloud\app\agent_runtime\payload.py" "agent_payload.py" >nul

echo Installing PyInstaller (one-time)...
python -m pip install --upgrade pyinstaller || goto :err

echo Building NetMonAgent.exe...
python -m PyInstaller --onefile --name NetMonAgent --console netmon_agent.py || goto :err

echo.
echo Done. Copy these THREE files into the SAME folder on each kiosk:
echo    dist\NetMonAgent.exe
echo    netmon_agent.config.json   (copy from the .example, paste the agent token)
echo    agent_payload.py           (the seed; the kiosk auto-updates it from the server)
echo.
echo Then run NetMonAgent.exe. To auto-start at login, put a shortcut to it in
echo the Startup folder (Win+R -> shell:startup).
pause
exit /b 0

:err
echo.
echo Build failed. Make sure Python is installed and on PATH (python --version).
pause
exit /b 1
