from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup, InputFile
from telegram.ext import ContextTypes, ConversationHandler, CallbackQueryHandler, MessageHandler, filters
from database import db
from services.key_generator import generate_qr
from utils.formatting import format_client_info
from handlers.menu import back_button, main_menu_keyboard
import logging

logger = logging.getLogger(__name__)

# Conversation states
ENTER_NAME = 1
EDIT_FIELD = 2
EDIT_VALUE = 3


# --- Create Client ---

async def create_client_start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    await query.edit_message_text(
        "➕ <b>Создание подключения</b>\n\nВведите имя для нового клиента:",
        parse_mode="HTML"
    )
    return ENTER_NAME


async def create_client_name(update: Update, context: ContextTypes.DEFAULT_TYPE):
    config = context.bot_data["config"]
    api = context.bot_data["api"]
    name = update.message.text.strip()

    if not name or len(name) > 50:
        await update.message.reply_text("❌ Имя должно быть от 1 до 50 символов. Попробуйте снова:")
        return ENTER_NAME

    # Check if name already exists
    existing = await api.get_clients()
    for c in existing:
        if c["email"].lower() == name.lower():
            await update.message.reply_text(f"❌ Клиент <code>{name}</code> уже существует. Введите другое имя:", parse_mode="HTML")
            return ENTER_NAME

    defaults = config["defaults"]

    # Create client via API
    status_msg = await update.message.reply_text("⏳ Создаю подключение...")

    client = await api.add_client(
        email=name,
        device_limit=defaults["device_limit"],
        total_gb=0  # Traffic limit managed by our bot, not 3X-UI
    )

    if not client:
        await status_msg.edit_text("❌ Ошибка при создании клиента. Проверьте логи.")
        return ConversationHandler.END

    # Save to our DB
    db.upsert_client_config(
        email=name,
        uuid=client["id"],
        monthly_traffic_gb=defaults["monthly_traffic_gb"],
        device_limit=defaults["device_limit"],
        is_unlimited=0
    )

    # Generate VLESS key
    key = await api.generate_vless_key(client["id"], name)

    if not key:
        await status_msg.edit_text(
            f"✅ Клиент <b>{name}</b> создан, но не удалось сгенерировать ключ.\n"
            f"UUID: <code>{client['id']}</code>",
            parse_mode="HTML"
        )
        return ConversationHandler.END

    # Send key as monospace (copyable with one tap)
    await status_msg.edit_text(
        f"✅ <b>Клиент создан: {name}</b>\n\n"
        f"📋 Ключ подключения (нажмите чтобы скопировать):\n\n"
        f"<code>{key}</code>\n\n"
        f"📱 Устройств: {defaults['device_limit']}\n"
        f"📦 Лимит: {defaults['monthly_traffic_gb']} ГБ/мес",
        parse_mode="HTML"
    )

    # Send QR code
    try:
        qr_buf = generate_qr(key)
        await update.message.reply_photo(
            photo=InputFile(qr_buf, filename="qr.png"),
            caption=f"QR-код для <b>{name}</b>",
            parse_mode="HTML"
        )
    except Exception as e:
        logger.error(f"QR generation failed: {e}")

    return ConversationHandler.END


async def create_client_cancel(update: Update, context: ContextTypes.DEFAULT_TYPE):
    config = context.bot_data["config"]
    is_admin = db.is_admin(update.effective_user.id, config)
    await update.message.reply_text(
        "❌ Создание отменено.\n\n📱 <b>Главное меню</b>",
        reply_markup=main_menu_keyboard(is_admin),
        parse_mode="HTML"
    )
    return ConversationHandler.END


# --- Manage Client (list and select) ---

