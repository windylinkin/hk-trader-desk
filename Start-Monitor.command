#!/bin/zsh
set -eu
cd -- "$(dirname -- "$0")"
if [[ ! -x .venv/bin/python ]]; then
  echo "Run: python3 -m venv .venv"
  echo "Then: .venv/bin/python -m pip install -r requirements.txt"
  exit 1
fi
exec .venv/bin/python launch.py
