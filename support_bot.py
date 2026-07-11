import telebot
from telebot import types
import time
import html
import os
import glob
import threading
import datetime
import sqlite3
import psycopg2 # Драйвер для Postgres
import logging

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s | %(levelname)s | %(message)s"
)

logger = logging.getLogger(__name__)

# --- КОНФИГУРАЦИЯ ---

PROJECT_NAME = os.getenv('PROJECT_NAME', 'VPN Support')
TOKEN = os.getenv('TELEGRAM_TOKEN')
ADMIN_GROUP_ID = int(os.getenv('ADMIN_GROUP_ID', '0'))
BANS_TOPIC_ID = int(os.getenv('BANS_TOPIC_ID', '1'))
AUTO_CLOSE_HOURS = int(os.getenv('AUTO_CLOSE_HOURS', '24'))

PG_HOST = os.getenv('PG_HOST', 'remnawave-db')
PG_DB = os.getenv('PG_DB', 'postgres')
PG_USER = os.getenv('PG_USER', 'postgres')
PG_PASS = os.getenv('PG_PASS', '')

# Локальная БД саппорта (для тикетов и банов)
DB_PATH = "data/support.db"
db_lock = threading.Lock()

if not TOKEN:
    raise ValueError("TELEGRAM_TOKEN не указан в .env")

bot = telebot.TeleBot(TOKEN)

# --- ФУНКЦИЯ ПРОВЕРКИ ПОДПИСКИ (Postgres) ---
def get_remnawave_info(tg_id):

    conn = None

    try:
        conn = psycopg2.connect(
            host=PG_HOST,
            database=PG_DB,
            user=PG_USER,
            password=PG_PASS,
            connect_timeout=5
        )

        with conn.cursor() as cur:

            query = """
            SELECT
                u.t_id,
                u.uuid,
                u.username,
                u.email,
                u.telegram_id,
                u.status,
                u.created_at,
                u.expire_at,
                u.traffic_limit_bytes,
                COALESCE(ut.used_traffic_bytes,0),
                COALESCE(ut.lifetime_used_traffic_bytes,0)
            FROM users u
            LEFT JOIN user_traffic ut
                ON ut.t_id=u.t_id
            WHERE u.telegram_id=%s
            LIMIT 1;
            """

            cur.execute(query, (tg_id,))
            row = cur.fetchone()

            if row is None:
                return "❌ Пользователь не найден в RemnaWave"

            (
                t_id,
                uuid,
                username,
                email,
                telegram_id,
                status,
                created_at,
                expire_at,
                traffic_limit,
                traffic_used,
                lifetime_used
            ) = row

            traffic_limit_gb = round((traffic_limit or 0) / 1024**3, 2)
            traffic_used_gb = round((traffic_used or 0) / 1024**3, 2)
            traffic_left_gb = max(
                round(traffic_limit_gb - traffic_used_gb, 2),
                0
            )

            created = (
                created_at.strftime("%d.%m.%Y %H:%M")
                if created_at else "—"
            )

            expire = (
                expire_at.strftime("%d.%m.%Y %H:%M")
                if expire_at else "Без ограничения"
            )

            icon = "🟢" if status == "ACTIVE" else "🔴"

            return (
                f"{icon} <b>Статус:</b> {status}\n\n"

                f"🆔 <b>ID:</b> <code>{t_id}</code>\n"

                f"🧬 <b>UUID:</b>\n"
                f"<code>{uuid}</code>\n\n"

                f"👤 <b>Username:</b> {username}\n"

                f"📧 <b>Email:</b> "
                f"{email if email else '—'}\n"

                f"📱 <b>Telegram ID:</b> "
                f"{telegram_id if telegram_id else '—'}\n\n"

                f"📅 <b>Создан:</b> {created}\n"

                f"⏳ <b>Подписка до:</b> {expire}\n\n"

                f"📦 <b>Лимит:</b> {traffic_limit_gb:.2f} GB\n"

                f"📊 <b>Использовано:</b> {traffic_used_gb:.2f} GB\n"

                f"✅ <b>Осталось:</b> {traffic_left_gb:.2f} GB\n\n"

                f"📈 <b>За всё время:</b> "
                f"{round((lifetime_used or 0)/1024**3,2):.2f} GB"
            )

    except Exception as e:
        return f"⚠️ Ошибка связи с БД:\n<code>{e}</code>"

    finally:
        if conn:
            conn.close()

