import json
import os
from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup
from telegram.ext import ContextTypes, ConversationHandler
from handlers.menu import back_button
import logging

logger = logging.getLogger(__name__)

EDIT_DEFAULT_VALUE = 10

CONFIG_PATH = os.path.join(os.path.dirname(os.path.dirname(__file__)), "config.json")


def save_config(config: dict):
    with open(CONFIG_PATH, "w") as f:
        json.dump(config, f, indent=4, ensure_ascii=False)


async def show_defaults(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    config = context.bot_data["config"]
    d = config["defaults"]
    limits = config["limits"]

    base_speed = "без лимита" if d['speed_base_mbps'] == 0 else f"{d['speed_base_mbps']} Мбит/с"
    text = (
        "⚙️ <b>Базовый конфиг (для новых клиентов)</b>\n\n"
        f"📱 Устройств: <b>{d['device_limit']}</b>\n"
        f"📦 Лимит трафика: <b>{d['monthly_traffic_gb']} ГБ/мес</b>\n"
        f"⚡ Скорость базовая: <b>{base_speed}</b>\n"
        f"⚡ Скорость 80%: <b>{d['speed_80pct_mbps']} Мбит/с</b>\n"
        f"⚡ Скорость 95%: <b>{d['speed_95pct_mbps']} Мбит/с</b>\n"
        f"\n📈 Общий лимит: <b>{limits['total_monthly_gb']} ГБ</b>\n"
        f"📅 Сброс: <b>{limits['reset_day']}-го числа</b>"
    )

    buttons = [
        [InlineKeyboardButton("📱 Устройства", callback_data="def:device_limit")],
        [InlineKeyboardButton("📦 Лимит трафика", callback_data="def:monthly_traffic_gb")],
        [InlineKeyboardButton("⚡ Скорость базовая", callback_data="def:speed_base_mbps")],
        [InlineKeyboardButton("⚡ Скорость 80%", callback_data="def:speed_80pct_mbps")],
        [InlineKeyboardButton("⚡ Скорость 95%", callback_data="def:speed_95pct_mbps")],
        [InlineKeyboardButton("📈 Общий лимит", callback_data="def:total_monthly_gb")],
        [InlineKeyboardButton("📅 День сброса", callback_data="def:reset_day")],
        [InlineKeyboardButton("◀️ Назад", callback_data="main_menu")],
    ]

    await query.edit_message_text(text, reply_markup=InlineKeyboardMarkup(buttons), parse_mode="HTML")


async def edit_default_start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    field = query.data.split(":")[1]
    context.user_data["edit_default_field"] = field

    labels = {
        "device_limit": "📱 Введите количество устройств:",
        "monthly_traffic_gb": "📦 Введите лимит трафика в ГБ:",
        "speed_base_mbps": "⚡ Введите базовую скорость (Мбит/с, 0 = без лимита):",
        "speed_80pct_mbps": "⚡ Введите скорость при 80% лимита (Мбит/с):",
        "speed_95pct_mbps": "⚡ Введите скорость при 95% лимита (Мбит/с):",
        "total_monthly_gb": "📈 Введите общий месячный лимит в ГБ:",
        "reset_day": "📅 Введите день сброса (1-28):",
    }

    await query.edit_message_text(labels.get(field, "Введите значение:"))
    return EDIT_DEFAULT_VALUE


async def edit_default_value(update: Update, context: ContextTypes.DEFAULT_TYPE):
    field = context.user_data.get("edit_default_field")
    config = context.bot_data["config"]
    value = update.message.text.strip()

    try:
        if field in ("device_limit", "monthly_traffic_gb", "total_monthly_gb", "reset_day"):
            num = int(value)
            if field == "reset_day" and (num < 1 or num > 28):
                await update.message.reply_text("❌ День должен быть от 1 до 28:")
                return EDIT_DEFAULT_VALUE
        else:
            num = float(value)

        if field in ("total_monthly_gb", "reset_day"):
            config["limits"][field] = num
        else:
            config["defaults"][field] = num

        save_config(config)

        await update.message.reply_text(
            f"✅ Значение обновлено.",
            reply_markup=InlineKeyboardMarkup([[
                InlineKeyboardButton("⚙️ К настройкам", callback_data="edit_defaults"),
                InlineKeyboardButton("🏠 Меню", callback_data="main_menu"),
            ]])
        )
    except ValueError:
        await update.message.reply_text("❌ Некорректное значение. Попробуйте снова:")
        return EDIT_DEFAULT_VALUE

    return ConversationHandler.END
