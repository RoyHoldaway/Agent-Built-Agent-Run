#!/usr/bin/env bash
cd "$(dirname "$0")"
if [ ! -d .venv ]; then
  python3 -m venv .venv && source .venv/bin/activate && python -m pip install -r requirements.txt
else
  source .venv/bin/activate
fi
python jobhunt_agent.py
