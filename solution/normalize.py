"""Input normalization rules from the Assignment A contract."""

from __future__ import annotations

import re
from datetime import datetime, timedelta, timezone
from decimal import Decimal, InvalidOperation, ROUND_HALF_UP
from unicodedata import decimal as unicode_decimal

IST = timezone(timedelta(hours=5, minutes=30))
UTC = timezone.utc


def header(value: str) -> str:
    return re.sub(r"[_\s]+", " ", value.strip().lower())


def phone(value: str) -> str | None:
    digits = "".join(str(unicode_decimal(char)) for char in value if char.isdecimal())
    if len(digits) == 12 and digits.startswith("91"):
        digits = digits[2:]
    elif len(digits) == 11 and digits.startswith("0"):
        digits = digits[1:]
    if len(digits) != 10 or digits[0] not in "6789":
        return None
    return "+91" + digits


def date(value: str) -> str | None:
    formats = ((r"[0-9]{4}-[0-9]{2}-[0-9]{2}", "%Y-%m-%d"),
               (r"[0-9]{2}/[0-9]{2}/[0-9]{4}", "%d/%m/%Y"),
               (r"[0-9]{2}-[0-9]{2}-[0-9]{4}", "%d-%m-%Y"))
    for pattern, fmt in formats:
        if not re.fullmatch(pattern, value):
            continue
        try:
            return datetime.strptime(value, fmt).date().isoformat()
        except ValueError:
            pass
    named = re.fullmatch(r"([0-9]{2})-([A-Za-z]{3})-([0-9]{4})", value)
    if named:
        months = ("jan", "feb", "mar", "apr", "may", "jun",
                  "jul", "aug", "sep", "oct", "nov", "dec")
        month = named.group(2).lower()
        if month in months:
            try:
                return datetime(int(named.group(3)), months.index(month) + 1,
                                int(named.group(1))).date().isoformat()
            except ValueError:
                pass
    return None


_MONEY = re.compile(r"^(?:₹|Rs\.?|INR)?\s*((?:\d{1,3}(?:,\d{3})+|\d+)(?:\.\d+)?)$", re.I)


def decimal(value: str) -> str | None:
    match = _MONEY.fullmatch(value)
    if not match:
        return None
    try:
        number = Decimal(match.group(1).replace(",", ""))
        return str(number.quantize(Decimal("0.01"), rounding=ROUND_HALF_UP))
    except InvalidOperation:
        return None


def timestamp(value: str) -> datetime:
    parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=IST)
    return parsed.astimezone(UTC)


def normalize_field(value: str, field: dict) -> str | None:
    if not value:
        return None
    kind = field["type"]
    if kind == "string":
        return value
    if kind == "phone":
        return phone(value)
    if kind == "date":
        return date(value)
    if kind == "decimal":
        return decimal(value)
    if kind == "enum":
        lowered = value.lower()
        return lowered if lowered in {v.lower() for v in field["values"]} else None
    raise ValueError(f"Unsupported schema field type: {kind}")
