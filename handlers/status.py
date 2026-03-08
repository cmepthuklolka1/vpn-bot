import logging
from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup
from telegram.ext import ContextTypes
from database import db
from services.traffic_monitor import get_status_data
from utils.formatting import format_status

logger = logging.getLogger(__name__)


async def show_status(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Show current status (manual trigger via menu)."""
    query = update.callback_query
    await query.answer()

    api = context.bot_data["api"]
    config = context.bot_data["config"]

    await query.edit_message_text("⏳ Собираю данные...")

    data = await get_status_data(api, config)
    text = format_status(data)

    buttons = InlineKeyboardMarkup([
        [InlineKeyboardButton("🔄 Обновить", callback_data="refresh_status")],
        [InlineKeyboardButton("📌 Закрепить статус", callback_data="pin_status")],
        [InlineKeyboardButton("◀️ Назад", callback_data="main_menu")],
    ])

    await query.edit_message_text(text, reply_markup=buttons, parse_mode="HTML")


async def refresh_status(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Refresh status data."""
    query = update.callback_query
    await query.answer("🔄 Обновляю...")

    api = context.bot_data["api"]
    config = context.bot_data["config"]

    data = await get_status_data(api, config)
    text = format_status(data)

    buttons = InlineKeyboardMarkup([
        [InlineKeyboardButton("🔄 Обновить", callback_data="refresh_status")],
        [InlineKeyboardButton("📌 Закрепить статус", callback_data="pin_status")],
        [InlineKeyboardButton("◀️ Назад", callback_data="main_menu")],
    ])

    try:
        await query.edit_message_text(text, reply_markup=buttons, parse_mode="HTML")
    except Exception:
        # Message not modified (same content)
        pass


async def pin_status(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Send and pin a status message that will be auto-updated."""
    query = update.callback_query
    await query.answer()

    api = context.bot_data["api"]
    config = context.bot_data["config"]
    chat_id = query.message.chat_id

    data = await get_status_data(api, config)
    text = format_status(data)

    # Send a new message (not edit) so we can pin it
    msg = await context.bot.send_message(
        chat_id, text, parse_mode="HTML",
        reply_markup=InlineKeyboardMarkup([
            [InlineKeyboardButton("🔄 Обновить", callback_data="refresh_pinned")],
        ])
    )

    # Try to pin
    try:
        await msg.pin(disable_notification=True)
    except Exception as e:
        logger.warning(f"Could not pin message: {e}")

    # Save message ID for auto-updates
    db.set_status_message(chat_id, msg.message_id)

    await query.edit_message_text(
        "📌 Статус закреплён. Он будет обновляться каждый час.",
        reply_markup=InlineKeyboardMarkup([
            [InlineKeyboardButton("◀️ Меню", callback_data="main_menu")]
        ])
    )


async def refresh_pinned(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Refresh pinned status message."""
    query = update.callback_query
    await query.answer("🔄 Обновляю...")

    api = context.bot_data["api"]
    config = context.bot_data["config"]

    data = await get_status_data(api, config)
    text = format_status(data)

    try:
        await query.edit_message_text(
            text, parse_mode="HTML",
            reply_markup=InlineKeyboardMarkup([
                [InlineKeyboardButton("🔄 Обновить", callback_data="refresh_pinned")],
            ])
        )
    except Exception:
        pass


async def auto_update_status(context: ContextTypes.DEFAULT_TYPE):
    """Called by scheduler to update all pinned status messages."""
    api = context.bot_data["api"]
    config = context.bot_data["config"]

    data = await get_status_data(api, config)
    text = format_status(data)

    conn = db.get_conn()
    rows = conn.execute("SELECT chat_id, message_id FROM status_messages").fetchall()
    conn.close()

    for row in rows:
        try:
            await context.bot.edit_message_text(
                text,
                chat_id=row["chat_id"],
                message_id=row["message_id"],
                parse_mode="HTML",
                reply_markup=InlineKeyboardMarkup([
                    [InlineKeyboardButton("🔄 Обновить", callback_data="refresh_pinned")],
                ])
            )
        except Exception as e:
            # Message might be too old or deleted
            if "message is not modified" not in str(e).lower():
                logger.warning(f"Failed to update status in chat {row['chat_id']}: {e}")
                # If message can't be edited (>48h), remove it
                if "message can't be edited" in str(e).lower() or "message to edit not found" in str(e).lower():
                    conn = db.get_conn()
                    conn.execute("DELETE FROM status_messages WHERE chat_id = ?", (row["chat_id"],))
                    conn.commit()
                    conn.close()
