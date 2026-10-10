import re

def parse_duration(value: str) -> int:
    if not isinstance(value, str):
        raise TypeError("value must be a string")
    match = re.fullmatch(r"([0-9]+)m([0-9]+)s", value)
    if match is None:
        raise ValueError("invalid duration syntax")
    minutes = int(match.group(1))
    seconds = int(match.group(2))
    if minutes < 0 or minutes > 1440:
        raise ValueError("minutes out of range")
    if seconds < 0 or seconds > 59:
        raise ValueError("seconds out of range")
    return minutes * 60 + seconds
