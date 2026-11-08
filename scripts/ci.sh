#!/bin/sh
# Local CI: every check this repo has. Run by .githooks/pre-push.
set -eu
cd "$(dirname "$0")/.."
if python3 -c "import pytest, numpy, serial, cryptography" 2>/dev/null; then
  PY="python3"
elif command -v uv >/dev/null 2>&1; then
  PY="uv run -q --no-project --with pytest --with numpy --with pyserial --with cryptography python"
else
  echo "ci: need pytest, numpy, pyserial and cryptography (pip install pytest numpy pyserial cryptography) or uv" >&2; exit 1
fi
echo "ci: tests"
$PY -m pytest -q
echo "ci: console module imports"
$PY -c "import rescue.console"
echo "ci: ok"