# --- ЛОГИКА ЛОКАЛЬНОЙ БД (SQLite) ---
def run_query(query, params=(), fetch=False, fetchall=False):
    with db_lock:
        with sqlite3.connect(DB_PATH) as conn:
            cursor = conn.cursor()
            cursor.execute(query, params)
            if fetch: return cursor.fetchone()
            if fetchall: return cursor.fetchall()
            conn.commit()

def init_db():
    run_query("CREATE TABLE IF NOT EXISTS users (uid INTEGER PRIMARY KEY, is_banned INTEGER DEFAULT 0, ban_reason TEXT)")
    run_query("CREATE TABLE IF NOT EXISTS tickets (ticket_id TEXT PRIMARY KEY, uid INTEGER, thread_id INTEGER, status TEXT DEFAULT 'open', created_at REAL, last_activity REAL)")

init_db()

def auto_close_worker():
    while True:
        try:
            limit = time.time() - AUTO_CLOSE_HOURS * 3600

            tickets = run_query(
                """
                SELECT uid, thread_id 
                FROM tickets
                WHERE status='open'
                AND last_activity<?
                """,
                (limit,),
                fetchall=True
            )

            for uid, thread_id in tickets:
                run_query(
                    """
                    UPDATE tickets
                    SET status='closed'
                    WHERE thread_id=?
                    """,
                    (thread_id,)
                )

                bot.close_forum_topic(
                    ADMIN_GROUP_ID,
                    thread_id
                )

                bot.send_message(
                    uid,
                    "⌛ Ваш тикет был автоматически закрыт из-за отсутствия активности.",
                    reply_markup=get_main_menu()
                )

        except Exception as e:
            logger.error(f"Auto close error: {e}")

        time.sleep(600)

# --- КЛАВИАТУРЫ ---

def get_main_menu():

    markup = types.ReplyKeyboardMarkup(
        resize_keyboard=True
    )

    markup.add(
        types.KeyboardButton("🎫 Открыть новый тикет"),
        types.KeyboardButton("📊 Моя подписка")
    )

    return markup

def get_active_menu():
    markup = types.ReplyKeyboardMarkup(resize_keyboard=True)
    markup.add(types.KeyboardButton("❌ Закрыть текущий тикет"))
    return markup

def get_admin_buttons(user_id):
    kb = types.InlineKeyboardMarkup(row_width=2)
    kb.add(
        types.InlineKeyboardButton("🔒 Закрыть", callback_data=f"force_close_{user_id}"),
        types.InlineKeyboardButton("🚫 Забанить", callback_data=f"banmenu_{user_id}")
    )
    return kb

threading.Thread(
    target=auto_close_worker,
    daemon=True
).start()

