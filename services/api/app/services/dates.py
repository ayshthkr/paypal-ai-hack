"""Business calendar dates are merchant-local; audit timestamps remain UTC."""
from datetime import date, datetime, timezone
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError
from app.config import get_settings


def business_date(value: date | datetime) -> date:
    if not isinstance(value, datetime):
        return value
    if value.tzinfo is None:
        value = value.replace(tzinfo=timezone.utc)
    try:
        zone = ZoneInfo(get_settings().merchant_timezone)
    except (ZoneInfoNotFoundError, ValueError):
        zone = timezone.utc
    return value.astimezone(zone).date()


def event_date(value) -> date | None:
    if isinstance(value, (date, datetime)):
        return business_date(value)
    if not isinstance(value, str):
        return None
    try:
        return business_date(datetime.fromisoformat(value.replace("Z", "+00:00"))) if "T" in value else date.fromisoformat(value)
    except ValueError:
        return None
