@echo off
setlocal

set "SCRIPT_DIR=%~dp0"
set "PYTHON_EXE=%SCRIPT_DIR%..\.venv\Scripts\python.exe"
if not exist "%PYTHON_EXE%" set "PYTHON_EXE=C:\Users\Osi\AppData\Local\Programs\Python\Python311\python.exe"
set "BOT_FILE=%SCRIPT_DIR%bot.py"
set "LOG_DIR=%SCRIPT_DIR%logs"
set "LOG_FILE=%LOG_DIR%\bot_console.log"

if not exist "%LOG_DIR%" mkdir "%LOG_DIR%"

if /I "%~1"=="restart" (
    echo [INFO] Restart mode: stopping all python.exe processes...
    taskkill /F /IM python.exe >nul 2>&1
    timeout /t 1 >nul
)

if not exist "%PYTHON_EXE%" (
    echo [ERROR] Python not found: %PYTHON_EXE%
    exit /b 1
)

echo [INFO] Starting bot: "%BOT_FILE%"
start "chinaya-bot" /B "%PYTHON_EXE%" "%BOT_FILE%" >> "%LOG_FILE%" 2>&1

timeout /t 2 >nul
tasklist | findstr /I "python.exe" >nul
if %errorlevel% neq 0 (
    echo [ERROR] Bot did not start. Check log: "%LOG_FILE%"
    exit /b 1
)

echo [OK] Bot started. Log: "%LOG_FILE%"
exit /b 0
