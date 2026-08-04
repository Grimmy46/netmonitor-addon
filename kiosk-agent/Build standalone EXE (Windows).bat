@echo off
REM Build a single self-contained NetMonAgent.exe (Python baked in) for kiosks.
REM Run this ONCE on any Windows PC that has Python; copy the resulting .exe to
REM each kiosk. Nothing needs to be installed on the kiosk itself.
setlocal
cd /d "%~dp0"

echo Installing PyInstaller (one-time)...
python -m pip install --upgrade pyinstaller || goto :err

echo Building NetMonAgent.exe...
python -m PyInstaller --onefile --name NetMonAgent --console netmon_agent.py || goto :err

echo.
echo Done. The agent is at:  dist\NetMonAgent.exe
echo Put NetMonAgent.exe and netmon_agent.config.json in the SAME folder on the kiosk.
echo (Copy netmon_agent.config.example.json to netmon_agent.config.json and paste the token.)
pause
exit /b 0

:err
echo.
echo Build failed. Make sure Python is installed and on PATH (python --version).
pause
exit /b 1