async def manage_client_list(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    api = context.bot_data["api"]

    clients = await api.get_clients()
    if not clients:
        await query.edit_message_text("📭 Нет клиентов.", reply_markup=back_button())
        return

    buttons = []
    for c in clients:
        email = c["email"]
        enabled = "✅" if c.get("enable", True) else "❌"
        buttons.append([InlineKeyboardButton(
            f"{enabled} {email}", callback_data=f"client_detail:{email}"
        )])
    buttons.append([InlineKeyboardButton("◀️ Назад", callback_data="main_menu")])

    await query.edit_message_text(
        "👤 <b>Выберите клиента:</b>",
        reply_markup=InlineKeyboardMarkup(buttons),
        parse_mode="HTML"
    )


async def client_detail(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    email = query.data.split(":", 1)[1]
    context.user_data["edit_email"] = email

    api = context.bot_data["api"]
    config = context.bot_data["config"]

    # Find client
    clients = await api.get_clients()
    client = None
    for c in clients:
        if c["email"] == email:
            client = c
            break

    if not client:
        await query.edit_message_text(f"❌ Клиент {email} не найден.", reply_markup=back_button())
        return

    traffic = await api.get_client_traffic(email)
    eff = db.get_effective_config(email, config["defaults"])
    ips = await api.get_client_ips(email)

    online_list = await api.get_online_clients()
    online_emails = set()
    for item in online_list:
        if isinstance(item, str):
            online_emails.add(item)
        elif isinstance(item, dict):
            online_emails.add(item.get("email", ""))
    is_online = email in online_emails

    text = format_client_info(client, traffic, eff, ips, is_online)

    buttons = [
        [
            InlineKeyboardButton("✏️ Имя", callback_data=f"cedit:email:{email}"),
            InlineKeyboardButton("📦 Лимит", callback_data=f"cedit:traffic:{email}"),
        ],
        [
            InlineKeyboardButton("📱 Устройства", callback_data=f"cedit:devices:{email}"),
            InlineKeyboardButton("⚡ Скорости", callback_data=f"cedit:speeds:{email}"),
        ],
        [
            InlineKeyboardButton("🔓 Снять лимит скорости" if not eff["speed_override"] else "🔒 Вернуть авто-лимит",
                                 callback_data=f"cedit:toggle_override:{email}"),
        ],
        [
            InlineKeyboardButton("🔄 Сбросить трафик", callback_data=f"cedit:reset_traffic:{email}"),
        ],
        [
            InlineKeyboardButton("❌ Отключить" if client.get("enable", True) else "✅ Включить",
                                 callback_data=f"cedit:toggle_enable:{email}"),
            InlineKeyboardButton("🗑 Удалить", callback_data=f"cedit:delete:{email}"),
        ],
        [
            InlineKeyboardButton("🔑 Показать ключ", callback_data=f"cedit:show_key:{email}"),
        ],
        [InlineKeyboardButton("◀️ Назад к списку", callback_data="manage_client")],
    ]

    await query.edit_message_text(text, reply_markup=InlineKeyboardMarkup(buttons), parse_mode="HTML")


async def client_edit_action(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    data = query.data.split(":")
    action = data[1]
    email = data[2]
    api = context.bot_data["api"]
    config = context.bot_data["config"]

    context.user_data["edit_email"] = email
    context.user_data["edit_action"] = action

    # Quick actions (no input needed)
    if action == "toggle_override":
        await query.answer()
        client_cfg = db.get_client_config(email)
        current = client_cfg["speed_override"] if client_cfg else 0
        new_val = 0 if current else 1
        db.upsert_client_config(email, speed_override=new_val)
        if new_val:
            # Remove speed limits
            from services.speed_limiter import apply_speed_limit_for_client
            await apply_speed_limit_for_client(api, email, 0)
        await query.edit_message_text(
            f"{'🔓 Ограничение скорости снято вручную' if new_val else '🔒 Авто-управление скоростью включено'} для <b>{email}</b>",
            reply_markup=InlineKeyboardMarkup([[
                InlineKeyboardButton("◀️ Назад к клиенту", callback_data=f"client_detail:{email}")
            ]]),
            parse_mode="HTML"
        )
        return

    if action == "toggle_enable":
        await query.answer()
        clients = await api.get_clients()
        client = next((c for c in clients if c["email"] == email), None)
        if client:
            new_state = not client.get("enable", True)
            await api.update_client(client["id"], {"enable": new_state})
            await query.edit_message_text(
                f"{'✅ Клиент включён' if new_state else '❌ Клиент отключён'}: <b>{email}</b>",
                reply_markup=InlineKeyboardMarkup([[
                    InlineKeyboardButton("◀️ Назад к клиенту", callback_data=f"client_detail:{email}")
                ]]),
                parse_mode="HTML"
            )
        return

    if action == "reset_traffic":
        await query.answer()
        await api.reset_client_traffic(email)
        db.clear_notifications(f"client:{email}:")
        await query.edit_message_text(
            f"🔄 Трафик сброшен для <b>{email}</b>",
            reply_markup=InlineKeyboardMarkup([[
                InlineKeyboardButton("◀️ Назад к клиенту", callback_data=f"client_detail:{email}")
            ]]),
            parse_mode="HTML"
        )
        return

    if action == "delete":
        await query.answer()
        buttons = [
            [
                InlineKeyboardButton("✅ Да, удалить", callback_data=f"cedit:confirm_delete:{email}"),
                InlineKeyboardButton("❌ Нет", callback_data=f"client_detail:{email}"),
            ]
        ]
        await query.edit_message_text(
            f"🗑 Удалить клиента <b>{email}</b>?\n\n⚠️ Это действие необратимо!",
            reply_markup=InlineKeyboardMarkup(buttons),
            parse_mode="HTML"
        )
        return

    if action == "confirm_delete":
        await query.answer()
        clients = await api.get_clients()
        client = next((c for c in clients if c["email"] == email), None)
        if client:
            await api.delete_client(client["id"])
            db.delete_client_config(email)
            is_admin = db.is_admin(query.from_user.id, config)
            await query.edit_message_text(
                f"🗑 Клиент <b>{email}</b> удалён.",
                reply_markup=main_menu_keyboard(is_admin),
                parse_mode="HTML"
            )
        return

    if action == "show_key":
        await query.answer()
        clients = await api.get_clients()
        client = next((c for c in clients if c["email"] == email), None)
        if client:
            key = await api.generate_vless_key(client["id"], email)
            if key:
                await query.edit_message_text(
                    f"🔑 <b>Ключ для {email}</b>\n\n<code>{key}</code>",
                    reply_markup=InlineKeyboardMarkup([[
                        InlineKeyboardButton("◀️ Назад к клиенту", callback_data=f"client_detail:{email}")
                    ]]),
                    parse_mode="HTML"
                )
                # Also send QR
                try:
                    qr_buf = generate_qr(key)
                    await query.message.reply_photo(
                        photo=InputFile(qr_buf, filename="qr.png"),
                        caption=f"QR для <b>{email}</b>",
                        parse_mode="HTML"
                    )
                except Exception as e:
                    logger.error(f"QR error: {e}")
        return

    # Actions that need input
    prompts = {
        "email": "✏️ Введите новое имя для клиента:",
        "traffic": "📦 Введите лимит трафика в ГБ (0 = безлимит):",
        "devices": "📱 Введите лимит устройств:",
        "speeds": "⚡ Введите скорости через пробел:\n<i>базовая 80% 95%</i>\nНапример: <code>0 10 1</code>\n(0 = без лимита)",
    }

    await query.answer()
    await query.edit_message_text(prompts.get(action, "Введите значение:"), parse_mode="HTML")
    return EDIT_VALUE


async def client_edit_value(update: Update, context: ContextTypes.DEFAULT_TYPE):
    api = context.bot_data["api"]
    config = context.bot_data["config"]
    email = context.user_data.get("edit_email")
    action = context.user_data.get("edit_action")
    value = update.message.text.strip()

    if not email or not action:
        await update.message.reply_text("❌ Ошибка. Начните заново.", reply_markup=back_button())
        return ConversationHandler.END

    try:
        if action == "email":
            # Rename client
            clients = await api.get_clients()
            client = next((c for c in clients if c["email"] == email), None)
            if client:
                await api.update_client(client["id"], {"email": value})
                # Update our DB
                old_cfg = db.get_client_config(email)
                if old_cfg:
                    db.delete_client_config(email)
                    db.upsert_client_config(value, **{k: v for k, v in old_cfg.items() if k != "email"})
                email = value
                context.user_data["edit_email"] = email

        elif action == "traffic":
            gb = int(value)
            is_unlimited = 1 if gb == 0 else 0
            db.upsert_client_config(email, monthly_traffic_gb=gb if gb > 0 else None, is_unlimited=is_unlimited)

        elif action == "devices":
            devices = int(value)
            db.upsert_client_config(email, device_limit=devices)
            clients = await api.get_clients()
            client = next((c for c in clients if c["email"] == email), None)
            if client:
                await api.update_client(client["id"], {"limitIp": devices})

        elif action == "speeds":
            parts = value.split()
            if len(parts) != 3:
                await update.message.reply_text("❌ Нужно 3 числа через пробел. Попробуйте снова:")
                return EDIT_VALUE
            base, s80, s95 = float(parts[0]), float(parts[1]), float(parts[2])
            db.upsert_client_config(email, speed_base_mbps=base, speed_80pct_mbps=s80, speed_95pct_mbps=s95)

        await update.message.reply_text(
            f"✅ Изменения сохранены для <b>{email}</b>",
            reply_markup=InlineKeyboardMarkup([[
                InlineKeyboardButton("◀️ К клиенту", callback_data=f"client_detail:{email}"),
                InlineKeyboardButton("🏠 Меню", callback_data="main_menu"),
            ]]),
            parse_mode="HTML"
        )
    except ValueError:
        await update.message.reply_text("❌ Некорректное значение. Попробуйте снова:")
        return EDIT_VALUE

    return ConversationHandler.END
