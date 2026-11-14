@echo off
setlocal
set "ROOT=%~dp0"
set "PYEXE=%ROOT%venv\Scripts\python.exe"
if not exist "%PYEXE%" set "PYEXE=python"

where "%PYEXE%" >nul 2>nul
if errorlevel 1 (
    echo [ERREUR] Python introuvable. Voir README.md ^(section Installation^).
    pause
    exit /b 1
)

cd /d "%ROOT%scripts_retro"

:loop
echo.
echo ===================================================
echo   Dashboard DCS-8000LH - http://127.0.0.1:8099
echo ===================================================
echo.
"%PYEXE%" dashboard_server.py
echo.
echo Le serveur s'est arrete. Nouvelle tentative dans 5s... (Ctrl+C pour quitter)
timeout /t 5 /nobreak >nul
goto loop
