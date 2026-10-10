import sys
sys.path.insert(0,'.')
import json
from src.duration import parse_duration
cases = [('0m0s',0),('1m2s',62),('0m59s',59),('1440m59s',86459),('001m02s',62)]
passed = 0
for value, expected in cases:
    assert parse_duration(value) == expected, repr(value)
    passed += 1
for value in ['', '1m60s', '1441m0s', '-1m2s', '1m-2s', '1m2s\n', ' 1m2s',
              '1m2s ', '1m2sother', '1.0m2s', '1m', '１２m2s', '1m２s']:
    try: parse_duration(value)
    except ValueError: passed += 1
    else: raise AssertionError(repr(value))
for value in [None, True, 62, ['1m2s']]:
    try: parse_duration(value)
    except TypeError: passed += 1
    else: raise AssertionError(repr(value))
print(json.dumps({'passed': passed, 'status': 'PASS'}, sort_keys=True))
