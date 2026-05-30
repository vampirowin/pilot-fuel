import zoneinfo
from datetime import datetime, timezone
from app.config import get_settings


def get_user_timezone(user=None) -> zoneinfo.ZoneInfo:
    """Return the user's timezone or server default."""
    if user and user.timezone:
        return zoneinfo.ZoneInfo(user.timezone)
    return zoneinfo.ZoneInfo(get_settings().timezone)


def utc_to_tz(dt: datetime, tz: zoneinfo.ZoneInfo) -> datetime:
    """Convert a naive UTC datetime to a timezone-aware datetime in the given timezone."""
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    return dt.astimezone(tz)


def format_dt(dt: datetime, fmt: str, user=None) -> str:
    """Format a naive UTC datetime to a string in the user's timezone."""
    tz = get_user_timezone(user)
    aware = utc_to_tz(dt, tz)
    return aware.strftime(fmt)


def utcnow() -> datetime:
    """Return current UTC time as a naive datetime (for DB storage)."""
    return datetime.now(timezone.utc).replace(tzinfo=None)


# Russian timezones with labels
RU_ZONES = [
    ("Europe/Kaliningrad", "MSK−1 — Калининград (UTC+2)"),
    ("Europe/Moscow", "MSK — Москва (UTC+3)"),
    ("Europe/Volgograd", "MSK+1 — Волгоград (UTC+4)"),
    ("Europe/Samara", "MSK+1 — Самара (UTC+4)"),
    ("Asia/Yekaterinburg", "MSK+2 — Екатеринбург (UTC+5)"),
    ("Asia/Omsk", "MSK+3 — Омск (UTC+6)"),
    ("Asia/Novosibirsk", "MSK+4 — Новосибирск (UTC+7)"),
    ("Asia/Krasnoyarsk", "MSK+4 — Красноярск (UTC+7)"),
    ("Asia/Irkutsk", "MSK+5 — Иркутск (UTC+8)"),
    ("Asia/Yakutsk", "MSK+6 — Якутск (UTC+9)"),
    ("Asia/Vladivostok", "MSK+7 — Владивосток (UTC+10)"),
    ("Asia/Magadan", "MSK+8 — Магадан (UTC+11)"),
    ("Asia/Kamchatka", "MSK+9 — Камчатка (UTC+12)"),
]

# Common non-Russian zones
OTHER_ZONES = [
    ("Etc/UTC", "UTC"),
    ("Europe/London", "Лондон (UTC+0/+1)"),
    ("Europe/Berlin", "Берлин (UTC+1/+2)"),
    ("Europe/Helsinki", "Хельсинки (UTC+2/+3)"),
    ("Asia/Tbilisi", "Тбилиси (UTC+4)"),
    ("Asia/Baku", "Баку (UTC+4)"),
    ("Asia/Yerevan", "Ереван (UTC+4)"),
    ("Asia/Almaty", "Алматы (UTC+5)"),
    ("Asia/Tashkent", "Ташкент (UTC+5)"),
    ("Asia/Bishkek", "Бишкек (UTC+6)"),
    ("Asia/Dushanbe", "Душанбе (UTC+5)"),
    ("Asia/Ashgabat", "Ашхабад (UTC+5)"),
    ("Asia/Dubai", "Дубай (UTC+4)"),
    ("Asia/Bangkok", "Бангкок (UTC+7)"),
    ("Asia/Shanghai", "Пекин (UTC+8)"),
]


def get_timezone_choices():
    """Return (ru_zones, other_zones) for profile template."""
    return RU_ZONES, OTHER_ZONES
