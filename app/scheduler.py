import asyncio
import logging
from datetime import datetime, timedelta, timezone

from apscheduler.schedulers.asyncio import AsyncIOScheduler
from sqlalchemy import select

from app.database import async_session
from app.models.user import User
from app.models.vehicle import Vehicle
from app.models.pilot_refuel import PilotRefuel
from app.models.refuel_entry import RefuelEntry
from app.models.sync_log import SyncLog
from app.models.setting import Setting
from app.models.trip_summary import TripSummary
from app.services.pilot_service import PilotService
from app.services.refuel_utils import match_vehicle as _match_vehicle, parse_timestamp as _parse_timestamp, get_effective_thresholds as _get_effective_thresholds, calc_comparison as _calc_comparison, get_thresholds_batch

logger = logging.getLogger(__name__)

_scheduler: AsyncIOScheduler | None = None


def start_scheduler():
    global _scheduler
    if _scheduler is not None:
        return
    _scheduler = AsyncIOScheduler(timezone="Europe/Moscow")
    _scheduler.add_job(
        sync_all_companies, "cron", hour=3, minute=0,
        id="daily_sync", replace_existing=True, misfire_grace_time=3600,
    )
    _scheduler.add_job(
        refresh_all_company_statuses, "interval", minutes=5,
        id="status_refresh", replace_existing=True,
    )
    _scheduler.start()
    logger.info("Scheduler started: daily sync at 03:00 MSK, status refresh every 5 min")


async def stop_scheduler():
    global _scheduler
    if _scheduler:
        _scheduler.shutdown(wait=False)
        _scheduler = None


async def sync_all_companies():
    logger.info("Daily auto-sync started")
    async with async_session() as db:
        admins = (await db.execute(
            select(User).where(
                User.role == "company_admin",
                User.is_active == True,
                User.pilot_token.isnot(None),
            )
        )).scalars().all()

    results = []
    for admin in admins:
        try:
            r = await _sync_company(admin)
            results.append({"username": admin.username, **r})
        except Exception as e:
            logger.error(f"Sync failed for {admin.username}: {e}", exc_info=True)
            results.append({"username": admin.username, "status": "error", "error": str(e)[:200]})

    logger.info(f"Daily auto-sync completed: {len(results)} companies")
    return results


async def _sync_trip_summaries(db, pilot, token, node_id, admin, start_str, stop_str) -> int:
    ts_from = int(datetime.strptime(start_str, "%d.%m.%Y %H:%M").timestamp())
    ts_to = int(datetime.strptime(stop_str, "%d.%m.%Y %H:%M").timestamp())
    start_dt = datetime.strptime(start_str, "%d.%m.%Y %H:%M")

    trip_vehicles = await db.execute(
        select(Vehicle).where(
            Vehicle.is_active == True,
            Vehicle.client_account_id == admin.client_account_id,
            Vehicle.imei.isnot(None),
            Vehicle.pilot_agent_id.isnot(None),
        )
    )
    trip_vehicles = trip_vehicles.scalars().all()

    trip_count = 0
    sem = asyncio.Semaphore(10)

    async def _fetch_one(tv):
        async with sem:
            return tv, await pilot.get_trip_summary(token, node_id, tv.imei, tv.pilot_agent_id, ts_from, ts_to)

    gathered = await asyncio.gather(*[_fetch_one(tv) for tv in trip_vehicles], return_exceptions=True)

    for item in gathered:
        if isinstance(item, BaseException):
            logger.error(f"Trip summary sync failed: {item}")
            continue
        tv, result = item
        if result is None:
            continue

        existing = await db.execute(
            select(TripSummary).where(
                TripSummary.vehicle_id == tv.id,
                TripSummary.date == start_dt,
            )
        )
        existing_ts = existing.scalar_one_or_none()

        if existing_ts:
            existing_ts.duration_seconds = result["duration"]
            existing_ts.motion_seconds = result["motion_duration"]
            existing_ts.gps_km = result["gps_km"]
            existing_ts.can_km = result["can_km"]
            existing_ts.max_speed = result["maxspeed"]
            existing_ts.avg_speed = result["avgspeed"]
            existing_ts.parking_count = result["parking_count"]
            existing_ts.segment_count = result["segment_count"]
        else:
            db.add(TripSummary(
                vehicle_id=tv.id,
                date=start_dt,
                duration_seconds=result["duration"],
                motion_seconds=result["motion_duration"],
                gps_km=result["gps_km"],
                can_km=result["can_km"],
                max_speed=result["maxspeed"],
                avg_speed=result["avgspeed"],
                parking_count=result["parking_count"],
                segment_count=result["segment_count"],
            ))
        trip_count += 1

    return trip_count


