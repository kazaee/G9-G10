@echo off
setlocal EnableDelayedExpansion
set "COUNT=0"
set "TARGET="
for /f "tokens=1,2 delims==" %%I in ('wmic diskdrive where "Size>0" get Index /value') do (
  if /I "%%I"=="Index" (
    set /a COUNT+=1
    set /a TARGET=%%J >nul
  )
)
echo COUNT=!COUNT! TARGET=!TARGET!