# --- ОБРАБОТКА ТИКЕТОВ ---
@bot.message_handler(commands=['start'])
def handle_start(message):
    row = run_query("SELECT is_banned FROM users WHERE uid=?", (message.from_user.id,), fetch=True)
    if row and row[0] == 1: return bot.send_message(message.chat.id, "❌ Доступ закрыт.")
    bot.send_message(
    message.chat.id,
    f"""
👋 Приветствуем, {html.escape(message.from_user.first_name)}!

Вы обратились в службу поддержки SecureWeb.

Напишите Ваш вопрос, и мы ответим вам в ближайшее время.

Чтобы мы решили вашу проблему быстрее, укажите сразу:

1️⃣ Вашу операционную систему:
iOS / Android / Windows / macOS / Linux

2️⃣ Тип подключения:
VLESS / Hysteria / другой протокол

3️⃣ Скриншот ошибки, если она есть

Нажмите кнопку ниже для связи 👇
""",
    parse_mode="HTML",
    reply_markup=get_main_menu()
)
@bot.message_handler(content_types=['text', 'photo', 'video', 'document', 'voice'], func=lambda m: m.chat.type == 'private')
def handle_private(message):
    uid = message.from_user.id
    # Проверка бана
    row = run_query("SELECT is_banned FROM users WHERE uid=?", (uid,), fetch=True)
    if row and row[0] == 1: return

    ticket = run_query("SELECT ticket_id, thread_id FROM tickets WHERE uid=? AND status='open'", (uid,), fetch=True)

    if message.text == "📊 Моя подписка":

        info = get_remnawave_info(uid)

        bot.send_message(
            message.chat.id,
            info,
            parse_mode="HTML"
        )

        return

    if message.text == "🎫 Открыть новый тикет":
        if ticket: return bot.send_message(message.chat.id, "У вас уже есть открытый тикет.")
        
        # ID тикета: T-Дата-Номер
        date_prefix = datetime.datetime.now().strftime("%d%m%y")
        count = run_query("SELECT COUNT(*) FROM tickets WHERE ticket_id LIKE ?", (f"T-{date_prefix}-%",), fetch=True)[0]
        t_id = f"T-{date_prefix}-{count + 1}"
        
        # Пробиваем инфу из базы RemnaWave
        user_info = get_remnawave_info(uid)
        
        try:
            topic = bot.create_forum_topic(ADMIN_GROUP_ID, f"⏳ {t_id} | {message.from_user.first_name}")
            bot.send_message(
                ADMIN_GROUP_ID, 
                f"🆕 <b>Новое обращение: {t_id}</b>\n"
                f"👤 От: {html.escape(message.from_user.first_name)} (ID: <code>{uid}</code>)\n\n"
                f"💳 <b>Данные подписки:</b>\n{user_info}",
                message_thread_id=topic.message_thread_id, 
                parse_mode="HTML", 
                reply_markup=get_admin_buttons(uid)
            )
            run_query("INSERT INTO tickets (ticket_id, uid, thread_id, status, created_at, last_activity) VALUES (?, ?, ?, 'open', ?, ?)", 
                      (t_id, uid, topic.message_thread_id, time.time(), time.time()))
            bot.send_message(
                message.chat.id,
                """
            ✅ Ваш тикет создан!

            Пожалуйста, отправьте:

            • описание проблемы;
            • вашу операционную систему;
            • используемый протокол подключения;
            • скриншот ошибки (если есть).

            Специалист SecureWeb ответит вам в ближайшее время.
            """,
                reply_markup=get_active_menu()
            )

        except Exception as e:
            bot.send_message(message.chat.id, "⚠️ Ошибка при создании тикета. Попробуйте позже.")
            logger.error(f"Topic error: {e}")

    elif message.text == "❌ Закрыть текущий тикет":
        if ticket:
            run_query("UPDATE tickets SET status='closed' WHERE uid=? AND status='open'", (uid,))
            bot.close_forum_topic(ADMIN_GROUP_ID, ticket[1])
            bot.send_message(message.chat.id, "🏁 Тикет закрыт.", reply_markup=get_main_menu())
    else:
        if not ticket: return bot.send_message(message.chat.id, "⚠️ Нажмите «Открыть новый тикет».")
        bot.copy_message(ADMIN_GROUP_ID, message.chat.id, message.message_id, message_thread_id=ticket[1])
        run_query("UPDATE tickets SET last_activity=? WHERE uid=? AND status='open'", (time.time(), uid))

@bot.message_handler(func=lambda m: m.chat.id == ADMIN_GROUP_ID and m.message_thread_id is not None)
def handle_admin_reply(message):
    ticket = run_query("SELECT uid FROM tickets WHERE thread_id=? AND status='open'", (message.message_thread_id,), fetch=True)
    if ticket:
        try: bot.copy_message(ticket[0], ADMIN_GROUP_ID, message.message_id)
        except: pass

@bot.callback_query_handler(func=lambda call: call.data.startswith("force_close_"))
def admin_close(call):
    uid = int(call.data.split("_")[2])
    ticket = run_query("SELECT thread_id, ticket_id FROM tickets WHERE uid=? AND status='open'", (uid,), fetch=True)
    if ticket:
        run_query("UPDATE tickets SET status='closed' WHERE uid=? AND status='open'", (uid,))
        bot.close_forum_topic(ADMIN_GROUP_ID, ticket[0])
        bot.send_message(uid, "🔒 Ваш тикет был закрыт поддержкой.", reply_markup=get_main_menu())
        bot.answer_callback_query(call.id, "Тикет закрыт")

logger.info("Support Bot запущен")

bot.infinity_polling(
    timeout=60,
    long_polling_timeout=60
)
