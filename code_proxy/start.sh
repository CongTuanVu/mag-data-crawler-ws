#!/usr/bin/env bash
# Khởi động proxy Claude CLI. Cần Python 3.9+ và Claude Code CLI đã đăng nhập.
set -euo pipefail

HERE="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PYTHON="${PYTHON:-python3}"

if ! "$PYTHON" -c 'import sys; sys.exit(0 if sys.version_info >= (3, 9) else 1)'; then
    echo "Cần Python 3.9+; đặt PYTHON=/đường/dẫn/python3 để chỉ định." >&2
    exit 1
fi

exec "$PYTHON" "$HERE/proxy.py" "$@"
