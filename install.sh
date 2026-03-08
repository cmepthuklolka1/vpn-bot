#!/bin/bash
set -e

echo "=== VPN Bot Installer ==="

INSTALL_DIR="/opt/vpn-bot"
SERVICE_NAME="vpn-bot"

# Check root
if [ "$EUID" -ne 0 ]; then
    echo "Запустите от root: sudo bash install.sh"
    exit 1
fi

# Install system dependencies
echo "Установка зависимостей..."
apt-get update -qq
apt-get install -y -qq python3 python3-pip python3-venv iproute2 git

# Create config from template if missing
if [ ! -f "$INSTALL_DIR/config.json" ]; then
    echo "Создаю config.json из шаблона..."
    cp "$INSTALL_DIR/config.example.json" "$INSTALL_DIR/config.json"
fi

# Create virtual environment
echo "Создание виртуального окружения..."
cd "$INSTALL_DIR"
python3 -m venv venv
source venv/bin/activate

# Install Python dependencies
echo "Установка Python-пакетов..."
pip install --quiet --upgrade pip
pip install --quiet -r requirements.txt

deactivate

# Check config
if grep -q "YOUR_BOT_TOKEN_HERE" "$INSTALL_DIR/config.json"; then
    echo ""
    echo "Отредактируйте config.json перед запуском:"
    echo "    nano $INSTALL_DIR/config.json"
    echo ""
    echo "Обязательные поля:"
    echo "  - telegram.bot_token"
    echo "  - telegram.admin_id"
    echo "  - panel.url / base_path"
    echo "  - panel.username / password"
    echo "  - panel.inbound_id"
    echo ""
fi

# Create systemd service
echo "Создание systemd-сервиса..."
cat > /etc/systemd/system/${SERVICE_NAME}.service << EOF
[Unit]
Description=VPN Management Telegram Bot
After=network.target x-ui.service
Wants=x-ui.service

[Service]
Type=simple
User=root
WorkingDirectory=${INSTALL_DIR}
ExecStart=${INSTALL_DIR}/venv/bin/python ${INSTALL_DIR}/bot.py
Restart=always
RestartSec=10
Environment=PYTHONUNBUFFERED=1

[Install]
WantedBy=multi-user.target
EOF

systemctl daemon-reload
systemctl enable ${SERVICE_NAME}

echo ""
echo "Установка завершена!"
echo ""
echo "Следующие шаги:"
echo "  1. Отредактируйте конфиг:  nano $INSTALL_DIR/config.json"
echo "  2. Запустите бот:           systemctl start $SERVICE_NAME"
echo "  3. Проверьте статус:        systemctl status $SERVICE_NAME"
echo "  4. Логи:                    journalctl -u $SERVICE_NAME -f"
echo "  5. Логи бота:               tail -f $INSTALL_DIR/bot.log"
echo "  6. Обновление:              bash $INSTALL_DIR/deploy.sh"
