@echo off
rem Phosphor OS lights relay: double-click to start. Keep this window open.
cd /d "%~dp0"
title Phosphor OS lights relay
py -3 --version >nul 2>&1 && (set PY=py -3) || (set PY=python)
%PY% -m pip install --quiet --upgrade python-kasa
%PY% kasa_bridge.py
pause
