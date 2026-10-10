@echo off
rem Phosphor OS lights relay: double-click to start. Keep this window open.
cd /d "%~dp0"
title Phosphor OS lights relay
set PY=python
where py >nul 2>nul
if not errorlevel 1 set PY=py
%PY% -m pip install --quiet --upgrade python-kasa
%PY% "%~dp0kasa_bridge.py"
pause
