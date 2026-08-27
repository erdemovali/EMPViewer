@echo off
REM Register the portable EMPViewer.exe (in this same folder) as a handler for
REM .eml / .msg / .pst / .ost for the CURRENT USER. No admin rights needed.
REM
REM IMPORTANT: move this folder to its permanent location BEFORE running this.
REM The registration records the .exe's current path.

setlocal
set "EXE=%~dp0EMPViewer.exe"

if not exist "%EXE%" (
    echo Could not find EMPViewer.exe next to this script.
    pause
    exit /b 1
)

"%EXE%" --register
if errorlevel 1 (
    echo Registration failed.
    pause
    exit /b 1
)

echo.
echo Opening Settings -> Default apps so you can pick EMPViewer for each type...
"%EXE%" --set-default

echo.
echo Done. If a type still opens elsewhere: right-click a file ->
echo   Open with -> Choose another app -> EMPViewer -> tick "Always".
pause
