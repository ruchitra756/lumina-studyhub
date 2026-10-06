@echo off
cd /d "%~dp0"
if not exist .lumina_env (
  echo Creating virtual environment...
  python -m venv .lumina_env
)
call .lumina_env\Scripts\activate
pip install -r requirements.txt
if not exist .env copy .env.example .env
python launch.py
pause
