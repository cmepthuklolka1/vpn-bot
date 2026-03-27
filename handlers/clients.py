from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup, InputFile
from telegram.ext import ContextTypes, ConversationHandler, CallbackQueryHandler, MessageHandler, filters
from database import db
from services.key_generator import generate_qr
from utils.formatting import format_client_info
from handlers.menu import back_button, main_menu_keyboard, require_auth
from html import escape
import logging
import re

logger = logging.getLogger(__name__)

# Conversation states
SELECT_INBOUND = 0
ENTER_NAME = 1
EDIT_FIELD = 2
EDIT_VALUE = 3


def _inbound_prefix(idx: int, remark: str, total: int) -> str:
    """Display prefix only when there are 2+ inbounds."""
    if total <= 1:
        return ""
    return f"[{idx}. {remark}] "


# --- Create Client ---

@require_auth
async def create_client_start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    api = context.bot_data["api"]

    all_inbounds = await api.get_all_clients()
    if not all_inbounds:
        await query.edit_message_text("❌ Нет доступных подключений в панели.", reply_markup=back_button())
        return ConversationHandler.END

    if len(all_inbounds) == 1:
        # Single inbound — skip selection
        iid, remark, _port, _ = all_inbounds[0]
        context.user_data["selected_inbound_id"] = iid
        context.user_data["selected_inbound_remark"] = remark
        await query.edit_message_text(
            "➕ <b>Создание подключения</b>\n\nВведите имя для нового клиента:",
            reply_markup=InlineKeyboardMarkup([[
                InlineKeyboardButton("❌ Отмена", callback_data="main_menu")
            ]]),
            parse_mode="HTML"
        )
        return ENTER_NAME

    # Multiple inbounds — ask user to choose
    lines = ["➕ <b>Выберите подключение:</b>\n"]
    context.user_data["inbound_list"] = []
    for idx, (iid, remark, _port, _) in enumerate(all_inbounds, 1):
        lines.append(f"<b>{idx}.</b> {escape(remark)}")
        context.user_data["inbound_list"].append((iid, remark))
    lines.append("\nВведите номер подключения:")

    await query.edit_message_text(
        "\n".join(lines),
        reply_markup=InlineKeyboardMarkup([[
            InlineKeyboardButton("❌ Отмена", callback_data="main_menu")
        ]]),
        parse_mode="HTML"
    )
    return SELECT_INBOUND


