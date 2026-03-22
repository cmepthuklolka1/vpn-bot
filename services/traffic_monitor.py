import logging
from datetime import datetime
from database import db
from services import speed_limiter

logger = logging.getLogger(__name__)


def bytes_to_gb(b: int) -> float:
    return round(b / (1024 ** 3), 2)


async def check_and_apply_limits(api, config: dict, bot=None, notify_chat_ids: list = None):
    """
    Main monitoring loop. Called every N minutes.
    Checks each client's traffic, applies speed limits, sends notifications.
    """
    defaults = config["defaults"]
    total_limit_gb = config["limits"]["total_monthly_gb"]
    all_inbounds = await api.get_all_clients()
    online_emails = await api.get_online_clients()

    total_usage_bytes = 0

    for iid, remark, clients in all_inbounds:
        for client in clients:
            email = client["email"]
            client_uuid = client["id"]

            # Get traffic data
            traffic = await api.get_client_traffic(email)
            if not traffic:
                continue

            up = traffic.get("up", 0)
            down = traffic.get("down", 0)
            usage_bytes = up + down
            total_usage_bytes += usage_bytes

            # Get effective config for this client
            eff = db.get_effective_config(email, defaults)

            # Skip unlimited clients for speed limiting
            if eff["is_unlimited"]:
                continue

            # Skip if manual speed override is set
            if eff["speed_override"]:
                continue

            limit_gb = eff["monthly_traffic_gb"]
            if limit_gb <= 0:
                continue

            limit_bytes = limit_gb * (1024 ** 3)
            usage_pct = (usage_bytes / limit_bytes * 100) if limit_bytes > 0 else 0

            # Determine speed tier
            target_speed = eff["speed_base_mbps"]
            tier = None

            if usage_pct >= 95:
                target_speed = eff["speed_95pct_mbps"]
                tier = "95"
            elif usage_pct >= 80:
                target_speed = eff["speed_80pct_mbps"]
                tier = "80"

            # Apply speed limit
            if target_speed and target_speed > 0:
                await speed_limiter.apply_speed_limit_for_client(api, email, target_speed)
            else:
                # Remove limits if base speed is 0 (unlimited)
                await speed_limiter.apply_speed_limit_for_client(api, email, 0)

            # Send notification if threshold crossed
            if tier and bot and notify_chat_ids:
                notif_key = f"client:{email}:{tier}"
                if not db.is_notified(notif_key):
                    db.set_notified(notif_key)
                    usage_gb = bytes_to_gb(usage_bytes)
                    msg = (
                        f"⚠️ <b>Порог трафика</b>\n"
                        f"Клиент: <code>{email}</code>\n"
                        f"Использовано: {usage_gb:.1f} ГБ / {limit_gb} ГБ ({usage_pct:.0f}%)\n"
                        f"Скорость ограничена до {target_speed} Мбит/с"
                    )
                    for chat_id in notify_chat_ids:
                        try:
                            await bot.send_message(chat_id, msg, parse_mode="HTML")
                        except Exception as e:
                            logger.error(f"Failed to send notification to {chat_id}: {e}")

    # Check total usage
    total_usage_gb = bytes_to_gb(total_usage_bytes)

    for pct in [80, 95]:
        threshold_gb = total_limit_gb * pct / 100
        if total_usage_gb >= threshold_gb:
            notif_key = f"total:{pct}"
            if not db.is_notified(notif_key) and bot and notify_chat_ids:
                db.set_notified(notif_key)
                msg = (
                    f"🚨 <b>Общий порог трафика: {pct}%</b>\n"
                    f"Суммарно: {total_usage_gb:.1f} ГБ / {total_limit_gb} ГБ\n"
                    f"{'Рекомендуется ограничить раздачу ключей' if pct >= 95 else 'Приближаемся к лимиту'}"
                )
                for chat_id in notify_chat_ids:
                    try:
                        await bot.send_message(chat_id, msg, parse_mode="HTML")
                    except Exception as e:
                        logger.error(f"Failed to send total notification: {e}")

    return total_usage_bytes


async def monthly_reset(api, config: dict):
    """
    Reset traffic on the configured day.
    Archives current traffic before reset.
    """
    period = datetime.now().strftime("%Y-%m")
    all_inbounds = await api.get_all_clients()

    # Archive current traffic
    for iid, remark, clients in all_inbounds:
        for client in clients:
            email = client["email"]
            traffic = await api.get_client_traffic(email)
            if traffic:
                db.archive_traffic(
                    email, period,
                    traffic.get("up", 0),
                    traffic.get("down", 0)
                )

    # Reset all traffic in 3X-UI
    await api.reset_all_traffics()

    # Clear notification states
    db.clear_notifications()

    # Clear speed limits
    await speed_limiter.clear_all_limits()
    await speed_limiter.init_tc()

    logger.info(f"Monthly reset completed for period {period}")


async def get_status_data(api, config: dict) -> dict:
    """Collect data for the status message."""
    defaults = config["defaults"]
    total_limit_gb = config["limits"]["total_monthly_gb"]

    all_inbounds = await api.get_all_clients()
    online_list = await api.get_online_clients()
    total_inbounds = len(all_inbounds)

    # online_list can be a list of email strings or dicts
    online_emails = set()
    if online_list:
        for item in online_list:
            if isinstance(item, str):
                online_emails.add(item)
            elif isinstance(item, dict):
                online_emails.add(item.get("email", ""))

    client_data = []
    total_usage = 0

    for idx, (iid, remark, clients) in enumerate(all_inbounds, 1):
        for client in clients:
            email = client["email"]
            traffic = await api.get_client_traffic(email)
            up = traffic.get("up", 0) if traffic else 0
            down = traffic.get("down", 0) if traffic else 0
            usage = up + down
            total_usage += usage

            eff = db.get_effective_config(email, defaults)
            is_online = email in online_emails

            # Determine current speed status
            if eff["is_unlimited"]:
                limit_str = "∞"
                speed_str = "—"
            else:
                limit_gb = eff["monthly_traffic_gb"]
                limit_str = f"{limit_gb} ГБ"
                limit_bytes = limit_gb * (1024 ** 3)
                usage_pct = (usage / limit_bytes * 100) if limit_bytes > 0 else 0

                if eff["speed_override"]:
                    speed_str = "🔓 вручную"
                elif usage_pct >= 95:
                    speed_str = f"🔴 {eff['speed_95pct_mbps']} Мбит"
                elif usage_pct >= 80:
                    speed_str = f"🟡 {eff['speed_80pct_mbps']} Мбит"
                else:
                    base = eff["speed_base_mbps"]
                    speed_str = "🟢 без лимита" if base == 0 else f"🟢 {base} Мбит"

            # Inbound label (only when multiple inbounds)
            inbound_label = ""
            if total_inbounds > 1:
                inbound_label = f"[{idx}. {remark}] "

            client_data.append({
                "email": email,
                "usage_gb": bytes_to_gb(usage),
                "limit_str": limit_str,
                "speed_str": speed_str,
                "is_online": is_online,
                "enabled": client.get("enable", True),
                "inbound_label": inbound_label,
            })

    return {
        "clients": client_data,
        "total_usage_gb": bytes_to_gb(total_usage),
        "total_limit_gb": total_limit_gb,
        "updated_at": datetime.now().strftime("%d.%m.%Y %H:%M"),
    }
