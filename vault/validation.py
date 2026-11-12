import re

from django.core.exceptions import ValidationError
from vault.models import MAX_RELOCK_SECONDS, MAX_UNLOCK_DELAY_SECONDS


UNIT_SECONDS = {"seconds": 1, "minutes": 60, "hours": 3600, "days": 86400}
INTEGER_RE = re.compile(r"^[0-9]+$")


def parse_duration(value, unit, *, maximum, field_name):
    if isinstance(value, bool) or not isinstance(value, (str, int)):
        raise ValidationError(f"{field_name} must be a whole number.")
    text = str(value)
    if not INTEGER_RE.fullmatch(text):
        raise ValidationError(f"{field_name} must be a positive whole number.")
    if unit not in UNIT_SECONDS:
        raise ValidationError(f"{field_name} unit must be seconds, minutes, hours, or days.")
    amount = int(text)
    seconds = amount * UNIT_SECONDS[unit]
    if amount < 1 or seconds > maximum:
        raise ValidationError(f"{field_name} must be between 1 second and {maximum} seconds.")
    return seconds


def validate_entry_payload(data, *, editing=False):
    allowed = {"title", "password", "unlock_delay", "unlock_unit", "relock_policy", "relock_after", "relock_unit"}
    unknown = set(data) - allowed
    if unknown:
        raise ValidationError(f"Unsupported fields: {', '.join(sorted(unknown))}.")
    title = data.get("title")
    password = data.get("password")
    if not isinstance(title, str) or not title.strip() or len(title.strip()) > 200:
        raise ValidationError("Title is required and must be at most 200 characters.")
    if not isinstance(password, str) or not password or len(password) > 4096:
        raise ValidationError("Password is required and must be at most 4096 characters.")
    delay = parse_duration(data.get("unlock_delay"), data.get("unlock_unit"), maximum=MAX_UNLOCK_DELAY_SECONDS, field_name="Unlock delay")
    policy = data.get("relock_policy", "automatic")
    if policy not in {"automatic", "manual"}:
        raise ValidationError("Relock policy must be automatic or manual.")
    relock_after = None
    if policy == "automatic":
        relock_after = parse_duration(data.get("relock_after"), data.get("relock_unit"), maximum=MAX_RELOCK_SECONDS, field_name="Automatic relock window")
    return {"title": title.strip(), "password": password, "unlock_delay_seconds": delay, "relock_policy": policy, "relock_after_seconds": relock_after}

