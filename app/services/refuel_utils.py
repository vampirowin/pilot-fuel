from datetime import datetime, timedelta, timezone
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from app.models.vehicle import Vehicle
from app.models.refuel_entry import RefuelEntry
from app.models.pilot_refuel import PilotRefuel
from app.models.user import User
from app.models.setting import Setting


STATUS_MAP = {
    "normal": "status-normal",
    "small_deviation": "status-small-deviation",
    "unacceptable": "status-unacceptable",
    "pilot_missing": "status-pilot-missing",
    "check_missing": "status-check-missing",
    "false_reading": "status-false-reading",
}

STATUS_LABELS = {
    "normal": "Норма",
    "small_deviation": "Расхождение",
    "unacceptable": "Недопустимо",
    "pilot_missing": "Нет в Pilot",
    "check_missing": "Нет чека",
    "false_reading": "Ложная",
}


async def resolve_pilot_credentials(user, db) -> tuple[str | None, int]:
    token = user.pilot_token
    node_id = user.pilot_node_id or 0
    if token:
        return token, node_id
    if user.role == "superadmin":
        admin = (await db.execute(
            select(User).where(User.role == "company_admin", User.pilot_token.isnot(None))
        )).scalar_one_or_none()
        if admin and admin.pilot_token:
            return admin.pilot_token, admin.pilot_node_id or 0
    return None, 0


def calc_comparison(pilot_amount: float | None, actual_amount: float | None, n_pct: float, w_pct: float, n_abs: float = 0.0, w_abs: float = 0.0, enable_abs: bool = False) -> tuple:
    if pilot_amount is not None and actual_amount is not None and pilot_amount > 0:
        diff = pilot_amount - actual_amount
        err = diff / pilot_amount * 100
        abs_diff = abs(diff)
        abs_err = abs(err)

        if enable_abs and abs_diff <= n_abs:
            status = "normal"
        elif enable_abs and abs_diff <= w_abs:
            status = "small_deviation"
        elif abs_err <= n_pct:
            status = "normal"
        elif abs_err <= w_pct:
            status = "small_deviation"
        else:
            status = "unacceptable"
    else:
        diff = None
        err = None
        if actual_amount is not None and (pilot_amount is None or pilot_amount == 0):
            status = "pilot_missing"
        elif pilot_amount is not None and pilot_amount > 0 and actual_amount is None:
            status = "check_missing"
        else:
            status = None
    return diff, err, status


async def get_effective_thresholds(db: AsyncSession, vehicle_id: int | None = None) -> tuple[float, float, float, float, bool]:
    n_pct, w_pct = 3.0, 10.0
    n_abs, w_abs = 0.0, 0.0
    enable_abs = False

    rows = (await db.execute(select(Setting).where(Setting.key.in_(["normal_threshold", "warning_threshold"])))).scalars().all()
    for s in rows:
        if s.key == "normal_threshold":
            n_pct = float(s.value)
        elif s.key == "warning_threshold":
            w_pct = float(s.value)

    if vehicle_id is not None:
        v = (await db.execute(select(Vehicle).where(Vehicle.id == vehicle_id))).scalar_one_or_none()
        if v:
            if v.normal_threshold_pct is not None:
                n_pct = v.normal_threshold_pct
            if v.warning_threshold_pct is not None:
                w_pct = v.warning_threshold_pct
            if v.enable_abs_threshold:
                enable_abs = True
                if v.normal_threshold_abs is not None:
                    n_abs = v.normal_threshold_abs
                if v.warning_threshold_abs is not None:
                    w_abs = v.warning_threshold_abs

    return n_pct, w_pct, n_abs, w_abs, enable_abs


def parse_timestamp(val) -> datetime | None:
    if not val:
        return None
    try:
        ts = int(val)
    except (ValueError, TypeError):
        return None
    if ts > 1e12:
        ts //= 1000
    return datetime.fromtimestamp(ts, tz=timezone.utc)


def match_vehicle(event: dict, vehicles: list) -> Vehicle | None:
    ev_name_lower = (event.get("name") or "").strip().lower()
    ev_tokens = {t.strip() for t in ev_name_lower.replace("-", " ").replace("(", " ").replace(")", " ").replace("_", " ").split() if t.strip()}
    for db_v in vehicles:
        plate = (db_v.plate_number or "").strip().lower()
        if not plate:
            continue
        if plate in ev_tokens:
            return db_v
        plate_ns = plate.replace(" ", "")
        if plate_ns in ev_tokens:
            return db_v
        if len(plate) > 2 and plate in ev_name_lower:
            return db_v
        if len(plate_ns) > 2 and plate_ns in ev_name_lower:
            return db_v
    return None


def group_by_vehicle(entries: list) -> list:
    seen = {}
    for e in entries:
        seen.setdefault(e.vehicle_id, []).append(e)
    return sorted(seen.items())
