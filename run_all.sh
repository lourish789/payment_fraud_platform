#!/usr/bin/env bash
# Full reproduction of the evaluation (after `payguard ingest`).
set -euo pipefail
export PYTHONPATH=src
PY=.venv/Scripts/python
[ "${SKIP_BACKFILL:-0}" = 1 ] || $PY -m payguard backfill
$PY -m payguard train
$PY -m payguard replay
$PY -m payguard parity
$PY -m payguard agent-eval --n 200 --provider heuristic
$PY -m payguard drift-demo
$PY -m payguard crypto-ingest
$PY -m payguard crypto-train
$PY -m payguard crypto-intel
echo CHAIN_DONE
