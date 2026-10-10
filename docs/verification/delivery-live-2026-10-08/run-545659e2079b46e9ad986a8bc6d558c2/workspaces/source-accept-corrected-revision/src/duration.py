import re

def parse_duration(value: str) -> int:
    if not isinstance(value, str):
        raise TypeError('must be a string')
    match = re.fullmatch(r'([0-9]+)m([0-9]+)s', value)
    if match is None:
        raise ValueError('invalid syntax')
    minute_text, second_text = match.groups()
    minutes = int(minute_text.lstrip('0') or '0')
    seconds = int(second_text.lstrip('0') or '0')
    if minutes > 1440 or seconds > 59:
        raise ValueError('out of range')
    return minutes * 60 + seconds
