#!/bin/sh
set -eu

script_dir=$(CDPATH= cd -- "$(dirname -- "$0")" && pwd -P)
project_root=$(CDPATH= cd -- "$script_dir/.." && pwd -P)
mac_package="$project_root/examples/DiamondMac"

# Resolve the Python interpreter the same way the project does elsewhere.
python=${DIAMOND_PYTHON:-python3}
if ! command -v "$python" >/dev/null 2>&1; then
    printf '%s\n' "Python >=3.11 is required; set DIAMOND_PYTHON or install python3." >&2
    exit 2
fi
if ! "$python" -c 'import sys; sys.exit(0 if sys.version_info >= (3,11) else 2)' 2>/dev/null; then
    printf '%s\n' "Python >=3.11 is required; set DIAMOND_PYTHON to a 3.11+ interpreter." >&2
    exit 2
fi
python=$(command -v "$python")

usage() {
    cat <<'EOF'
Flawless Diamond local launcher

Usage:
  ./scripts/local.sh              Check the controller and open the macOS app
  ./scripts/local.sh app          Build and open the macOS app
  ./scripts/local.sh status       Show local controller status
  ./scripts/local.sh doctor       Check installed provider CLIs
  ./scripts/local.sh demo         Create and run a disposable workflow demo
  ./scripts/local.sh music-player Build and open the local music player
  ./scripts/local.sh mcp          Run the controller as an MCP stdio server
  ./scripts/local.sh check        Run the complete validation suite
  ./scripts/local.sh help         Show this help
EOF
}

status() {
    "$script_dir/diamond.sh" --json project status
}

app() {
    if ! command -v swift >/dev/null 2>&1; then
        printf '%s\n' "Swift is required to launch the macOS app." >&2
        exit 2
    fi
    "$script_dir/diamond.sh" validate-config >/dev/null
    printf '%s\n' "Building the local Diamond console..."
    (cd "$mac_package" && swift build >/dev/null)
    binary=$(find "$mac_package/.build" -type f -path '*/debug/DiamondMac' -perm -111 -print -quit)
    if [ -z "$binary" ]; then
        printf '%s\n' "The DiamondMac executable was not produced." >&2
        exit 1
    fi
    open -n "$binary" >/dev/null 2>&1
    printf '%s\n' "Diamond console opened in its own macOS window. This terminal is ready for other commands."
}

command_name=${1:-start}
case "$command_name" in
    start)
        status
        app
        ;;
    app)
        app
        ;;
    status)
        status
        ;;
    doctor)
        "$script_dir/diamond.sh" doctor
        ;;
    demo)
        demo_root="${TMPDIR:-/tmp}/flawless-diamond-demo"
        demo_state="$demo_root-state"
        # Idempotent: clean up any previous demo run so re-running works.
        rm -rf "$demo_root" "$demo_state"
        mkdir -p "$demo_root"
        cat > "$demo_root/greeting.py" <<'EOF'
def greeting() -> str:
    return "hello from Diamond"
EOF
        cat > "$demo_root/check_greeting.py" <<'EOF'
from greeting import greeting

assert greeting() == "hello from Diamond"
print(greeting())
EOF
        command_id="demo-workflow-$(date +%s)"
        test_json=$(printf '%s' "{\"name\":\"greeting check\",\"argv\":[\"$python\",\"check_greeting.py\"],\"timeout_seconds\":10,\"output_limit\":4096}")
        created=$("$script_dir/diamond.sh" --state-dir "$demo_state" --json workflow create \
            --objective "Improve the greeting utility" \
            --workspace "$demo_root" \
            --owned-path greeting.py \
            --test "$test_json" \
            --command-id "$command_id")
        workflow_id=$(printf '%s' "$created" | "$python" -c 'import json,sys; print(json.load(sys.stdin)["result"]["workflow"]["spec"]["workflow_id"])')
        printf '%s\n' "Demo workspace: $demo_root"
        printf '%s\n' "Workflow: $workflow_id"
        printf '%s\n' "Running one bounded provider step..."
        "$script_dir/diamond.sh" --state-dir "$demo_state" --json workflow run "$workflow_id" --expected-revision 1
        printf '%s\n' "Inspect the result with:"
        printf '  %s\n' "\"$script_dir/diamond.sh\" --state-dir \"$demo_state\" workflow status $workflow_id"
        ;;
    mcp)
        exec "$script_dir/diamond.sh" mcp --stdio
        ;;
    music-player)
        music_package="$project_root/examples/MusicPlayer"
        if ! command -v swift >/dev/null 2>&1; then
            printf '%s\n' "Swift is required to build the music player." >&2
            exit 2
        fi
        printf '%s\n' "Building the local music player..."
        (cd "$music_package" && swift build >/dev/null)
        binary=$(find "$music_package/.build" -type f -path '*/debug/MusicPlayer' -perm -111 -print -quit)
        if [ -z "$binary" ]; then
            printf '%s\n' "The MusicPlayer executable was not produced." >&2
            exit 1
        fi
        open -n "$binary" >/dev/null 2>&1
        printf '%s\n' "Music Player opened. Import audio files to begin."
        ;;
    check)
        "$script_dir/check.sh"
        ;;
    help|-h|--help)
        usage
        ;;
    *)
        printf 'Unknown local command: %s\n\n' "$command_name" >&2
        usage >&2
        exit 2
        ;;
esac
