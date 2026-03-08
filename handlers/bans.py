import logging
import subprocess
from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup
from telegram.ext import ContextTypes
from handlers.menu import back_button

logger = logging.getLogger(__name__)


def _get_banned_ips() -> list[dict]:
    """Get banned IPs from fail2ban and 3x-ui ip limit logs."""
    banned = []

    # fail2ban bans (SSH)
    try:
        result = subprocess.run(
            ["fail2ban-client", "status", "sshd"],
            capture_output=True, text=True, timeout=10
        )
        for line in result.stdout.splitlines():
            if "Banned IP list:" in line:
                ips = line.split(":")[-1].strip().split()
                for ip in ips:
                    banned.append({"ip": ip, "source": "fail2ban-ssh"})
    except Exception:
        pass

    # 3X-UI IP limit bans
    try:
        with open("/var/log/x-ui/3xipl-banned.log", "r") as f:
            for line in f:
                line = line.strip()
                if line:
                    # Format varies, try to extract IP
                    parts = line.split()
                    for part in parts:
                        if _is_ip(part):
                            banned.append({"ip": part, "source": "3xui-iplimit"})
                            break
    except FileNotFoundError:
        pass
    except Exception as e:
        logger.error(f"Error reading ban log: {e}")

    # Deduplicate
    seen = set()
    unique = []
    for b in banned:
        if b["ip"] not in seen:
            seen.add(b["ip"])
            unique.append(b)

    return unique


def _is_ip(s: str) -> bool:
    parts = s.split(".")
    if len(parts) == 4:
        return all(p.isdigit() and 0 <= int(p) <= 255 for p in parts)
    return ":" in s  # IPv6


def _unban_ip(ip: str, source: str) -> bool:
    try:
        if source == "fail2ban-ssh":
            subprocess.run(
                ["fail2ban-client", "set", "sshd", "unbanip", ip],
                capture_output=True, timeout=10, check=True
            )
            return True
        elif source == "3xui-iplimit":
            # 3X-UI uses fail2ban jail 3x-ipl
            result = subprocess.run(
                ["fail2ban-client", "set", "3x-ipl", "unbanip", ip],
                capture_output=True, text=True, timeout=10
            )
            if result.returncode == 0:
                return True
            # Try alternative jail name
            subprocess.run(
                ["fail2ban-client", "set", "3x-ipl", "unbanip", ip],
                capture_output=True, timeout=10
            )
            return True
    except Exception as e:
        logger.error(f"Unban failed for {ip}: {e}")
    return False


async def show_bans(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()

    banned = _get_banned_ips()

    if not banned:
        await query.edit_message_text(
            "🚫 <b>Баны</b>\n\nСписок пуст — заблокированных IP нет.",
            reply_markup=back_button(),
            parse_mode="HTML"
        )
        return

    text = f"🚫 <b>Заблокированные IP ({len(banned)})</b>\n\n"
    buttons = []

    for i, b in enumerate(banned):
        source_label = "SSH" if "ssh" in b["source"] else "IP-лимит"
        text += f"{i+1}. <code>{b['ip']}</code> ({source_label})\n"
        buttons.append([InlineKeyboardButton(
            f"🔓 Разбанить {b['ip']}", callback_data=f"unban:{b['ip']}:{b['source']}"
        )])

    buttons.append([InlineKeyboardButton("🔓 Разбанить всех", callback_data="unban_all")])
    buttons.append([InlineKeyboardButton("◀️ Назад", callback_data="main_menu")])

    await query.edit_message_text(text, reply_markup=InlineKeyboardMarkup(buttons), parse_mode="HTML")


async def unban_action(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query

    if query.data == "unban_all":
        await query.answer()
        banned = _get_banned_ips()
        count = 0
        for b in banned:
            if _unban_ip(b["ip"], b["source"]):
                count += 1
        await query.edit_message_text(
            f"🔓 Разблокировано: {count} IP",
            reply_markup=InlineKeyboardMarkup([
                [InlineKeyboardButton("🚫 К банам", callback_data="bans")],
                [InlineKeyboardButton("◀️ Меню", callback_data="main_menu")],
            ]),
            parse_mode="HTML"
        )
        return

    parts = query.data.split(":")
    ip = parts[1]
    source = parts[2]

    await query.answer()
    if _unban_ip(ip, source):
        await query.edit_message_text(
            f"🔓 IP <code>{ip}</code> разблокирован",
            reply_markup=InlineKeyboardMarkup([
                [InlineKeyboardButton("🚫 К банам", callback_data="bans")],
                [InlineKeyboardButton("◀️ Меню", callback_data="main_menu")],
            ]),
            parse_mode="HTML"
        )
    else:
        await query.edit_message_text(
            f"❌ Не удалось разблокировать <code>{ip}</code>",
            reply_markup=InlineKeyboardMarkup([
                [InlineKeyboardButton("🚫 К банам", callback_data="bans")],
            ]),
            parse_mode="HTML"
        )
