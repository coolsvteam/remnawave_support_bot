#!/bin/bash

echo "===================================================="
echo "  🎫🎫🎫 RemnaWave Support Bot Installer 🎫🎫🎫  "
echo "===================================================="
echo ""

if ! command -v docker &> /dev/null; then
    echo "Ошибка: Docker не установлен. Установите его перед запуском."
    exit 1
fi

if [ -f .env ]; then
    echo "ВНИМАНИЕ: Файл .env уже существует!"
    read -p "Перезаписать его? (y/n): " confirm
    if [[ $confirm != [yY] ]]; then
        echo "Установка отменена. Ваш текущий .env сохранен."
        exit 0
    fi
fi

echo "--- НАСТРОЙКА БОТА ---"

read -p "Введите название вашего проекта (например, SecureWeb Support): " PROJECT_NAME
read -p "Введите токен бота (от BotFather): " TELEGRAM_TOKEN
read -p "Введите ID админ-группы (начинается с -100): " ADMIN_GROUP_ID
read -p "Введите ID темы для логов/банов (например, 22): " BANS_TOPIC_ID

echo ""
echo "--- НАСТРОЙКА БАЗЫ ДАННЫХ REMNAWAVE ---"

read -p "Хост БД                        [нажмите enter для установки значения по умолчанию remnawave-db]: " PG_HOST
PG_HOST=${PG_HOST:-remnawave-db}

read -p "Имя БД                         [нажмите enter для установки значения по умолчанию postgres]: " PG_DB
PG_DB=${PG_DB:-postgres}

read -p "Пользователь БД                [нажмите enter для установки значения по умолчанию postgres]: " PG_USER
PG_USER=${PG_USER:-postgres}

read -p "Название Docker-сети панели    [нажмите enter для установки значения по умолчанию remnawave-network]: " NET_NAME
NET_NAME=${NET_NAME:-remnawave-network}

read -p "Пароль от БД                   [найди файл .env панели Remnawave и в нем найди строку POSTGRES_PASSWORD= ]: " PG_PASS

echo "Создаю .env файл..."

cat <<EOT > .env
PROJECT_NAME=$PROJECT_NAME
TELEGRAM_TOKEN=$TELEGRAM_TOKEN
ADMIN_GROUP_ID=$ADMIN_GROUP_ID
BANS_TOPIC_ID=$BANS_TOPIC_ID
PG_HOST=$PG_HOST
PG_DB=$PG_DB
PG_USER=$PG_USER
PG_PASS=$PG_PASS
EXTERNAL_NETWORK_NAME=$NET_NAME
AUTO_CLOSE_HOURS=$AUTO_CLOSE_HOURS
TZ=Europe/Moscow
EOT

echo "Файл .env успешно создан!"
echo ""
read -p "Запустить бота прямо сейчас? (y/n): " run_now

if [[ $run_now == [yY] ]]; then
    docker compose up -d --build
    echo "===================================================================="
    echo " 🤖 Бот запущен! Просмотр логов: docker logs remnawave-support-bot"
    echo "===================================================================="
else
    echo "Для ручного запуска используйте команду: docker compose up -d --build"
fi
