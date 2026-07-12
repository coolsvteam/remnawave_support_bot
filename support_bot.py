import telebot
from telebot import types
import time
import html
import os
import glob
import threading
import datetime
import sqlite3
import psycopg2
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

DB_PATH = "data/support.db"
db_lock = threading.Lock()

if not TOKEN:
    raise ValueError("TELEGRAM_TOKEN не указан в .env")

bot = telebot.TeleBot(TOKEN)

# --- ФУНКЦИЯ ОПРЕДЕЛЕНИЯ ТАРИФА ---
def get_plan_name(traffic_limit_bytes, hwid_limit):
    """Определяет тариф на основе лимитов трафика и устройств"""
    traffic_gb = (traffic_limit_bytes or 0) / 1024**3
    
    if hwid_limit == 1 and traffic_gb <= 1150:
        return "🔹 SecureWeb Start"
    elif hwid_limit <= 3 and traffic_gb <= 1300:
        return "⚡ SecureWeb Plus"
    elif hwid_limit <= 5 and traffic_gb <= 1500:
        return "👑 SecureWeb Ultra"
    else:
        return "Custom"

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
                u.t_id, u.uuid, u.username, u.email, u.telegram_id,
                u.status, u.created_at, u.expire_at,
                u.traffic_limit_bytes, u.traffic_limit_strategy,
                u.hwid_device_limit,
                COALESCE(ut.used_traffic_bytes, 0),
                COALESCE(ut.lifetime_used_traffic_bytes, 0),
                ut.online_at, ut.first_connected_at,
                ut.last_connected_node_uuid,
                n.name as last_node_name
            FROM users u
            LEFT JOIN user_traffic ut ON ut.t_id = u.t_id
            LEFT JOIN nodes n ON n.uuid = ut.last_connected_node_uuid
            WHERE u.telegram_id = %s
            LIMIT 1;
            """
            cur.execute(query, (tg_id,))
            row = cur.fetchone()

            if row is None:
                return " Пользователь не найден в RemnaWave"

            (t_id, uuid, username, email, telegram_id, status,
             created_at, expire_at, traffic_limit, traffic_strategy,
             hwid_limit,
             traffic_used, lifetime_used,
             online_at, first_connected_at, last_node_uuid, last_node_name) = row

            cur.execute("""
                SELECT hwid, platform, os_version, device_model, user_agent, request_ip, created_at
                FROM hwid_user_devices
                WHERE user_id = %s
                ORDER BY created_at DESC
            """, (t_id,))
            devices = cur.fetchall()

            traffic_limit_gb = round((traffic_limit or 0) / 1024**3, 2)
            traffic_used_gb = round((traffic_used or 0) / 1024**3, 2)
            traffic_left_gb = max(round(traffic_limit_gb - traffic_used_gb, 2), 0)

            created = created_at.strftime("%d.%m.%Y %H:%M") if created_at else "—"
            expire = expire_at.strftime("%d.%m.%Y %H:%M") if expire_at else "Без ограничения"
            icon = "🟢" if status == "ACTIVE" else "🔴"

            online = online_at.strftime("%d.%m.%Y %H:%M") if online_at else "Не в сети"
            first_conn = first_connected_at.strftime("%d.%m.%Y %H:%M") if first_connected_at else "—"
            
            strategy_map = {
                "NO_RESET": "Не сбрасывается",
                "DAY": "Ежедневно",
                "WEEK": "Еженедельно",
                "MONTH": "Ежемесячно",
                "MONTH_ROLLING": "Скользящий месяц"
            }
            strategy_text = strategy_map.get(traffic_strategy, traffic_strategy)

            plan_name = get_plan_name(traffic_limit, hwid_limit)

            result = (
                f"{icon} <b>Статус:</b> {status}\n\n"
                f"💎 <b>Тариф:</b> {plan_name}\n\n"
                f"🆔 <b>ID:</b> <code>{t_id}</code>\n"
                f"🧬 <b>UUID:</b>\n<code>{uuid}</code>\n\n"
                f"👤 <b>Username:</b> {username}\n"
                f"📧 <b>Email:</b> {email if email else '—'}\n"
                f"📱 <b>Telegram ID:</b> {telegram_id if telegram_id else '—'}\n\n"
                f"📅 <b>Создан:</b> {created}\n"
                f" <b>Первое подключение:</b> {first_conn}\n"
                f"⏳ <b>Подписка до:</b> {expire}\n\n"
                f"📦 <b>Лимит:</b> {traffic_limit_gb:.2f} GB\n"
                f" <b>Использовано:</b> {traffic_used_gb:.2f} GB\n"
                f"✅ <b>Осталось:</b> {traffic_left_gb:.2f} GB\n"
                f"🔄 <b>Сброс трафика:</b> {strategy_text}\n\n"
                f"📈 <b>За всё время:</b> {round((lifetime_used or 0)/1024**3,2):.2f} GB\n\n"
                f"🟢 <b>Онлайн:</b> {online}\n"
                f"🖥 <b>Последняя нода:</b> {last_node_name if last_node_name else '—'}\n\n"
                f"📱 <b>Устройства:</b> {len(devices)}/{hwid_limit if hwid_limit else '∞'}\n"
            )

            if devices:
                result += "\n<b>📱 Подключенные устройства:</b>\n\n"
                for i, (hwid, platform, os_ver, model, ua, ip, dev_created) in enumerate(devices[:5], 1):
                    dev_date = dev_created.strftime("%d.%m") if dev_created else "—"
                    device_info = f"{i}. {platform or 'Unknown'}"
                    if model:
                        device_info += f" ({model})"
                    if ip:
                        device_info += f" - <code>{ip}</code>"
                    device_info += f" [{dev_date}]\n"
                    result += device_info
                
                if len(devices) > 5:
                    result += f"<i>... и еще {len(devices) - 5} устройств</i>\n"
            else:
                result += "\n<i>Устройства не обнаружены</i>\n"

            return result

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
                "SELECT uid, thread_id FROM tickets WHERE status='open' AND last_activity<?",
                (limit,),
                fetchall=True
            )
            for uid, thread_id in tickets:
                run_query("UPDATE tickets SET status='closed' WHERE thread_id=?", (thread_id,))
                try:
                    bot.close_forum_topic(ADMIN_GROUP_ID, thread_id)
                except Exception as e:
                    logger.error(f"Auto close topic error: {e}")
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
    markup = types.ReplyKeyboardMarkup(resize_keyboard=True)
    markup.add(
        types.KeyboardButton("🎫 Открыть новый тикет"),
        types.KeyboardButton("📊 Моя подписка")
    )
    markup.add(types.KeyboardButton("❌ Закрыть текущий тикет"))
    return markup

def get_active_menu():
    markup = types.ReplyKeyboardMarkup(resize_keyboard=True)
    markup.add(types.KeyboardButton("❌ Закрыть текущий тикет"))
    return markup

def get_admin_buttons(user_id):
    """Кнопки под карточкой тикета (3 кнопки в 2 ряда)"""
    kb = types.InlineKeyboardMarkup(row_width=2)
    kb.add(
        types.InlineKeyboardButton("🔒 Закрыть", callback_data=f"force_close_{user_id}"),
        types.InlineKeyboardButton("🚫 Забанить", callback_data=f"banmenu_{user_id}")
    )
    kb.add(
        types.InlineKeyboardButton("✅ Разбанить", callback_data=f"unban_{user_id}")
    )
    return kb

def get_admin_banned_buttons(user_id):
    """Кнопки после бана - только Закрыть и Разбанить"""
    kb = types.InlineKeyboardMarkup(row_width=2)
    kb.add(
        types.InlineKeyboardButton("🔒 Закрыть", callback_data=f"force_close_{user_id}"),
        types.InlineKeyboardButton("✅ Разбанить", callback_data=f"unban_{user_id}")
    )
    return kb

threading.Thread(target=auto_close_worker, daemon=True).start()

# --- ОБРАБОТКА КОМАНД ---

@bot.message_handler(commands=['start'])
def handle_start(message):
    row = run_query("SELECT is_banned FROM users WHERE uid=?", (message.from_user.id,), fetch=True)
    if row and row[0] == 1:
        return bot.send_message(message.chat.id, " Доступ закрыт.")
    
    welcome_text = (
        f"👋 Приветствуем, {html.escape(message.from_user.first_name)}!\n\n"
        f"Вы обратились в службу поддержки SecureWeb.\n"
        f"Напишите Ваш вопрос, и мы ответим Вам в ближайшее время.\n\n"
        f"<b>Чтобы мы решили Вашу проблему быстрее, укажите сразу:</b>\n\n"
        f"1️ Вашу операционную систему:\n"
        f"iOS / Android / Windows / macOS / Linux\n\n"
        f"2️⃣ Тип подключения:\n"
        f"VLESS / Hysteria / другой протокол\n\n"
        f"3️⃣ Скриншот ошибки, если она есть\n\n"
        f"Нажмите кнопку 👇 «🎫 Открыть новый тикет» для связи"
    )
    bot.send_message(
        message.chat.id,
        welcome_text,
        parse_mode="HTML",
        reply_markup=get_main_menu()
    )

@bot.message_handler(commands=['ticket'])
def handle_ticket_command(message):
    """Обработчик команды /ticket"""
    uid = message.from_user.id
    
    row = run_query("SELECT is_banned FROM users WHERE uid=?", (uid,), fetch=True)
    if row and row[0] == 1:
        return bot.send_message(message.chat.id, "❌ Доступ закрыт.")
    
    ticket = run_query("SELECT ticket_id FROM tickets WHERE uid=? AND status='open'", (uid,), fetch=True)
    if ticket:
        return bot.send_message(
            message.chat.id,
            "️ У вас уже есть открытый тикет. Дождитесь ответа поддержки.",
            reply_markup=get_main_menu()
        )
    
    date_prefix = datetime.datetime.now().strftime("%d%m%y")
    count = run_query("SELECT COUNT(*) FROM tickets WHERE ticket_id LIKE ?", (f"T-{date_prefix}-%",), fetch=True)[0]
    t_id = f"T-{date_prefix}-{count + 1}"
    
    user_info = get_remnawave_info(uid)
    
    try:
        topic = bot.create_forum_topic(ADMIN_GROUP_ID, f" {t_id} | {message.from_user.first_name}")
        bot.send_message(
            ADMIN_GROUP_ID,
            f"🆕 <b>Новое обращение: {t_id}</b>\n"
            f"👤 От: {html.escape(message.from_user.first_name)} (ID: <code>{uid}</code>)\n\n"
            f"💳 <b>Данные подписки:</b>\n{user_info}",
            message_thread_id=topic.message_thread_id,
            parse_mode="HTML",
            reply_markup=get_admin_buttons(uid)
        )
        run_query(
            "INSERT INTO tickets (ticket_id, uid, thread_id, status, created_at, last_activity) VALUES (?, ?, ?, 'open', ?, ?)",
            (t_id, uid, topic.message_thread_id, time.time(), time.time())
        )
        bot.send_message(
            message.chat.id,
            """
