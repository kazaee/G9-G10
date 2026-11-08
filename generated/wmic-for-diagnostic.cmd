@echo off
setlocal EnableDelayedExpansion
for /f "tokens=2 delims==" %%I in ('wmic diskdrive get Index /value ^| findstr /B /C:"Index="') do (
  set "TARGET=%%I"
)
echo select disk !TARGET! > generated\wmic-diskpart-readonly.txt
echo list disk >> generated\wmic-diskpart-readonly.txt
diskpart /s generated\wmic-diskpart-readonly.txt
echo DISKPART_EXIT=!ERRORLEVEL!
