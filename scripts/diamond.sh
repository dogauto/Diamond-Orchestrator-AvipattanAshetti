#!/bin/sh
set -eu
script_dir=$(CDPATH= cd -- "$(dirname -- "$0")" && pwd -P)
project_root=$(CDPATH= cd -- "$script_dir/.." && pwd -P)
python=${DIAMOND_PYTHON:-python3}
if ! command -v "$python" >/dev/null 2>&1; then
    printf '%s\n' "diamond requires Python >=3.11; executable not found: $python" >&2
    exit 2
fi
if ! "$python" -B -c 'import sys; sys.exit(0 if sys.version_info >= (3, 11) else 2)'; then
    printf '%s\n' "diamond requires Python >=3.11; select it with DIAMOND_PYTHON" >&2
    exit 2
fi
DIAMOND_PROJECT_ROOT=$project_root
PYTHONPATH="$project_root/src${PYTHONPATH:+:$PYTHONPATH}"
PYTHONDONTWRITEBYTECODE=1
export DIAMOND_PROJECT_ROOT PYTHONPATH PYTHONDONTWRITEBYTECODE
exec "$python" -B -m diamond "$@"