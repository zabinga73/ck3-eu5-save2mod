@echo off
cd /d "%~dp0"
python -m save2mod %*
if errorlevel 1 pause