async def _sync_company(admin: User) -> dict:
    async with async_session() as db:
        pilot = PilotService()

        msk = timezone(timedelta(hours=3))
        now = datetime.now(msk)
        start_str = (now - timedelta(days=2)).strftime("%d.%m.%Y 00:00")
        stop_str = now.strftime("%d.%m.%Y 23:59")

        vehicles = await db.execute(
            select(Vehicle).where(
                Vehicle.is_active == True,
                Vehicle.has_fuel_sensor == True,
                Vehicle.client_account_id == admin.client_account_id,
            )
        )
        vehicles = vehicles.scalars().all()
        if not vehicles:
            return {"status": "skipped", "reason": "no vehicles"}

        veh_ids = [v.pilot_agent_id for v in vehicles if v.pilot_agent_id]
        if not veh_ids:
            return {"status": "skipped", "reason": "no agent_ids"}

        token = admin.pilot_token
        node_id = admin.pilot_node_id or 0
        login_attempted = False

        async def _on_retry(e: Exception, attempt: int):
            nonlocal token, node_id, login_attempted
            err_msg = str(e)
            if "401" in err_msg or "Unauthorized" in err_msg:
                if not login_attempted and admin.pilot_password:
                    try:
                        result = await pilot.login(admin.username, admin.pilot_password)
                        new_token = result.get("token") or admin.pilot_token
                        new_node_id = result.get("node_id", 0) or admin.pilot_node_id
                        admin.pilot_token = new_token
                        admin.pilot_node_id = new_node_id
                        await db.commit()
                        token = new_token
                        node_id = new_node_id
                        login_attempted = True
                        return token, node_id
                    except Exception as login_err:
                        logger.error(f"Re-login failed for {admin.username}: {login_err}")
                elif not login_attempted:
                    logger.warning(f"No saved password for {admin.username} — re-login needed")
            return None

        all_events = await pilot.fetch_refuel_reports_batch(
            token, node_id, veh_ids, start_str, stop_str,
            on_retry=_on_retry,
        )

        total_events = len(all_events)
        new_count = 0
        updated_count = 0
        errors = []
        vehicle_updates = []
        thresh_cache = await get_thresholds_batch(db, [v.id for v in vehicles])
        event_log = []

        for ev in all_events:
            v = _match_vehicle(ev, vehicles)
            if not v:
                errors.append(ev.get("name", "?")[:30])
                event_log.append({"plate": "—", "event_date": "", "old_amount": None, "new_amount": ev.get("refuel_amount"), "check_value": None, "action": "unmatched", "name": ev.get("name", "?")})
                continue

            n_pct, w_pct, n_abs, w_abs, enable_abs = thresh_cache.get(v.id, (3.0, 10.0, 0.0, 0.0, False))

            ev_ts = _parse_timestamp(ev.get("ts"))
            if not ev_ts:
                event_log.append({"plate": v.plate_number or "—", "event_date": "", "old_amount": None, "new_amount": ev.get("refuel_amount"), "check_value": None, "action": "bad_ts", "name": ev.get("name", "?")})
                continue
            if ev_ts.tzinfo is not None:
                ev_ts = ev_ts.replace(tzinfo=None)

            amount = ev.get("refuel_amount")
            if not amount or amount <= 0:
                event_log.append({"plate": v.plate_number or "—", "event_date": ev_ts.strftime("%d.%m.%Y %H:%M"), "old_amount": None, "new_amount": ev.get("refuel_amount"), "check_value": None, "action": "bad_amount", "name": ev.get("name", "?")})
                continue

            ev_date_str = ev_ts.strftime("%d.%m.%Y %H:%M")
            plate = v.plate_number or v.name or "—"

            existing = await db.execute(
                select(PilotRefuel).where(
                    PilotRefuel.vehicle_id == v.id,
                    PilotRefuel.event_date >= ev_ts - timedelta(hours=1),
                    PilotRefuel.event_date <= ev_ts + timedelta(hours=1),
                )
            )
            existing_pr = existing.scalar_one_or_none()

            if existing_pr:
                old_amount = existing_pr.amount
                check_value = None

                existing_entry = await db.execute(
                    select(RefuelEntry).where(
                        RefuelEntry.pilot_refuel_id == existing_pr.id,
                        RefuelEntry.is_deleted == False,
                    )
                )
                existing_entry = existing_entry.scalar_one_or_none()
                if existing_entry:
                    check_value = existing_entry.actual_amount

                existing_pr.amount = amount
                existing_pr.event_date = ev_ts

                if existing_entry:
                    existing_entry.pilot_amount = amount
                    existing_entry.event_date = ev_ts
                    if existing_entry.actual_amount:
                        diff, err, status = _calc_comparison(amount, existing_entry.actual_amount, n_pct, w_pct, n_abs, w_abs, enable_abs)
                        existing_entry.difference = diff
                        existing_entry.error_percent = err
                        existing_entry.comparison_status = status
                    else:
                        existing_entry.difference = None
                        existing_entry.error_percent = None
                        existing_entry.comparison_status = "check_missing"
                    updated_count += 1

                action = "identical" if abs((old_amount or 0) - amount) < 0.001 else "conflict" if check_value else "updated"
                vehicle_updates.append({
                    "plate": plate,
                    "date": ev_date_str,
                    "amount": f"{amount:.1f}",
                    "action": action,
                })
                event_log.append({
                    "plate": plate, "event_date": ev_date_str,
                    "old_amount": old_amount, "new_amount": amount,
                    "check_value": check_value, "action": action, "name": ev.get("name", "?"),
                })
            else:
                pr = PilotRefuel(
                    vehicle_id=v.id,
                    event_date=ev_ts,
                    amount=amount,
                    start_level=ev.get("start_level"),
                    end_level=ev.get("end_level"),
                    address=(ev.get("address") or "")[:500],
                    lat=ev.get("lat"),
                    lon=ev.get("lon"),
                    raw_data=ev,
                )
                db.add(pr)
                await db.flush()

                entry = RefuelEntry(
                    vehicle_id=v.id,
                    pilot_refuel_id=pr.id,
                    event_date=ev_ts,
                    pilot_amount=amount,
                    source="pilot_sync",
                )
                db.add(entry)
                new_count += 1
                vehicle_updates.append({
                    "plate": plate,
                    "date": ev_date_str,
                    "amount": f"{amount:.1f}",
                    "action": "+",
                })
                event_log.append({
                    "plate": plate, "event_date": ev_date_str,
                    "old_amount": None, "new_amount": amount,
                    "check_value": None, "action": "new", "name": ev.get("name", "?"),
                })

        trip_count = await _sync_trip_summaries(db, pilot, token, node_id, admin, start_str, stop_str)

        details = f"new={new_count}, updated={updated_count}, pilot_events={total_events}"
        if errors:
            details += f"; unmatched({len(errors)}): {', '.join(errors[:5])}"
        if trip_count:
            details += f"; trip_summaries={trip_count}"

        log = SyncLog(
            sync_type="auto_refuels",
            status="completed" if not errors else "partial",
            records_affected=new_count + updated_count,
            details=details,
            details_json={"vehicle_updates": vehicle_updates, "errors": errors[:20], "event_log": event_log},
            created_by=admin.username,
        )
        db.add(log)
        await db.commit()

        return {
            "status": "completed" if not errors else "partial",
            "new": new_count,
            "updated": updated_count,
            "errors": errors,
            "total_events": total_events,
            "vehicle_updates": vehicle_updates,
        }


