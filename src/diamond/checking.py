import ast
import sys
import unittest
from pathlib import Path


def main() -> int:
    root = Path(__file__).resolve().parents[2]
    suite = unittest.defaultTestLoader.discover(str(root / "tests"), pattern="test_*.py")
    count = suite.countTestCases()
    if count == 0:
        print("FAIL: no tests discovered", file=sys.stderr)
        return 1
    result = unittest.TextTestRunner(verbosity=2).run(suite)
    if not result.wasSuccessful():
        return 1
    sources = sorted((root / "src").rglob("*.py")) + sorted((root / "tests").rglob("*.py")) + sorted((root / "scripts").glob("*.py"))
    for source in sources:
        contents = source.read_text(encoding="utf-8")
        ast.parse(contents, filename=str(source))
        compile(contents, str(source), "exec", dont_inherit=True)
        if len(contents.splitlines()) > 500:
            print(f"FAIL: {source} exceeds 500 lines", file=sys.stderr)
            return 1
    print(f"PASS: {count} tests; {len(sources)} Python files compiled in memory; no bytecode files written")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
