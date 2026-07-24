@echo off
REM  Simplifyber -- Standalone BOM -> Purchasing Sheet builder
REM  Double-click this file to produce Simplifyber_BOM_Purchasing.exe in this folder.
cd /d "%~dp0"
set LOG=%~dp0build_purchasing_log.txt
echo Build started: %DATE% %TIME% > "%LOG%"
echo Working directory: %CD% >> "%LOG%"

REM -- Find Python --
set PYTHON=
where py >nul 2>&1 && ( set PYTHON=py & goto :found )
where python >nul 2>&1 && ( set PYTHON=python & goto :found )
where python3 >nul 2>&1 && ( set PYTHON=python3 & goto :found )
echo.
echo  ERROR: Python was not found. Install Python 3.10+ from
echo  https://www.python.org/downloads/ and tick "Add Python to PATH".
echo.
pause
exit /b 1

:found
echo  Using Python: %PYTHON% >> "%LOG%"
echo  Installing required packages...
%PYTHON% -m pip install pandas openpyxl xlrd pyinstaller pillow >> "%LOG%" 2>&1
if %ERRORLEVEL% neq 0 (
    echo  ERROR: package install failed. See build_purchasing_log.txt
    pause
    exit /b 1
)

echo  Building .exe -- this takes 60-120 seconds, please wait...
%PYTHON% -m PyInstaller --onefile --windowed --name "Simplifyber_BOM_Purchasing" --clean --distpath "." --add-data "Simplifyber_Logo.png;." --add-data "Simplifyber_Logo_White.png;." purchasing_standalone.py >> "%LOG%" 2>&1
if %ERRORLEVEL% neq 0 (
    echo  ERROR: PyInstaller build failed. See build_purchasing_log.txt
    pause
    exit /b 1
)
if not exist "%~dp0Simplifyber_BOM_Purchasing.exe" (
    echo  ERROR: build finished but the .exe was not found. See build_purchasing_log.txt
    pause
    exit /b 1
)

REM -- Clean up PyInstaller artifacts (keep the .exe) --
if exist "%~dp0build" rmdir /s /q "%~dp0build"
if exist "%~dp0Simplifyber_BOM_Purchasing.spec" del /q "%~dp0Simplifyber_BOM_Purchasing.spec"

echo.
echo  ---------------------------------------------------------------------------
echo   SUCCESS
echo   Your .exe:  %~dp0Simplifyber_BOM_Purchasing.exe
echo   Share it via SharePoint. Teammates do NOT need Python, Vault, or the MCP.
echo  ---------------------------------------------------------------------------
echo.
explorer "%~dp0"
pause
