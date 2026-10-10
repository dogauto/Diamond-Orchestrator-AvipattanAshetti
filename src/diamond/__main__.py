import sys

if sys.version_info < (3, 11):
    print("diamond requires Python >=3.11", file=sys.stderr)
    raise SystemExit(2)

from .cli import main

raise SystemExit(main())

# Support for: python -m diamond shell
import sys
if len(sys.argv) > 1 and sys.argv[1] == "shell":
    from .shell import main as _shell_main
    raise SystemExit(_shell_main())
