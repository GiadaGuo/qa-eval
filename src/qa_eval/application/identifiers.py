import secrets
import time
from uuid import UUID


def uuid7() -> UUID:
    """Generate an RFC 9562 UUIDv7 on Python versions without uuid.uuid7."""

    unix_ms = time.time_ns() // 1_000_000
    if unix_ms >= 1 << 48:
        raise OverflowError("UUIDv7 timestamp exceeds 48 bits")
    value = unix_ms << 80
    value |= 0x7 << 76
    value |= secrets.randbits(12) << 64
    value |= 0b10 << 62
    value |= secrets.randbits(62)
    return UUID(int=value)
