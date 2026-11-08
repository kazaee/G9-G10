@echo off
cd /d "%~dp0"
if exist ".tools\python\python.exe" (
  ".tools\python\python.exe" app.py --open
) else (
  python app.py --open
)
if errorlevel 1 pause
