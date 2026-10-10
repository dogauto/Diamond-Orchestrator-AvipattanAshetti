"""History management for the Diamond interactive shell."""
import readline
from pathlib import Path


HISTORY_FILE = Path.home() / ".diamond_history"


def load_history() -> None:
    try:
        if HISTORY_FILE.exists():
            readline.read_history_file(str(HISTORY_FILE))
    except (OSError, PermissionError):
        pass


def save_history() -> None:
    try:
        readline.write_history_file(str(HISTORY_FILE))
    except OSError:
        pass


def show_history(args: list[str]) -> None:
    try:
        length = int(args[0]) if args else 20
    except (ValueError, IndexError):
        length = 20
    count = readline.get_current_history_length()
    start = max(1, count - length + 1)
    for i in range(start, count + 1):
        print(f"  {i:4d}  {readline.get_history_item(i)}")