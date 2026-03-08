#!/bin/bash
set -e

SERVICE_NAME="vpn-bot"
INSTALL_DIR="/opt/vpn-bot"

echo "=== VPN Bot Update ==="

# Check root
if [ "$EUID" -ne 0 ]; then
    echo "Запустите от root: sudo bash deploy.sh"
    exit 1
fi

# Check directory
if [ ! -d "$INSTALL_DIR/.git" ]; then
    echo "Git не инициализирован в $INSTALL_DIR"
    echo "Сначала выполните установку: см. README.md"
    exit 1
fi

cd "$INSTALL_DIR"

# Stop bot
echo "Останавливаю бота..."
systemctl stop "$SERVICE_NAME" 2>/dev/null || true

# Pull updates
echo "Загружаю обновления..."
git pull origin main

# Update dependencies
echo "Обновляю зависимости..."
source venv/bin/activate
pip install --quiet -r requirements.txt
deactivate

# Start bot
echo "Запускаю бота..."
systemctl start "$SERVICE_NAME"

echo ""
echo "Обновление завершено!"
systemctl status "$SERVICE_NAME" --no-pager
