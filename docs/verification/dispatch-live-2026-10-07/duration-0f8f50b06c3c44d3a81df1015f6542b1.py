import re


def parse_duration(value: str) -> int:
    if not isinstance(value, str):
        raise TypeError("value must be a string")
    m = re.fullmatch(r'([0-9]+)m([0-9]+)s', value)
    if m is None:
        raise ValueError("invalid duration syntax")
    minutes = int(m.group(1))
    seconds = int(m.group(2))
    if not 0 <= minutes <= 1440:
        raise ValueError("minutes out of range")
    if not 0 <= seconds <= 59:
        raise ValueError("seconds out of range")
    return minutes * 60 + seconds