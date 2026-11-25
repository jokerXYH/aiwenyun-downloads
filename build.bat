@echo off
chcp 65001 >nul
setlocal
cd /d "%~dp0"

pyinstaller --noconfirm --clean --onefile --windowed ^
  --name "爱问云集成工具" ^
  --icon app.ico ^
  --add-data "app.ico;." ^
  --add-data "assets;assets" ^
  --paths . ^
  --exclude-module numpy ^
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
  --hidden-import login ^
  --hidden-import banshu_export ^
  --hidden-import feedback_export ^
  --hidden-import homework_export ^
  --hidden-import update_checker ^
  --hidden-import update_installer ^
  --hidden-import theme_art ^
  --hidden-import PIL ^
  --hidden-import PIL.ImageDraw ^
  --hidden-import PIL.ImageTk ^
  --hidden-import PIL._tkinter_finder ^
  --hidden-import certifi ^
  --collect-data certifi ^
  run_gui.py

echo.
echo 输出：dist\爱问云集成工具.exe
pause