@require_auth
async def create_client_inbound(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Handle inbound selection (number input)."""
    text = update.message.text.strip()
    inbound_list = context.user_data.get("inbound_list", [])

    if not inbound_list:
        await update.message.reply_text("❌ Ошибка. Начните заново.", reply_markup=back_button())
        return ConversationHandler.END

    try:
        num = int(text)
    except ValueError:
        await update.message.reply_text(f"❌ Введите число от 1 до {len(inbound_list)}:")
        return SELECT_INBOUND

    if num < 1 or num > len(inbound_list):
        await update.message.reply_text(f"❌ Введите число от 1 до {len(inbound_list)}:")
        return SELECT_INBOUND

    iid, remark = inbound_list[num - 1]
    context.user_data["selected_inbound_id"] = iid
    context.user_data["selected_inbound_remark"] = remark

    await update.message.reply_text(
        f"📡 Подключение: <b>{escape(remark)}</b>\n\nВведите имя для нового клиента:",
        parse_mode="HTML"
    )
    return ENTER_NAME


@require_auth
async def create_client_name(update: Update, context: ContextTypes.DEFAULT_TYPE):
    config = context.bot_data["config"]
    api = context.bot_data["api"]
    name = update.message.text.strip()
    inbound_id = context.user_data.get("selected_inbound_id")

    if not inbound_id:
        await update.message.reply_text("❌ Ошибка. Начните заново.", reply_markup=back_button())
        return ConversationHandler.END

    if not re.match(r'^[a-zA-Z0-9а-яА-ЯёЁ_\-\.]+$', name):
        await update.message.reply_text("❌ Имя может содержать только буквы, цифры, _, -, точку. Попробуйте снова:")
        return ENTER_NAME

    if not name or len(name) > 50:
        await update.message.reply_text("❌ Имя должно быть от 1 до 50 символов. Попробуйте снова:")
        return ENTER_NAME

    # Check if name already exists across all inbounds
    all_clients = await api.get_all_clients()
    for _, _, _port, clients in all_clients:
        for c in clients:
            if c["email"].lower() == name.lower():
                await update.message.reply_text(f"❌ Клиент <code>{escape(name)}</code> уже существует. Введите другое имя:", parse_mode="HTML")
                return ENTER_NAME

    defaults = config["defaults"]

    # Create client via API
    status_msg = await update.message.reply_text("⏳ Создаю подключение...")

    client = await api.add_client(
        email=name,
        device_limit=defaults["device_limit"],
        total_gb=0,
        inbound_id=inbound_id,
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
        is_unlimited=0,
        inbound_id=inbound_id,
    )

    # Generate VLESS key
    key = await api.generate_vless_key(client["id"], name, inbound_id=inbound_id)

    if not key:
        await status_msg.edit_text(
            f"✅ Клиент <b>{escape(name)}</b> создан, но не удалось сгенерировать ключ.\n"
            f"UUID: <code>{client['id']}</code>",
            parse_mode="HTML"
        )
        return ConversationHandler.END

    # Send key as monospace (copyable with one tap)
    await status_msg.edit_text(
        f"✅ <b>Клиент создан: {escape(name)}</b>\n\n"
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
            caption=f"QR-код для <b>{escape(name)}</b>",
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

@require_auth
async def manage_client_list(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    api = context.bot_data["api"]

    all_inbounds = await api.get_all_clients()
    total_inbounds = len(all_inbounds)

    buttons = []
    has_clients = False
    for idx, (iid, remark, port, clients) in enumerate(all_inbounds, 1):
        for c in clients:
            has_clients = True
            email = c["email"]
            enabled = "✅" if c.get("enable", True) else "❌"
            prefix = _inbound_prefix(idx, remark, total_inbounds)
            buttons.append([InlineKeyboardButton(
                f"{enabled} {prefix}{email}", callback_data=f"client_detail:{iid}:{email}"
            )])

    if not has_clients:
        await query.edit_message_text("📭 Нет клиентов.", reply_markup=back_button())
        return

    buttons.append([InlineKeyboardButton("◀️ Назад", callback_data="main_menu")])

    await query.edit_message_text(
        "👤 <b>Выберите клиента:</b>",
        reply_markup=InlineKeyboardMarkup(buttons),
        parse_mode="HTML"
    )


@require_auth
async def client_detail(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()

    parts = query.data.split(":", 2)
    if len(parts) == 3:
        # New format: client_detail:{inbound_id}:{email}
        inbound_id = int(parts[1])
        email = parts[2]
    else:
        # Legacy format: client_detail:{email}
        email = parts[1]
        inbound_id = db.get_client_inbound_id(email)

    context.user_data["edit_email"] = email
    context.user_data["edit_inbound_id"] = inbound_id

    api = context.bot_data["api"]
    config = context.bot_data["config"]

    # Find client in the correct inbound
    client = None
    inbound_remark = ""
    all_inbounds = await api.get_all_clients()
    total_inbounds = len(all_inbounds)
    for idx, (iid, remark, port, clients) in enumerate(all_inbounds, 1):
        if inbound_id and iid != inbound_id:
            continue
        for c in clients:
            if c["email"] == email:
                client = c
                inbound_remark = remark
                if not inbound_id:
                    inbound_id = iid
                    context.user_data["edit_inbound_id"] = inbound_id
                break
        if client:
            break

    if not client:
        await query.edit_message_text(f"❌ Клиент {escape(email)} не найден.", reply_markup=back_button())
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

    inbound_label = _inbound_prefix(0, inbound_remark, total_inbounds).strip("[] ") if total_inbounds > 1 else ""
    text = format_client_info(client, traffic, eff, ips, is_online, inbound_label=inbound_label)

    iid_str = str(inbound_id) if inbound_id else "0"
    buttons = [
        [
            InlineKeyboardButton("✏️ Имя", callback_data=f"cedit:email:{iid_str}:{email}"),
            InlineKeyboardButton("📦 Лимит", callback_data=f"cedit:traffic:{iid_str}:{email}"),
        ],
        [
            InlineKeyboardButton("📱 Устройства", callback_data=f"cedit:devices:{iid_str}:{email}"),
            InlineKeyboardButton("⚡ Скорости", callback_data=f"cedit:speeds:{iid_str}:{email}"),
        ],
        [
            InlineKeyboardButton("🔓 Снять лимит скорости" if not eff["speed_override"] else "🔒 Вернуть авто-лимит",
                                 callback_data=f"cedit:toggle_override:{iid_str}:{email}"),
        ],
        [
            InlineKeyboardButton("🔄 Сбросить трафик", callback_data=f"cedit:reset_traffic:{iid_str}:{email}"),
        ],
        [
            InlineKeyboardButton("❌ Отключить" if client.get("enable", True) else "✅ Включить",
                                 callback_data=f"cedit:toggle_enable:{iid_str}:{email}"),
            InlineKeyboardButton("🗑 Удалить", callback_data=f"cedit:delete:{iid_str}:{email}"),
        ],
        [
            InlineKeyboardButton("🔑 Показать ключ", callback_data=f"cedit:show_key:{iid_str}:{email}"),
        ],
        [InlineKeyboardButton("◀️ Назад к списку", callback_data="manage_client")],
    ]

    await query.edit_message_text(text, reply_markup=InlineKeyboardMarkup(buttons), parse_mode="HTML")


@require_auth
async def client_edit_action(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    data = query.data.split(":")

    # New format: cedit:{action}:{inbound_id}:{email}
    if len(data) >= 4:
        action = data[1]
        inbound_id = int(data[2]) if data[2] != "0" else None
        email = ":".join(data[3:])  # email might theoretically contain ":"
    else:
        # Legacy format: cedit:{action}:{email}
        action = data[1]
        email = data[2]
        inbound_id = db.get_client_inbound_id(email)

    api = context.bot_data["api"]
    config = context.bot_data["config"]

    context.user_data["edit_email"] = email
    context.user_data["edit_action"] = action
    context.user_data["edit_inbound_id"] = inbound_id

    iid_str = str(inbound_id) if inbound_id else "0"

    # Quick actions (no input needed)
    if action == "toggle_override":
        await query.answer()
        client_cfg = db.get_client_config(email)
        current = client_cfg["speed_override"] if client_cfg else 0
        new_val = 0 if current else 1
        db.upsert_client_config(email, speed_override=new_val)
        if new_val:
            from services.speed_limiter import apply_speed_limit_for_client
            await apply_speed_limit_for_client(api, email, 0)
        await query.edit_message_text(
            f"{'🔓 Ограничение скорости снято вручную' if new_val else '🔒 Авто-управление скоростью включено'} для <b>{escape(email)}</b>",
            reply_markup=InlineKeyboardMarkup([[
                InlineKeyboardButton("◀️ Назад к клиенту", callback_data=f"client_detail:{iid_str}:{email}")
            ]]),
            parse_mode="HTML"
        )
        return

    if action == "toggle_enable":
        await query.answer()
        clients = await api.get_clients(inbound_id)
        client = next((c for c in clients if c["email"] == email), None)
        if client:
            new_state = not client.get("enable", True)
            await api.update_client(client["id"], {"enable": new_state}, inbound_id=inbound_id)
            await query.edit_message_text(
                f"{'✅ Клиент включён' if new_state else '❌ Клиент отключён'}: <b>{escape(email)}</b>",
                reply_markup=InlineKeyboardMarkup([[
                    InlineKeyboardButton("◀️ Назад к клиенту", callback_data=f"client_detail:{iid_str}:{email}")
                ]]),
                parse_mode="HTML"
            )
        return

    if action == "reset_traffic":
        await query.answer()
        await api.reset_client_traffic(email, inbound_id=inbound_id)
        db.clear_notifications(f"client:{email}:")
        await query.edit_message_text(
            f"🔄 Трафик сброшен для <b>{escape(email)}</b>",
            reply_markup=InlineKeyboardMarkup([[
                InlineKeyboardButton("◀️ Назад к клиенту", callback_data=f"client_detail:{iid_str}:{email}")
            ]]),
            parse_mode="HTML"
        )
        return

    if action == "delete":
        await query.answer()
        buttons = [
            [
                InlineKeyboardButton("✅ Да, удалить", callback_data=f"cedit:confirm_delete:{iid_str}:{email}"),
                InlineKeyboardButton("❌ Нет", callback_data=f"client_detail:{iid_str}:{email}"),
            ]
        ]
        await query.edit_message_text(
            f"🗑 Удалить клиента <b>{escape(email)}</b>?\n\n⚠️ Это действие необратимо!",
            reply_markup=InlineKeyboardMarkup(buttons),
            parse_mode="HTML"
        )
        return

    if action == "confirm_delete":
        await query.answer()
        clients = await api.get_clients(inbound_id)
        client = next((c for c in clients if c["email"] == email), None)
        if client:
            await api.delete_client(client["id"], inbound_id=inbound_id)
            db.delete_client_config(email)
            is_admin = db.is_admin(query.from_user.id, config)
            await query.edit_message_text(
                f"🗑 Клиент <b>{escape(email)}</b> удалён.",
                reply_markup=main_menu_keyboard(is_admin),
                parse_mode="HTML"
            )
        return

    if action == "show_key":
        await query.answer()
        clients = await api.get_clients(inbound_id)
        client = next((c for c in clients if c["email"] == email), None)
        if client:
            key = await api.generate_vless_key(client["id"], email, inbound_id=inbound_id)
            if key:
                await query.edit_message_text(
                    f"🔑 <b>Ключ для {escape(email)}</b>\n\n<code>{key}</code>",
                    reply_markup=InlineKeyboardMarkup([[
                        InlineKeyboardButton("◀️ Назад к клиенту", callback_data=f"client_detail:{iid_str}:{email}")
                    ]]),
                    parse_mode="HTML"
                )
                # Also send QR
                try:
                    qr_buf = generate_qr(key)
                    await query.message.reply_photo(
                        photo=InputFile(qr_buf, filename="qr.png"),
                        caption=f"QR для <b>{escape(email)}</b>",
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


@require_auth
async def client_edit_value(update: Update, context: ContextTypes.DEFAULT_TYPE):
    api = context.bot_data["api"]
    config = context.bot_data["config"]
    email = context.user_data.get("edit_email")
    action = context.user_data.get("edit_action")
    inbound_id = context.user_data.get("edit_inbound_id")
    value = update.message.text.strip()

    if not email or not action:
        await update.message.reply_text("❌ Ошибка. Начните заново.", reply_markup=back_button())
        return ConversationHandler.END

    iid_str = str(inbound_id) if inbound_id else "0"

    try:
        if action == "email":
            # Validate new name
            if not re.match(r'^[a-zA-Z0-9а-яА-ЯёЁ_\-\.]+$', value):
                await update.message.reply_text("❌ Имя может содержать только буквы, цифры, _, -, точку:")
                return EDIT_VALUE
            # Rename client
            clients = await api.get_clients(inbound_id)
            client = next((c for c in clients if c["email"] == email), None)
            if client:
                await api.update_client(client["id"], {"email": value}, inbound_id=inbound_id)
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
            clients = await api.get_clients(inbound_id)
            client = next((c for c in clients if c["email"] == email), None)
            if client:
                await api.update_client(client["id"], {"limitIp": devices}, inbound_id=inbound_id)

        elif action == "speeds":
            parts = value.split()
            if len(parts) != 3:
                await update.message.reply_text("❌ Нужно 3 числа через пробел. Попробуйте снова:")
                return EDIT_VALUE
            base, s80, s95 = float(parts[0]), float(parts[1]), float(parts[2])
            db.upsert_client_config(email, speed_base_mbps=base, speed_80pct_mbps=s80, speed_95pct_mbps=s95)

        await update.message.reply_text(
            f"✅ Изменения сохранены для <b>{escape(email)}</b>",
            reply_markup=InlineKeyboardMarkup([[
                InlineKeyboardButton("◀️ К клиенту", callback_data=f"client_detail:{iid_str}:{email}"),
                InlineKeyboardButton("🏠 Меню", callback_data="main_menu"),
            ]]),
            parse_mode="HTML"
        )
    except ValueError:
        await update.message.reply_text("❌ Некорректное значение. Попробуйте снова:")
        return EDIT_VALUE

    return ConversationHandler.END
