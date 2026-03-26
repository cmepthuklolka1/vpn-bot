import logging
from datetime import datetime
from services.traffic_monitor import check_and_apply_limits, monthly_reset
from handlers.status import auto_update_status
from database import db

logger = logging.getLogger(__name__)


async def traffic_check_job(context):
    """Periodic job: check traffic and apply speed limits."""
    api = context.bot_data["api"]
    config = context.bot_data["config"]
    bot = context.bot

    # Collect chat IDs for notifications (admin + operators)
    notify_ids = [config["telegram"]["admin_id"]]
    operators = db.list_operators()
    notify_ids.extend(op["telegram_id"] for op in operators)

    try:
        await check_and_apply_limits(api, config, bot, notify_ids)
    except Exception as e:
        logger.error(f"Traffic check job failed: {e}")


async def status_update_job(context):
    """Periodic job: update pinned status messages."""
    try:
        await auto_update_status(context)
    except Exception as e:
        logger.error(f"Status update job failed: {e}")


async def monthly_reset_job(context):
    """Daily check: reset traffic on the configured day."""
    config = context.bot_data["config"]
    api = context.bot_data["api"]
    reset_day = config["limits"]["reset_day"]

    if datetime.now().day != reset_day:
        return

    # Check if already reset today
    today_key = f"monthly_reset:{datetime.now().strftime('%Y-%m')}"
    if db.is_notified(today_key):
        return

    logger.info("Starting monthly traffic reset...")

    try:
        success = await monthly_reset(api, config)

        admin_id = config["telegram"]["admin_id"]
        period = datetime.now().strftime("%Y-%m")

        if success:
            db.set_notified(today_key)
            try:
                await context.bot.send_message(
                    admin_id,
                    f"🔄 <b>Месячный сброс выполнен</b>\n"
                    f"Период: {period}\n"
                    f"Трафик и ограничения скорости сброшены.",
                    parse_mode="HTML"
                )
            except Exception:
                pass
        else:
            logger.error("Monthly reset: panel API returned failure, will retry next run")
            try:
                await context.bot.send_message(
                    admin_id,
                    f"⚠️ <b>Ошибка месячного сброса</b>\n"
                    f"Период: {period}\n"
                    f"Не удалось сбросить трафик на панели. Повторная попытка при следующем запуске.",
                    parse_mode="HTML"
                )
            except Exception:
                pass

    except Exception as e:
        logger.error(f"Monthly reset failed: {e}")
