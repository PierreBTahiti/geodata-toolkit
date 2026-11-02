@echo off
set "PYTHON=%~dp0.venv\Scripts\python.exe"
set "APP=%~dp0src\geodata_toolkit\API_DICT_final.py"

if not exist "%PYTHON%" (
    echo Python du projet introuvable : "%PYTHON%"
    echo Verifie que l'environnement .venv existe dans le dossier du projet.
    pause
    exit /b 1
)

"%PYTHON%" -m streamlit run "%APP%"
if errorlevel 1 pause
