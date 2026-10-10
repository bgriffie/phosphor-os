#!/bin/sh
# Phosphor OS lights relay (Mac / Raspberry Pi): ./start-lights.sh
cd "$(dirname "$0")"
[ -d .venv ] || python3 -m venv .venv
.venv/bin/pip install --quiet --upgrade python-kasa
exec .venv/bin/python kasa_bridge.py "$@"
