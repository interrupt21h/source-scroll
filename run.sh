#!/usr/bin/env bash
set -euo pipefail
program_dir="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)"
exec "$program_dir/.venv/bin/python" "$program_dir/source_scroll.py" "$@"
