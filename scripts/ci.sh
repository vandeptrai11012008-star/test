#!/bin/sh
# Local CI: every check this repo has. Run by .githooks/pre-push.
set -eu
cd "$(dirname "$0")/.."
if python3 -c "import pytest, numpy, serial" 2>/dev/null; then
  PY="python3"
elif command -v uv >/dev/null 2>&1; then
  PY="uv run -q --no-project --with pytest --with numpy --with pyserial python"
else
  echo "ci: need pytest, numpy and pyserial (pip install pytest numpy pyserial) or uv" >&2; exit 1
fi
echo "ci: tests"
$PY -m pytest -q
echo "ci: console module imports"
$PY -c "import rescue.console"
echo "ci: ok"
