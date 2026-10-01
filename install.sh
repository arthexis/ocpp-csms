#!/bin/sh
set -eu

ROOT=$(CDPATH= cd -- "$(dirname -- "$0")" && pwd)
PREFIX=${OCPP_CSMS_PREFIX:-"$HOME/.local/share/ocpp-csms"}
BIN_DIR=${OCPP_CSMS_BIN_DIR:-"$HOME/.local/bin"}
VENV="$PREFIX/venv"

python3 -m venv "$VENV"
"$VENV/bin/python" -m pip install --upgrade pip
"$VENV/bin/python" -m pip install "$ROOT"

mkdir -p "$BIN_DIR"
ln -sf "$VENV/bin/ocpp-csms" "$BIN_DIR/ocpp-csms"

printf 'Installed OCPP CSMS for %s\n' "$USER"
printf 'Command: %s/ocpp-csms\n' "$BIN_DIR"
printf 'Data:    %s/ocpp-csms-data\n' "$HOME"

case ":$PATH:" in
  *":$BIN_DIR:"*) ;;
  *) printf 'Add %s to PATH to run ocpp-csms directly.\n' "$BIN_DIR" ;;
esac
