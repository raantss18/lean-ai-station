#!/usr/bin/env bash
# One-command test suite. Fast tests by default; --all adds slow end-to-end tests (model + Lean + GPU).
set -e
cd "$(dirname "$0")"
if [ "${1:-}" = "--all" ]; then
  exec .venv/bin/python -m pytest -v -p no:cacheprovider tests/
else
  exec .venv/bin/python -m pytest -v -p no:cacheprovider -m "not slow" tests/
fi
