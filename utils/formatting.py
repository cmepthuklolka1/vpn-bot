from datetime import datetime
from html import escape


def format_status(data: dict) -> str:
    """Format the pinned status message."""
    lines = ["📊 <b>Статус клиентов</b>", ""]

    for c in data["clients"]:
        online = "🟢" if c["is_online"] else "⚪"
        enabled = "" if c["enabled"] else " [ОТКЛ]"
        prefix = c.get("inbound_label", "")

        # Device count: connected[limit]
        connected = c.get("connected_ips", 0)
        dev_limit = c.get("device_limit", 0)
        devices_str = f"{connected}[{dev_limit}]" if dev_limit > 0 else f"{connected}[∞]"

        lines.append(
            f"{online} <b>{prefix}{escape(c['email'])}</b>{enabled}\n"
            f"   📦 {c['usage_gb']:.1f} ГБ / {c['limit_str']}  |  {c['speed_str']}  |  📱 {devices_str}"
        )

    lines.append("")
    lines.append("━" * 28)

    total_pct = (data["total_usage_gb"] / data["total_limit_gb"] * 100) if data["total_limit_gb"] > 0 else 0
    lines.append(
        f"📈 <b>Итого:</b> {data['total_usage_gb']:.1f} ГБ / {data['total_limit_gb']} ГБ ({total_pct:.0f}%)"
    )
    lines.append(f"\n🕐 Обновлено: {data['updated_at']}")

    return "\n".join(lines)


def format_client_info(client: dict, traffic: dict, eff_config: dict, ips: list, is_online: bool, inbound_label: str = "", connected_count: int = None) -> str:
    """Format detailed client info."""
    up = traffic.get("up", 0) if traffic else 0
    down = traffic.get("down", 0) if traffic else 0
    usage_gb = (up + down) / (1024 ** 3)

    limit_str = "∞" if eff_config["is_unlimited"] else f"{eff_config['monthly_traffic_gb']} ГБ"

    base_speed = eff_config["speed_base_mbps"]
    speed_80 = eff_config["speed_80pct_mbps"]
    speed_95 = eff_config["speed_95pct_mbps"]

    inbound_suffix = f" ({inbound_label})" if inbound_label else ""
    lines = [
        f"👤 <b>{escape(client['email'])}</b>{inbound_suffix}",
        "",
        f"📦 Трафик: {usage_gb:.2f} ГБ / {limit_str}",
        f"   ↑ {up / (1024**3):.2f} ГБ  |  ↓ {down / (1024**3):.2f} ГБ",
        f"📱 Устройств: {connected_count if connected_count is not None else len(ips)} / {client.get('limitIp', 0) or '∞'}",
        f"🌐 Онлайн: {'Да' if is_online else 'Нет'}",
        f"✅ Включён: {'Да' if client.get('enable', True) else 'Нет'}",
        "",
        f"⚡ Скорости:",
        f"   Базовая: {'без лимита' if base_speed == 0 else f'{base_speed} Мбит/с'}",
        f"   80%: {speed_80} Мбит/с",
        f"   95%: {speed_95} Мбит/с",
        f"   Ручной оверрайд: {'Да' if eff_config['speed_override'] else 'Нет'}",
    ]

    if ips:
        lines.append(f"\n🔗 IP: {', '.join(ips[:5])}")

    lines.append(f"\n🆔 UUID: <code>{client['id']}</code>")

    return "\n".join(lines)


def format_bytes(b: int) -> str:
    if b >= 1024 ** 3:
        return f"{b / (1024**3):.2f} ГБ"
    elif b >= 1024 ** 2:
        return f"{b / (1024**2):.1f} МБ"
    elif b >= 1024:
        return f"{b / 1024:.0f} КБ"
    return f"{b} Б"
