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
from app.services.pilot_service import PilotService
from app.api.refuels import _match_vehicle, _parse_timestamp, _get_effective_thresholds, _calc_comparison

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
    _scheduler.start()
    logger.info("Scheduler started: daily sync at 03:00 MSK")


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

        BATCH_SIZE = 20
        token = admin.pilot_token
        node_id = admin.pilot_node_id or 0
        all_events = []
        login_attempted = False

        for i in range(0, len(veh_ids), BATCH_SIZE):
            batch = veh_ids[i:i + BATCH_SIZE]
            for attempt in range(3):
                try:
                    batch_events = await pilot.get_refuel_report(token, node_id, batch, start_str, stop_str)
                    break
                except Exception as e:
                    err_msg = str(e)
                    is_auth_err = "401" in err_msg or "Unauthorized" in err_msg
                    if is_auth_err and not login_attempted:
                        if admin.pilot_password:
                            try:
                                result = await pilot.login(admin.username, admin.pilot_password)
                                token = result.get("token") or admin.pilot_token
                                node_id = result.get("node_id", 0) or admin.pilot_node_id
                                admin.pilot_token = token
                                admin.pilot_node_id = node_id
                                await db.commit()
                                login_attempted = True
                                continue
                            except Exception as login_err:
                                logger.error(f"Re-login failed for {admin.username}: {login_err}")
                        else:
                            logger.warning(f"No saved password for {admin.username} — re-login needed")
                    if attempt == 2 or is_auth_err:
                        raise
                    await asyncio.sleep(1)
            all_events.extend(batch_events)
            await asyncio.sleep(0.5)

        total_events = len(all_events)
        new_count = 0
        updated_count = 0
        errors = []
        vehicle_updates = []
        thresh_cache = {}

        for ev in all_events:
            v = _match_vehicle(ev, vehicles)
            if not v:
                errors.append(ev.get("name", "?")[:30])
                continue

            if v.id not in thresh_cache:
                thresh_cache[v.id] = await _get_effective_thresholds(db, v.id)
            n_pct, w_pct, n_abs, w_abs, enable_abs = thresh_cache[v.id]

            ev_ts = _parse_timestamp(ev.get("ts"))
            if not ev_ts:
                continue
            if ev_ts.tzinfo is not None:
                ev_ts = ev_ts.replace(tzinfo=None)

            amount = ev.get("refuel_amount")
            if not amount or amount <= 0:
                continue

            existing = await db.execute(
                select(PilotRefuel).where(
                    PilotRefuel.vehicle_id == v.id,
                    PilotRefuel.event_date >= ev_ts - timedelta(hours=1),
                    PilotRefuel.event_date <= ev_ts + timedelta(hours=1),
                )
            )
            existing_pr = existing.scalar_one_or_none()

            plate = v.plate_number or v.name or "—"

            if existing_pr:
                existing_pr.amount = amount
                existing_pr.event_date = ev_ts

                existing_entry = await db.execute(
                    select(RefuelEntry).where(
                        RefuelEntry.pilot_refuel_id == existing_pr.id,
                        RefuelEntry.is_deleted == False,
                    )
                )
                existing_entry = existing_entry.scalar_one_or_none()

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
                        existing_entry.comparison_status = "pilot_missing" if existing_entry.actual_amount else None
                    updated_count += 1
                    vehicle_updates.append({
                        "plate": plate,
                        "date": ev_ts.strftime("%d.%m.%Y %H:%M"),
                        "amount": f"{amount:.1f}",
                        "action": "обновлено",
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
                    "date": ev_ts.strftime("%d.%m.%Y %H:%M"),
                    "amount": f"{amount:.1f}",
                    "action": "+",
                })

        details = f"new={new_count}, updated={updated_count}, pilot_events={total_events}"
        if errors:
            details += f"; unmatched({len(errors)}): {', '.join(errors[:5])}"

        log = SyncLog(
            sync_type="auto_refuels",
            status="completed" if not errors else "partial",
            records_affected=new_count + updated_count,
            details=details,
            details_json={"vehicle_updates": vehicle_updates, "errors": errors[:20]},
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
