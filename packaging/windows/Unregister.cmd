@echo off
REM Remove EMPViewer's file-type registration for the current user.

setlocal
set "EXE=%~dp0EMPViewer.exe"

if not exist "%EXE%" (
    echo Could not find EMPViewer.exe next to this script.
    pause
    exit /b 1
)

"%EXE%" --unregister
echo.
echo EMPViewer file associations removed for the current user.
echo (Windows may still list EMPViewer under "Open with" until you pick another
echo  default for those types once.)
pause
