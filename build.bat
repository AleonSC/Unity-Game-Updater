@echo off
REM Build Game Updater locally on Windows. Requires Python 3.10+ on PATH.
REM Makes dist\GameUpdater\GameUpdater.exe, and dist\GameUpdater-Setup.exe if Inno Setup 6 is installed.
python -m pip install -r requirements-build.txt || exit /b 1
python -m PyInstaller --noconfirm --onedir --windowed --name GameUpdater ^
  --icon assets\icon.ico --add-data "assets;assets" run_game_updater.py || exit /b 1
echo.
echo Built dist\GameUpdater\GameUpdater.exe
set ISCC="%ProgramFiles(x86)%\Inno Setup 6\ISCC.exe"
if exist %ISCC% (
  %ISCC% /Q installer\GameUpdater.iss && echo Built dist\GameUpdater-Setup.exe
)