async def refresh_all_company_statuses():
    """Фоновый пуллинг — обновляет кэш статусов для всех компаний."""
    from app.api.vehicles import refresh_company_statuses

    async with async_session() as db:
        result = await db.execute(
            select(Vehicle.client_account_id)
            .where(Vehicle.is_active == True, Vehicle.has_fuel_sensor == True)
            .distinct()
        )
        company_ids = [r[0] for r in result if r[0] is not None]

    total = 0
    for ca_id in company_ids:
        count = await refresh_company_statuses(ca_id)
        total += count
    logger.info("Background status refresh: %d companies, %d vehicles", len(company_ids), total)


async def trigger_sync_all(admin_user_ids: list[int] | None = None) -> list[dict]:
    if admin_user_ids:
        async with async_session() as db:
            admins = (await db.execute(
                select(User).where(
                    User.id.in_(admin_user_ids),
                    User.role == "company_admin",
                    User.is_active == True,
                )
            )).scalars().all()
        results = []
        for admin in admins:
            try:
                r = await _sync_company(admin)
                results.append({"username": admin.username, **r})
            except Exception as e:
                logger.error(f"Sync failed for {admin.username}: {e}", exc_info=True)
                results.append({"username": admin.username, "status": "error", "error": str(e)[:200]})
        return results
    return await sync_all_companies()