✅ <b>Ваш тикет создан!</b>

Пожалуйста, отправьте:

• описание проблемы;
• вашу операционную систему;
• используемый протокол подключения;
• скриншот ошибки (если есть).

Специалист SecureWeb ответит вам в ближайшее время.
            """,
            parse_mode="HTML",
            reply_markup=get_active_menu()
        )
    except Exception as e:
        bot.send_message(message.chat.id, "⚠️ Ошибка при создании тикета. Попробуйте позже.")
        logger.error(f"Topic error: {e}")

@bot.message_handler(commands=['close'])
def handle_close_command(message):
    """Обработчик команды /close"""
    uid = message.from_user.id
    ticket = run_query("SELECT thread_id FROM tickets WHERE uid=? AND status='open'", (uid,), fetch=True)
    
    if ticket:
        run_query("UPDATE tickets SET status='closed' WHERE uid=? AND status='open'", (uid,))
        try:
            bot.close_forum_topic(ADMIN_GROUP_ID, ticket[0])
        except Exception as e:
            logger.error(f"Error closing topic: {e}")
        
        bot.send_message(
            message.chat.id,
            "🏁 Тикет закрыт. Если у вас появятся новые вопросы - создайте другой тикет.",
            reply_markup=get_main_menu()
        )
    else:
        bot.send_message(
            message.chat.id,
            "⚠️ У вас нет открытых тикетов.",
            reply_markup=get_main_menu()
        )

@bot.message_handler(commands=['subscription'])
def handle_subscription_command(message):
    """Обработчик команды /subscription"""
    uid = message.from_user.id
    info = get_remnawave_info(uid)
    bot.send_message(message.chat.id, info, parse_mode="HTML")

@bot.message_handler(commands=['menu'])
def handle_menu_command(message):
    """Обработчик команды /menu"""
    bot.send_message(
        message.chat.id,
        "📋 <b>Главное меню</b>\n\nВыберите действие:",
        parse_mode="HTML",
        reply_markup=get_main_menu()
    )

@bot.message_handler(commands=['unban'])
def admin_unban(message):
    """Разбан пользователя (только для админов)"""
    ADMIN_IDS = [540087018]
    
    if message.from_user.id not in ADMIN_IDS:
        return bot.send_message(message.chat.id, " Недостаточно прав")
    
    if len(message.text.split()) < 2:
        if message.reply_to_message:
            uid = message.reply_to_message.from_user.id
            run_query("UPDATE users SET is_banned=0 WHERE uid=?", (uid,))
            return bot.send_message(
                message.chat.id, 
                f"✅ Пользователь <code>{uid}</code> разбанен!", 
                parse_mode="HTML"
            )
        return bot.send_message(
            message.chat.id, 
            "📝 Использование:\n"
            "• <code>/unban 123456789</code> - по ID\n"
            "• Ответьте на сообщение пользователя командой <code>/unban</code>",
            parse_mode="HTML"
        )
    
    try:
        uid = int(message.text.split()[1])
        run_query("UPDATE users SET is_banned=0 WHERE uid=?", (uid,))
        bot.send_message(
            message.chat.id, 
            f"✅ Пользователь <code>{uid}</code> разбанен!",
            parse_mode="HTML"
        )
        try:
            bot.send_message(uid, "✅ Вы были разбанены службой поддержки.")
        except:
            pass
    except Exception as e:
        logger.error(f"Unban error: {e}")
        bot.send_message(message.chat.id, "❌ Ошибка разбана")

@bot.message_handler(commands=['banned'])
def show_banned(message):
    """Показать список забаненных пользователей"""
    ADMIN_IDS = [540087018]
    
    if message.from_user.id not in ADMIN_IDS:
        return bot.send_message(message.chat.id, "❌ Недостаточно прав")
    
    banned = run_query("SELECT uid, ban_reason FROM users WHERE is_banned=1", fetchall=True)
    
    if not banned:
        return bot.send_message(message.chat.id, "✅ Забаненных пользователей нет")
    
    text = "🚫 <b>Забаненные пользователи:</b>\n\n"
    for uid, reason in banned:
        text += f"• <code>{uid}</code>"
        if reason:
            text += f" - {reason}"
        text += "\n"
    
    bot.send_message(message.chat.id, text, parse_mode="HTML")

# --- ОБРАБОТКА ТЕКСТОВЫХ СООБЩЕНИЙ И КНОПОК ---
@bot.message_handler(content_types=['text', 'photo', 'video', 'document', 'voice'], func=lambda m: m.chat.type == 'private')
def handle_private(message):
    uid = message.from_user.id
    
    row = run_query("SELECT is_banned FROM users WHERE uid=?", (uid,), fetch=True)
    if row and row[0] == 1:
        return

    ticket = run_query("SELECT ticket_id, thread_id FROM tickets WHERE uid=? AND status='open'", (uid,), fetch=True)

    if message.text == "📊 Моя подписка":
        info = get_remnawave_info(uid)
        bot.send_message(message.chat.id, info, parse_mode="HTML")
        return

    if message.text == "🎫 Открыть новый тикет":
        handle_ticket_command(message)
        return

    if message.text == "❌ Закрыть текущий тикет":
        handle_close_command(message)
        return
    
    # Обработка обычных сообщений - БЕЗ КНОПОК под каждым сообщением
    if not ticket:
        return bot.send_message(
            message.chat.id,
            "⚠️ Сначала нажмите «🎫 Открыть новый тикет».",
            reply_markup=get_main_menu()
        )
    
    try:
        # Просто копируем сообщение в админскую тему (без кнопок)
        bot.copy_message(
            ADMIN_GROUP_ID, 
            message.chat.id, 
            message.message_id, 
            message_thread_id=ticket[1]
        )
        
        run_query("UPDATE tickets SET last_activity=? WHERE uid=? AND status='open'", (time.time(), uid))
    except Exception as e:
        bot.send_message(message.chat.id, "️ Ошибка отправки сообщения.")
        logger.error(f"Send message error: {e}")

@bot.callback_query_handler(func=lambda call: call.data.startswith("unban_"))
def admin_unban_callback(call):
    """Разбан пользователя по кнопке"""
    uid = int(call.data.split("_")[1])
    
    run_query("UPDATE users SET is_banned=0 WHERE uid=?", (uid,))
    
    bot.answer_callback_query(call.id, "✅ Пользователь разбанен")
    
    # Возвращаем полный набор кнопок после разбана
    bot.edit_message_reply_markup(
        call.message.chat.id, 
        call.message.message_id, 
        reply_markup=get_admin_buttons(uid)
    )
    
    # Уведомляем пользователя
    try:
        bot.send_message(uid, "✅ Вы были разбанены службой поддержки SecureWeb.")
    except:
        pass
    
    logger.info(f"Admin unbanned user {uid} via callback")

@bot.message_handler(func=lambda m: m.chat.id == ADMIN_GROUP_ID and m.message_thread_id is not None)
def handle_admin_reply(message):
    ticket = run_query("SELECT uid FROM tickets WHERE thread_id=? AND status='open'", (message.message_thread_id,), fetch=True)
    if ticket:
        try:
            bot.copy_message(ticket[0], ADMIN_GROUP_ID, message.message_id)
        except Exception as e:
            logger.error(f"Admin reply error: {e}")

@bot.callback_query_handler(func=lambda call: call.data.startswith("force_close_"))
def admin_close(call):
    uid = int(call.data.split("_")[2])
    ticket = run_query("SELECT thread_id, ticket_id FROM tickets WHERE uid=? AND status='open'", (uid,), fetch=True)
    if ticket:
        run_query("UPDATE tickets SET status='closed' WHERE uid=? AND status='open'", (uid,))
        try:
            bot.close_forum_topic(ADMIN_GROUP_ID, ticket[0])
        except Exception as e:
            logger.error(f"Admin close topic error: {e}")
        bot.send_message(uid, "🔒 Ваш тикет был закрыт поддержкой.", reply_markup=get_main_menu())
        bot.answer_callback_query(call.id, "Тикет закрыт")

@bot.callback_query_handler(func=lambda call: call.data.startswith("banmenu_"))
def ban_menu(call):
    uid = int(call.data.split("_")[1])
    kb = types.InlineKeyboardMarkup(row_width=2)
    kb.add(
        types.InlineKeyboardButton("✅ Забанить", callback_data=f"ban_{uid}"),
        types.InlineKeyboardButton("❌ Отмена", callback_data=f"cancel_{uid}")
    )
    bot.edit_message_reply_markup(call.message.chat.id, call.message.message_id, reply_markup=kb)

@bot.callback_query_handler(func=lambda call: call.data.startswith("ban_"))
def ban_user(call):
    uid = int(call.data.split("_")[1])
    run_query("INSERT OR REPLACE INTO users (uid, is_banned) VALUES (?, 1)", (uid,))
    try:
        bot.send_message(uid, "🚫 Вы заблокированы службой поддержки.")
    except:
        pass
    bot.answer_callback_query(call.id, "Пользователь заблокирован")
    # После бана показываем кнопки "Закрыть" и "Разбанить" (без "Забанить")
    bot.edit_message_reply_markup(
        call.message.chat.id, 
        call.message.message_id, 
        reply_markup=get_admin_banned_buttons(uid)
    )

@bot.callback_query_handler(func=lambda call: call.data.startswith("cancel_"))
def cancel_ban(call):
    uid = int(call.data.split("_")[1])
    bot.edit_message_reply_markup(call.message.chat.id, call.message.message_id, reply_markup=get_admin_buttons(uid))
    bot.answer_callback_query(call.id, "Отменено")

logger.info("Support Bot запущен")

bot.infinity_polling(
    timeout=60,
    long_polling_timeout=60
)
