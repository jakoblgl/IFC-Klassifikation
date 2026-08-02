@echo off
cd /d "%~dp0"

if not exist ".venv\Scripts\python.exe" (
    echo Erstelle einmalig die lokale Python-Umgebung, das kann ein paar Minuten dauern...
    py -3.9 -m venv .venv
    if errorlevel 1 (
        echo.
        echo Python 3.9 wurde nicht gefunden. Bitte von https://www.python.org/downloads/ installieren
        echo und diese Datei danach erneut ausfuehren.
        pause
        exit /b 1
    )
    ".venv\Scripts\python.exe" -m pip install -r requirements.txt
)

".venv\Scripts\python.exe" launch_gui.py
