@echo off
cd /d "%~dp0"
set CONFIG_FILE=config.json
".venv\Scripts\python.exe" -m streamlit run streamlit_app\app.py
