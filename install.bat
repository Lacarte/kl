@echo off
set "SOURCE_FILE=%~dp0KL-RUNNER.vbs"
set "STARTUP_FOLDER=%APPDATA%\Microsoft\Windows\Start Menu\Programs\Startup"
set "SHORTCUT_NAME=KL-RUNNER Shortcut.lnk"
set "VBS_SCRIPT=%temp%\CreateShortcut.vbs"

echo Installing KL-RUNNER.vbs shortcut to Windows Startup Folder...
echo.

if not exist "%SOURCE_FILE%" (
    echo [ERROR] KL-RUNNER.vbs was not found in the current directory.
    echo Please ensure this install.bat is in the exact same folder as KL-RUNNER.vbs.
    goto :end
)

echo Creating shortcut...
    
:: Use VBScript to create the shortcut
echo Set oWS = WScript.CreateObject("WScript.Shell") > "%VBS_SCRIPT%"
echo sLinkFile = "%STARTUP_FOLDER%\%SHORTCUT_NAME%" >> "%VBS_SCRIPT%"
echo Set oLink = oWS.CreateShortcut(sLinkFile) >> "%VBS_SCRIPT%"
echo oLink.TargetPath = "%SOURCE_FILE%" >> "%VBS_SCRIPT%"
echo oLink.WorkingDirectory = "%~dp0" >> "%VBS_SCRIPT%"
echo oLink.Description = "KL-RUNNER Shortcut" >> "%VBS_SCRIPT%"
echo oLink.Save >> "%VBS_SCRIPT%"

cscript /nologo "%VBS_SCRIPT%"
del "%VBS_SCRIPT%"

if exist "%STARTUP_FOLDER%\%SHORTCUT_NAME%" (
    echo [SUCCESS] Shortcut to KL-RUNNER.vbs has been successfully added to your Startup folder!
) else (
    echo [ERROR] Failed to create the shortcut. Please check if you have the correct permissions.
)

:end
echo.
pause
