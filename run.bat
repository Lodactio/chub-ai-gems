@echo off
cd /d "%~dp0"

:: Create venv if it doesn't exist
if not exist "venv" (
    echo Creating virtual environment...
    python -m venv venv
)

:: Activate and install
call venv\Scripts\activate.bat
pip install -r requirements.txt

:: Run
python chub_search_tool.py
pause