@echo off
cd /d "%~dp0"
if not exist .venv (
  py -m venv .venv || python -m venv .venv
  call .venv\Scripts\activate.bat
  python -m pip install -r requirements.txt
) else (
  call .venv\Scripts\activate.bat
)
python jobhunt_agent.py
pause
