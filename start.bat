@echo off
REM Windows Batch Script to set up and run the Sheet Pro Flask application.
REM This script will:
REM 1. Check if Python is installed.
REM 2. Install Python dependencies from requirements.txt.
REM 3. Run the Flask application (app.py) in a new window.
REM 4. Open the application URL in your default web browser.

REM --- Configuration ---
REM Set the Python executable name. Use 'python' for most modern installations.
REM If you have multiple Python versions or specific setups, you might need 'python3' or 'py'.
set PYTHON_EXE=python

REM Set the Flask application file name.
set FLASK_APP_FILE=app.py

REM Set the application URL. Flask typically runs on http://127.0.0.1:5000
set APP_URL=http://127.0.0.1:5000

REM Set the delay in seconds before opening the browser (gives Flask time to start).
set DELAY_SECONDS=5

REM --- Script Start ---
echo.
echo ===========================================
echo   Sheet Pro Application Launcher
echo ===========================================
echo.

REM --- Step 1: Check for Python Installation ---
echo Checking for Python installation...
%PYTHON_EXE% --version >nul 2>&1
if %ERRORLEVEL% NEQ 0 (
    echo.
    echo ERROR: Python is not found or not in your system's PATH.
    echo Please install Python from https://www.python.org/downloads/
    echo Make sure to check the "Add Python to PATH" option during installation.
    echo After installation, please restart this script.
    echo.
    pause
    exit /b 1
)
echo Python found.

REM --- Step 2: Install Python Dependencies ---
echo.
echo Installing Python dependencies from requirements.txt...
REM Check if requirements.txt exists
if not exist "requirements.txt" (
    echo.
    echo WARNING: requirements.txt not found in the current directory.
    echo Skipping dependency installation. If app.py fails, create a requirements.txt
    echo file with "Flask" inside it and place it in the same directory as this script.
    echo.
    pause
) else (
    %PYTHON_EXE% -m pip install -r requirements.txt
    if %ERRORLEVEL% NEQ 0 (
        echo.
        echo ERROR: Failed to install Python dependencies.
        echo Please check your internet connection or pip configuration.
        echo.
        pause
        exit /b 1
    )
    echo Dependencies installed successfully.
)

REM --- Step 3: Run the Flask Application ---
echo.
echo Starting the Flask application (%FLASK_APP_FILE%)...
echo This will open in a new command prompt window.
REM The 'start' command opens a new command prompt window and runs the command in it.
start "Sheet Pro Flask Server" %PYTHON_EXE% %FLASK_APP_FILE%
if %ERRORLEVEL% NEQ 0 (
    echo.
    echo ERROR: Failed to start the Flask application.
    echo Please check %FLASK_APP_FILE% for errors.
    echo.
    pause
    exit /b 1
)
echo Flask application started.

REM --- Step 4: Open URL in Browser ---
echo.
echo Waiting %DELAY_SECONDS% seconds for the server to fully start...
timeout /t %DELAY_SECONDS% /nobreak >nul
echo Opening %APP_URL% in your default web browser...
start %APP_URL%
if %ERRORLEVEL% NEQ 0 (
    echo.
    echo WARNING: Failed to open the URL in the browser.
    echo Please open your web browser manually and navigate to: %APP_URL%
    echo.
) else (
    echo URL opened successfully.
)

echo.
echo Setup and launch complete.
echo You can close this window now, but keep the Flask server window open.
echo.
pause
