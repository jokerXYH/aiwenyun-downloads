@echo off
setlocal
cd /d "%~dp0"

pyinstaller --noconfirm --clean --onefile --windowed ^
  --name "爱问云邀请码导出2.0" ^
  --icon app.ico ^
  --add-data "app.ico;." ^
  --paths . ^
  --exclude-module numpy ^
  --exclude-module PIL ^
  --exclude-module lxml ^
  --exclude-module pandas ^
  --exclude-module scipy ^
  --exclude-module matplotlib ^
  --hidden-import gui ^
  --hidden-import pipeline ^
  --hidden-import token_source ^
  --hidden-import token_model ^
  --hidden-import token_windows ^
  --hidden-import net ^
  --hidden-import analyze ^
  --hidden-import matrix ^
  --hidden-import excel ^
  --hidden-import config ^
  run_gui.py

echo.
echo 输出：dist\爱问云邀请码导出2.0.exe
pause
