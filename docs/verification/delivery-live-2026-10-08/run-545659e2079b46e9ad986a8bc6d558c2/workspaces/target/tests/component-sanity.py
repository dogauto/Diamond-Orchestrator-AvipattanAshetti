import sys
sys.path.insert(0,'.')
from src.duration import parse_duration
assert parse_duration('0m0s') in (0,1)
print('component sanity passed')
