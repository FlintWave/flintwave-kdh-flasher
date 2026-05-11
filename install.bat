@echo off
:: FlintWave Flash — Windows installer
:: Download and run this file, or paste the commands into PowerShell/CMD

echo ===================================
echo   FlintWave Flash Installer
echo ===================================
echo.

:: Check Python
where py >nul 2>&1
if %errorlevel% neq 0 (
    echo ERROR: Python is not installed.
    echo Download it from https://python.org
    echo IMPORTANT: Check "Add Python to PATH" during install.
    pause
    exit /b 1
)

echo Installing dependencies...
py -m pip install --quiet pyserial wxPython requests
if %errorlevel% neq 0 (
    echo.
    echo Trying with --user flag...
    py -m pip install --user pyserial wxPython requests
)

:: Clone or update. The local install directory is named flintwave-flash;
:: the GitHub repo URL stays as flintwave-kdh-flasher.
if exist "%USERPROFILE%\flintwave-flash\.git" (
    echo Updating existing installation...
    cd /d "%USERPROFILE%\flintwave-flash"
    git pull --ff-only
) else (
    where git >nul 2>&1
    if %errorlevel% neq 0 (
        echo Git not found. Downloading ZIP instead...
        powershell -Command "Invoke-WebRequest -Uri 'https://github.com/FlintWave/flintwave-kdh-flasher/archive/refs/heads/master.zip' -OutFile '%TEMP%\flintwave-flash.zip'"
        powershell -Command "Expand-Archive -Path '%TEMP%\flintwave-flash.zip' -DestinationPath '%USERPROFILE%' -Force"
        if exist "%USERPROFILE%\flintwave-flash" rmdir /s /q "%USERPROFILE%\flintwave-flash"
        ren "%USERPROFILE%\flintwave-kdh-flasher-master" "flintwave-flash"
    ) else (
        git clone --depth 1 https://github.com/FlintWave/flintwave-kdh-flasher.git "%USERPROFILE%\flintwave-flash"
    )
)

:: Create desktop shortcut
echo Creating desktop shortcut...
powershell -Command "$ws = New-Object -ComObject WScript.Shell; $s = $ws.CreateShortcut('%USERPROFILE%\Desktop\FlintWave Flash.lnk'); $s.TargetPath = 'py'; $s.Arguments = '%USERPROFILE%\flintwave-flash\flash_firmware_gui.py'; $s.WorkingDirectory = '%USERPROFILE%\flintwave-flash'; $s.IconLocation = '%USERPROFILE%\flintwave-flash\icon_128.png'; $s.Save()"

echo.
echo ===================================
echo   Installation complete!
echo ===================================
echo.
echo Desktop shortcut created.
echo Or run: py %USERPROFILE%\flintwave-flash\flash_firmware_gui.py
echo.
pause
