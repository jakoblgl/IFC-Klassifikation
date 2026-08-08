@echo off
cd /d "%~dp0"

if not exist ".venv\Scripts\python.exe" (
    echo Erstelle einmalig die lokale Python-Umgebung, das kann ein paar Minuten dauern...
    py -3.11 -m venv .venv
    if errorlevel 1 (
        echo.
        echo Python 3.11 wurde nicht gefunden.
        echo Bitte Python 3.11 installieren - NICHT unbedingt die neueste Version
        echo von https://www.python.org/downloads/, denn ifcopenshell unterstuetzt
        echo die aktuellsten Python-Versionen teils noch nicht. Direkter Link:
        echo https://www.python.org/downloads/release/python-3119/
        echo Dort "Windows installer (64-bit)" herunterladen, installieren,
        echo dann diese Datei erneut ausfuehren.
        pause
        exit /b 1
    )
    ".venv\Scripts\python.exe" -m pip install -r requirements.txt
)

".venv\Scripts\python.exe" launch_gui.py
