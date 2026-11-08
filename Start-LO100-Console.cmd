@echo off
cd /d "%~dp0"
if exist ".tools\python\python.exe" (
  ".tools\python\python.exe" lo100.py console --jar "generated\lo100-console\M2.JAR" --launch %*
) else (
  python lo100.py console --jar "generated\lo100-console\M2.JAR" --launch %*
)
if errorlevel 1 pause
