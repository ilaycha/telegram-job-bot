import os
import sys
import threading
import asyncio
import sqlite3
import csv
import io
import json
import functools
import traceback
import time
from datetime import datetime, timedelta
from http.server import HTTPServer, BaseHTTPRequestHandler
from typing import Optional, Dict, Any

# Telegram
from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup
from telegram.ext import (
    Application,
    CommandHandler,
    MessageHandler,
    CallbackQueryHandler,
    ConversationHandler,
    ContextTypes,
    filters,
)

# VK
import vk_api
from vk_api.bot_longpoll import VkBotLongPoll, VkBotEventType
from vk_api.utils import get_random_id

# Конфигурация
from config import (
    VACANCY_BOT_TOKEN,
    CLEANER_BOT_TOKEN,
    MODERATION_GROUP_ID,
    ADMIN_IDS,
    CHANNEL_USERNAME,
    VACANCY_THREAD_ID,
    RESUME_THREAD_ID,
    VK_TOKEN,
    VK_GROUP_ID,
    VK_CHAT_VACANCIES,
    VK_CHAT_RESUMES,
    DB_NAME,
    TGState,
    VKState,
    VACANCY_FORM,
    RESUME_FORM,
)

# ============================================
# Принудительный вывод диагностики
# ============================================
print("=" * 60, flush=True)
print("🚀 ЗАПУСК БОТА...", flush=True)
print(f"Python версия: {sys.version}", flush=True)
print(f"VK_TOKEN задан: {bool(VK_TOKEN)}", flush=True)
print(f"VK_TOKEN длина: {len(VK_TOKEN) if VK_TOKEN else 0}", flush=True)
print(f"VK_GROUP_ID: {VK_GROUP_ID}", flush=True)
print(f"VK_CHAT_VACANCIES: {VK_CHAT_VACANCIES}", flush=True)
print(f"VK_CHAT_RESUMES: {VK_CHAT_RESUMES}", flush=True)
print(f"MODERATION_GROUP_ID: {MODERATION_GROUP_ID}", flush=True)
print(f"ADMIN_IDS: {ADMIN_IDS}", flush=True)
print("=" * 60, flush=True)
sys.stdout.flush()

# ============================================
# Глобальные переменные
# ============================================
vk_api_instance = None
vk_longpoll = None
_telegram_app = None

# ============================================
# Декораторы
# ============================================
def retry_on_exception(exception=Exception, tries=3, delay=1, backoff=2):
    """Декоратор для повторных попыток выполнения функции"""
    def decorator(func):
        @functools.wraps(func)
        async def wrapper(*args, **kwargs):
            current_delay = delay
            for attempt in range(tries):
                try:
                    return await func(*args, **kwargs)
                except exception as e:
                    if attempt == tries - 1:
                        print(f"❌ Исчерпаны попытки для {func.__name__}: {e}", flush=True)
                        raise
                    print(f"⚠️ Попытка {attempt + 1}/{tries} для {func.__name__}: {e}", flush=True)
                    await asyncio.sleep(current_delay)
                    current_delay *= backoff
            return None
        return wrapper
    return decorator

# ============================================
# Работа с БД
# ============================================
def get_db_connection():
    """Создать соединение с БД"""
    conn = sqlite3.connect(DB_NAME)
    conn.row_factory = sqlite3.Row
    return conn

def init_database():
    """Инициализация БД"""
    conn = get_db_connection()
    cursor = conn.cursor()
    
    cursor.execute("""
        CREATE TABLE IF NOT EXISTS publications (
            id TEXT PRIMARY KEY,
            user_id TEXT,
            platform TEXT,
            text TEXT,
            type TEXT,
            status TEXT DEFAULT 'pending',
            tg_channel_message_id INTEGER,
            vk_message_id INTEGER,
            vk_chat_id INTEGER,
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
            moderated_at TIMESTAMP
        )
    """)
    
    cursor.execute("""
        CREATE TABLE IF NOT EXISTS publications_archive (
            id TEXT PRIMARY KEY,
            user_id TEXT,
            platform TEXT,
            text TEXT,
            type TEXT,
            status TEXT,
            tg_channel_message_id INTEGER,
            vk_message_id INTEGER,
            vk_chat_id INTEGER,
            created_at TIMESTAMP,
            moderated_at TIMESTAMP,
            archived_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
        )
    """)
    
    cursor.execute("""
        CREATE TABLE IF NOT EXISTS vk_user_states (
            user_id TEXT PRIMARY KEY,
            state TEXT,
            data TEXT,
            updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
        )
    """)
    
    cursor.execute("CREATE INDEX IF NOT EXISTS idx_user_id ON publications(user_id)")
    cursor.execute("CREATE INDEX IF NOT EXISTS idx_status ON publications(status)")
    cursor.execute("CREATE INDEX IF NOT EXISTS idx_platform ON publications(platform)")
    cursor.execute("CREATE INDEX IF NOT EXISTS idx_created_at ON publications(created_at)")
    cursor.execute("CREATE INDEX IF NOT EXISTS idx_vk_states_updated ON vk_user_states(updated_at)")
    
    conn.commit()
    conn.close()
    print("📦 База данных инициализирована", flush=True)

# ----- Основные функции БД -----
def save_publication(pub_id: str, user_id: str, platform: str, text: str, pub_type: str, status: str = 'pending'):
    """Сохранить публикацию в БД"""
    conn = get_db_connection()
    cursor = conn.cursor()
    cursor.execute("""
        INSERT OR REPLACE INTO publications (id, user_id, platform, text, type, status, created_at)
        VALUES (?, ?, ?, ?, ?, ?, CURRENT_TIMESTAMP)
    """, (pub_id, user_id, platform, text, pub_type, status))
    conn.commit()
    conn.close()

def update_publication_status(pub_id: str, status: str, tg_channel_message_id: Optional[int] = None,
                              vk_message_id: Optional[int] = None, vk_chat_id: Optional[int] = None):
    """Обновить статус публикации"""
    conn = get_db_connection()
    cursor = conn.cursor()
    
    if tg_channel_message_id is not None and vk_message_id is not None:
        cursor.execute("""
            UPDATE publications 
            SET status = ?, tg_channel_message_id = ?, vk_message_id = ?, vk_chat_id = ?, moderated_at = CURRENT_TIMESTAMP
            WHERE id = ?
        """, (status, tg_channel_message_id, vk_message_id, vk_chat_id, pub_id))
    else:
        cursor.execute("""
            UPDATE publications 
            SET status = ?, moderated_at = CURRENT_TIMESTAMP
            WHERE id = ?
        """, (status, pub_id))
    
    conn.commit()
    conn.close()

def get_publication(pub_id: str) -> Optional[Dict]:
    """Получить публикацию по ID"""
    conn = get_db_connection()
    cursor = conn.cursor()
    cursor.execute("SELECT * FROM publications WHERE id = ?", (pub_id,))
    row = cursor.fetchone()
    conn.close()
    return dict(row) if row else None

def get_user_publications(user_id: str, platform: Optional[str] = None, status: Optional[str] = None) -> list:
    """Получить публикации пользователя (по умолчанию только активные: pending и approved)"""
    conn = get_db_connection()
    cursor = conn.cursor()
    
    query = "SELECT * FROM publications WHERE user_id = ?"
    params = [user_id]
    
    if platform:
        query += " AND platform = ?"
        params.append(platform)
    
    if status:
        query += " AND status = ?"
        params.append(status)
    else:
        query += " AND status IN ('pending', 'approved')"
    
    query += " ORDER BY created_at DESC"
    
    cursor.execute(query, params)
    rows = cursor.fetchall()
    conn.close()
    return [dict(row) for row in rows]

def delete_publication_from_db(pub_id: str):
    """Пометить публикацию как удалённую"""
    conn = get_db_connection()
    cursor = conn.cursor()
    cursor.execute("UPDATE publications SET status = 'deleted' WHERE id = ?", (pub_id,))
    conn.commit()
    conn.close()

def archive_old_publications() -> int:
    """Архивировать старые публикации"""
    conn = get_db_connection()
    cursor = conn.cursor()
    
    cursor.execute("""
        INSERT INTO publications_archive (id, user_id, platform, text, type, status, tg_channel_message_id, vk_message_id, vk_chat_id, created_at, moderated_at)
        SELECT id, user_id, platform, text, type, status, tg_channel_message_id, vk_message_id, vk_chat_id, created_at, moderated_at
        FROM publications 
        WHERE status IN ('deleted', 'expired')
        AND created_at < datetime('now', '-6 months')
    """)
    
    cursor.execute("""
        DELETE FROM publications 
        WHERE status IN ('deleted', 'expired')
        AND created_at < datetime('now', '-6 months')
    """)
    
    conn.commit()
    rows = cursor.total_changes
    conn.close()
    return rows

def auto_reject_stale_publications() -> int:
    """Автоматически отклонить просроченные публикации"""
    conn = get_db_connection()
    cursor = conn.cursor()
    
    cursor.execute("""
        SELECT id FROM publications 
        WHERE status = 'pending'
        AND created_at < datetime('now', '-2 months')
    """)
    
    stale_ids = [row[0] for row in cursor.fetchall()]
    
    for pub_id in stale_ids:
        cursor.execute("""
            UPDATE publications SET status = 'rejected', moderated_at = CURRENT_TIMESTAMP
            WHERE id = ?
        """, (pub_id,))
        print(f"⏰ Автоматически отклонена публикация {pub_id} (просрочена)", flush=True)
    
    conn.commit()
    conn.close()
    return len(stale_ids)

def cleanup_old_vk_states() -> int:
    """Очистить состояния VK старше 24 часов (брошенные пользователями)"""
    conn = get_db_connection()
    cursor = conn.cursor()
    
    cursor.execute("""
        DELETE FROM vk_user_states 
        WHERE updated_at < datetime('now', '-24 hours')
    """)
    
    deleted = cursor.rowcount
    if deleted > 0:
        print(f"🧹 Очищено {deleted} устаревших состояний VK", flush=True)
    
    conn.commit()
    conn.close()
    return deleted

# ----- Функции состояний VK -----
def get_vk_state(user_id: str) -> tuple:
    """Получить состояние пользователя VK"""
    conn = get_db_connection()
    cursor = conn.cursor()
    cursor.execute("SELECT state, data FROM vk_user_states WHERE user_id = ?", (user_id,))
    row = cursor.fetchone()
    conn.close()
    if row:
        return row['state'], json.loads(row['data']) if row['data'] else {}
    return VKState.MAIN_MENU, {}

def save_vk_state(user_id: str, state: str, data: Optional[Dict] = None):
    """Сохранить состояние пользователя VK"""
    conn = get_db_connection()
    cursor = conn.cursor()
    cursor.execute("""
        INSERT OR REPLACE INTO vk_user_states (user_id, state, data, updated_at)
        VALUES (?, ?, ?, CURRENT_TIMESTAMP)
    """, (user_id, state, json.dumps(data) if data else '{}'))
    conn.commit()
    conn.close()

def clear_vk_state(user_id: str):
    """Очистить состояние VK"""
    conn = get_db_connection()
    cursor = conn.cursor()
    cursor.execute("DELETE FROM vk_user_states WHERE user_id = ?", (user_id,))
    conn.commit()
    conn.close()

# ============================================
# Вспомогательные функции
# ============================================
def build_vacancy_text(data: dict) -> str:
    """Форматировать текст вакансии"""
    lines = []
    if data.get("title"): lines.append(f"📌 {data['title']}")
    if data.get("company"): lines.append(f"🏢 {data['company']}")
    if data.get("salary"): lines.append(f"💰 {data['salary']}")
    if data.get("schedule"): lines.append(f"🕒 {data['schedule']}")
    if data.get("description"): lines.append(f"📋 {data['description']}")
    if data.get("contact"): lines.append(f"📞 {data['contact']}")
    return "\n\n".join(lines) if lines else "⚠️ Данные не заполнены"

def build_resume_text(data: dict) -> str:
    """Форматировать текст резюме"""
    lines = []
    if data.get("name"): lines.append(f"👤 {data['name']}")
    if data.get("age"): lines.append(f"🎂 {data['age']}")
    if data.get("position"): lines.append(f"💼 Желаемая должность: {data['position']}")
    if data.get("experience"): lines.append(f"📅 Опыт работы:\n{data['experience']}")
    if data.get("skills"): lines.append(f"🛠 Навыки:\n{data['skills']}")
    if data.get("education"): lines.append(f"🎓 Образование:\n{data['education']}")
    if data.get("contact"): lines.append(f"📞 Контакты: {data['contact']}")
    return "\n\n".join(lines) if lines else "⚠️ Данные не заполнены"

async def safe_edit(query, text, **kwargs):
    """Безопасное редактирование сообщения"""
    try:
        await query.edit_message_text(text, **kwargs)
    except Exception as e:
        print(f"⚠️ Не удалось отредактировать сообщение: {e}", flush=True)

def get_telegram_app():
    """Получить экземпляр Telegram-приложения"""
    return _telegram_app

# ============================================
# Telegram: Клавиатуры для шагов
# ============================================
def get_step_keyboard(skip_callback: Optional[str] = None):
    """Клавиатура с кнопками Пропустить (опционально) и Отменить"""
    buttons = []
    if skip_callback:
        buttons.append([InlineKeyboardButton("⏭ Пропустить", callback_data=skip_callback)])
    buttons.append([InlineKeyboardButton("❌ Отменить", callback_data="cancel_action")])
    return InlineKeyboardMarkup(buttons)

# ============================================
# Публикация и удаление
# ============================================
@retry_on_exception(tries=2, delay=2)
async def _publish_to_telegram(context, text: str, thread_id: int):
    """Публикация в Telegram с повторной попыткой"""
    sent = await context.bot.send_message(
        chat_id=CHANNEL_USERNAME,
        text=text,
        message_thread_id=thread_id
    )
    return sent.message_id

@retry_on_exception(tries=2, delay=2)
async def _publish_to_vk(text: str, chat_id: int):
    """Публикация в VK с повторной попыткой"""
    global vk_api_instance
    if vk_api_instance:
        result = vk_api_instance.messages.send(
            chat_id=chat_id,
            message=text,
            random_id=get_random_id()
        )
        return result
    return None

async def publish_to_both_platforms(pub_id: str, context) -> bool:
    """Публикует одобренную заявку в Telegram и VK"""
    pub = get_publication(pub_id)
    if not pub:
        print(f"❌ Публикация {pub_id} не найдена", flush=True)
        return False
    
    text = pub['text']
    pub_type = pub['type']
    
    if pub_type == 'vacancy':
        tg_thread_id = VACANCY_THREAD_ID
        vk_chat_id = VK_CHAT_VACANCIES
    else:
        tg_thread_id = RESUME_THREAD_ID
        vk_chat_id = VK_CHAT_RESUMES
    
    tg_message_id = None
    vk_message_id = None
    
    try:
        tg_message_id = await _publish_to_telegram(context, text, tg_thread_id)
        print(f"✅ Опубликовано в Telegram: {pub_id}", flush=True)
    except Exception as e:
        print(f"❌ Ошибка публикации в Telegram: {e}", flush=True)
        return False
    
    try:
        vk_message_id = await _publish_to_vk(text, vk_chat_id)
        if vk_message_id:
            print(f"✅ Опубликовано в VK (чат {vk_chat_id}): {pub_id}", flush=True)
    except Exception as e:
        print(f"⚠️ Ошибка публикации в VK: {e}", flush=True)
    
    update_publication_status(
        pub_id,
        'approved',
        tg_channel_message_id=tg_message_id,
        vk_message_id=vk_message_id,
        vk_chat_id=vk_chat_id
    )
    
    return True

async def delete_from_both_platforms(pub_id: str, context) -> bool:
    """Удаляет публикацию из Telegram и VK"""
    pub = get_publication(pub_id)
    if not pub:
        return False
    
    if pub['tg_channel_message_id']:
        try:
            await context.bot.delete_message(
                chat_id=CHANNEL_USERNAME,
                message_id=pub['tg_channel_message_id']
            )
            print(f"🗑 Удалено из Telegram: {pub_id}", flush=True)
        except Exception as e:
            print(f"⚠️ Не удалось удалить из Telegram: {e}", flush=True)
    
    if pub['vk_message_id'] and pub['vk_chat_id']:
        try:
            if vk_api_instance:
                vk_api_instance.messages.delete(
                    message_ids=[pub['vk_message_id']],
                    delete_for_all=1
                )
                print(f"🗑 Удалено из VK (чат {pub['vk_chat_id']}): {pub_id}", flush=True)
        except Exception as e:
            print(f"⚠️ Не удалось удалить из VK: {e}", flush=True)
    
    delete_publication_from_db(pub_id)
    return True

# ============================================
# HTTP-Сервер (для Render)
# ============================================
class SimpleHandler(BaseHTTPRequestHandler):
    def do_GET(self):
        self._send_ok()
    def do_HEAD(self):
        self._send_ok()
    def _send_ok(self):
        self.send_response(200)
        self.send_header("Content-type", "text/plain")
        self.end_headers()
        if self.command == "GET":
            self.wfile.write(b"Bots are running")
    def log_message(self, format, *args):
        pass

def run_server():
    """Запуск HTTP-сервера"""
    port = int(os.environ.get("PORT", 10000))
    server = HTTPServer(("0.0.0.0", port), SimpleHandler)
    print(f"🌐 HTTP Server running on port {port}", flush=True)
    server.serve_forever()

# ============================================
# Telegram: ГЛОБАЛЬНЫЙ обработчик /start (работает всегда)
# ============================================
async def global_start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """
    Глобальный обработчик /start — срабатывает ВСЕГДА, 
    даже когда пользователь не в диалоге.
    """
    context.user_data.clear()
    keyboard = InlineKeyboardMarkup([
        [InlineKeyboardButton("📌 Создать вакансию", callback_data="menu_vacancy")],
        [InlineKeyboardButton("📄 Создать резюме", callback_data="menu_resume")],
        [InlineKeyboardButton("📋 Мои публикации", callback_data="my_publications")],
    ])
    await update.message.reply_text("👋 Привет! Что вы хотите сделать?", reply_markup=keyboard)
    return TGState.MAIN_MENU

# ============================================
# Telegram: Обработчики команд
# ============================================
async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Команда /start внутри диалога (fallback)"""
    context.user_data.clear()
    keyboard = InlineKeyboardMarkup([
        [InlineKeyboardButton("📌 Создать вакансию", callback_data="menu_vacancy")],
        [InlineKeyboardButton("📄 Создать резюме", callback_data="menu_resume")],
        [InlineKeyboardButton("📋 Мои публикации", callback_data="my_publications")],
    ])
    await update.message.reply_text("👋 Привет! Что вы хотите сделать?", reply_markup=keyboard)
    return TGState.MAIN_MENU

async def my_publications(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Команда /my — показать активные публикации"""
    user_id = str(update.effective_user.id)
    publications = get_user_publications(user_id, platform='tg')
    
    if not publications:
        await update.message.reply_text("📭 У вас нет активных публикаций.")
        return
    
    total = len(publications)
    await update.message.reply_text(f"📊 Ваши активные публикации (всего: {total}):")
    
    for pub in publications[:10]:
        pub_type = "📌 Вакансия" if pub['type'] == 'vacancy' else "📄 Резюме"
        preview = pub['text'][:150] + "..." if len(pub['text']) > 150 else pub['text']
        
        status_emoji = {
            'pending': '⏳ На модерации',
            'approved': '✅ Опубликовано',
            'rejected': '❌ Отклонено',
            'deleted': '🗑 Удалено',
            'expired': '⏰ Истекло'
        }.get(pub['status'], '❓')
        
        keyboard = InlineKeyboardMarkup([
            [InlineKeyboardButton("🗑 Удалить", callback_data=f"delete:{pub['id']}")]
        ])
        
        await update.message.reply_text(
            f"{status_emoji} {pub_type} от {pub['created_at'][:10]}\n\n{preview}",
            reply_markup=keyboard
        )
    
    if total > 10:
        await update.message.reply_text(f"🔍 Показаны последние 10 из {total}.")

# ============================================
# Telegram: Админ-команды
# ============================================
async def admin_stats(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Админ-команда: статистика"""
    if update.effective_user.id not in ADMIN_IDS:
        await update.message.reply_text("⛔ Доступ запрещён.")
        return
    
    conn = get_db_connection()
    cursor = conn.cursor()
    
    cursor.execute("SELECT COUNT(*) FROM publications")
    total = cursor.fetchone()[0]
    
    cursor.execute("SELECT platform, COUNT(*) FROM publications GROUP BY platform")
    platforms = cursor.fetchall()
    
    cursor.execute("SELECT status, COUNT(*) FROM publications GROUP BY status")
    stats = cursor.fetchall()
    
    cursor.execute("SELECT COUNT(DISTINCT user_id) FROM publications")
    users = cursor.fetchone()[0]
    
    conn.close()
    
    text = f"📊 **Статистика БД**\n\n"
    text += f"👥 Всего пользователей: {users}\n"
    text += f"📄 Всего публикаций: {total}\n\n"
    
    text += "**По платформам:**\n"
    platform_names = {'tg': 'Telegram', 'vk': 'VK'}
    for platform, count in platforms:
        name = platform_names.get(platform, platform)
        text += f"📱 {name}: {count}\n"
    
    text += "\n**По статусам:**\n"
    status_emoji = {
        'pending': '⏳ На модерации',
        'approved': '✅ Опубликовано',
        'rejected': '❌ Отклонено',
        'deleted': '🗑 Удалено',
        'expired': '⏰ Истекло'
    }
    for status, count in stats:
        emoji = status_emoji.get(status, '❓')
        text += f"{emoji} {status}: {count}\n"
    
    await update.message.reply_text(text)

async def admin_export(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Админ-команда: экспорт в CSV"""
    if update.effective_user.id not in ADMIN_IDS:
        await update.message.reply_text("⛔ Доступ запрещён.")
        return
    
    await update.message.reply_text("⏳ Формирую файл экспорта...")
    
    conn = get_db_connection()
    cursor = conn.cursor()
    cursor.execute("SELECT * FROM publications ORDER BY created_at DESC")
    rows = cursor.fetchall()
    conn.close()
    
    if not rows:
        await update.message.reply_text("📭 База данных пуста.")
        return
    
    output = io.StringIO()
    writer = csv.writer(output, delimiter=';', quoting=csv.QUOTE_MINIMAL)
    writer.writerow([
        'ID', 'Платформа', 'ID пользователя', 'Тип', 'Статус',
        'Текст (первые 500 символов)', 'ID канала TG', 'ID сообщения VK',
        'ID чата VK', 'Создано', 'Обработано'
    ])
    
    for row in rows:
        text_preview = row['text'][:500] + "..." if len(row['text']) > 500 else row['text']
        text_preview = text_preview.replace('\n', ' ').replace('\r', ' ')
        
        writer.writerow([
            row['id'],
            row['platform'],
            row['user_id'],
            row['type'],
            row['status'],
            text_preview,
            row['tg_channel_message_id'] or '',
            row['vk_message_id'] or '',
            row['vk_chat_id'] or '',
            row['created_at'],
            row['moderated_at'] or ''
        ])
    
    output.seek(0)
    await update.message.reply_document(
        document=io.BytesIO(output.getvalue().encode('utf-8-sig')),
        filename=f"publications_export_{datetime.now().strftime('%Y%m%d_%H%M%S')}.csv"
    )

# ============================================
# Telegram: Обработчики шагов
# ============================================
def make_step_handler(field: str, next_state: TGState, prompt: str, skip_callback: Optional[str] = None):
    """Создать обработчик шага с кнопками Пропустить (если можно) и Отменить"""
    async def handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
        text = update.message.text.strip()
        context.user_data[field] = text
        
        reply_markup = get_step_keyboard(skip_callback)
        await update.message.reply_text(prompt, reply_markup=reply_markup)
        return next_state
    return handler

def make_skip_callback(field: str, next_state: TGState, prompt: str, next_skip: Optional[str] = None):
    """Создать обработчик кнопки Пропустить"""
    async def handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
        query = update.callback_query
        await query.answer()
        
        context.user_data[field] = None
        
        reply_markup = get_step_keyboard(next_skip)
        await query.message.reply_text(prompt, reply_markup=reply_markup)
        return next_state
    return handler

def make_contact_handler():
    """Обработчик шага контактов (последний шаг перед превью)"""
    async def handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
        text = update.message.text.strip()
        context.user_data["contact"] = text
        return await show_tg_preview(update, context)
    return handler

async def show_tg_preview(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Показать предпросмотр и кнопки отправки"""
    form_type = context.user_data.get("form_type")
    form_data = context.user_data
    
    if form_type == "vacancy":
        text = build_vacancy_text(form_data)
        send_cb = "v_send"
        restart_cb = "v_restart"
        cancel_cb = "v_cancel"
        type_name = "вакансии"
        next_state = TGState.V_PREVIEW
    else:
        text = build_resume_text(form_data)
        send_cb = "r_send"
        restart_cb = "r_restart"
        cancel_cb = "r_cancel"
        type_name = "резюме"
        next_state = TGState.R_PREVIEW
    
    keyboard = InlineKeyboardMarkup([
        [InlineKeyboardButton("✅ Отправить", callback_data=send_cb)],
        [InlineKeyboardButton("✏️ Заполнить заново", callback_data=restart_cb)],
        [InlineKeyboardButton("❌ Отмена", callback_data=cancel_cb)],
    ])
    
    await update.message.reply_text(
        f"📌 Предпросмотр {type_name}:\n\n{text}",
        reply_markup=keyboard
    )
    return next_state

async def handle_tg_cancel(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Обработчик кнопки Отменить на любом шаге"""
    query = update.callback_query
    await query.answer()
    context.user_data.clear()
    await safe_edit(query, "❌ Создание отменено.")
    return ConversationHandler.END

async def handle_preview_callback(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Обработчик кнопок предпросмотра"""
    query = update.callback_query
    await query.answer()
    
    form_type = context.user_data.get("form_type")
    
    if query.data in ["v_cancel", "r_cancel"]:
        context.user_data.clear()
        await safe_edit(query, "❌ Создание отменено.")
        return ConversationHandler.END
    
    if query.data in ["v_restart", "r_restart"]:
        context.user_data.clear()
        context.user_data["form_type"] = form_type
        if form_type == "vacancy":
            await query.message.reply_text("📌 Название вакансии?", reply_markup=get_step_keyboard())
            return TGState.V_TITLE
        else:
            await query.message.reply_text("👤 Ваше имя?", reply_markup=get_step_keyboard())
            return TGState.R_NAME
    
    if query.data in ["v_send", "r_send"]:
        form_data = context.user_data
        
        if form_type == "vacancy":
            text = build_vacancy_text(form_data)
            prefix = "tg_vac"
        else:
            text = build_resume_text(form_data)
            prefix = "tg_res"
        
        user_id = str(query.from_user.id)
        pub_id = f"{prefix}_{query.message.message_id}_{user_id}"
        
        save_publication(pub_id, user_id, 'tg', text, form_type)
        
        mod_keyboard = InlineKeyboardMarkup([
            [InlineKeyboardButton("✅ Одобрить", callback_data=f"approve:{pub_id}"),
             InlineKeyboardButton("❌ Отклонить", callback_data=f"reject:{pub_id}")]
        ])
        try:
            await context.bot.send_message(
                MODERATION_GROUP_ID,
                f"📥 Новое объявление (из TG)\n\n{text}",
                reply_markup=mod_keyboard
            )
        except Exception as e:
            print(f"⚠️ Не удалось отправить на модерацию: {e}", flush=True)
            await query.message.reply_text("⚠️ Не удалось отправить на модерацию.")
            return ConversationHandler.END
        
        menu_keyboard = InlineKeyboardMarkup([
            [InlineKeyboardButton("➕ Создать ещё", callback_data=f"menu_{form_type}")],
            [InlineKeyboardButton("🏠 В главное меню", callback_data="main_menu")],
        ])
        await safe_edit(query, "✅ Отправлено на модерацию.", reply_markup=menu_keyboard)
        context.user_data.clear()
        return TGState.V_PREVIEW
    
    return ConversationHandler.END

# ============================================
# Telegram: Главное меню
# ============================================
async def main_menu_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Обработчик главного меню"""
    query = update.callback_query
    await query.answer()
    
    if query.data == "menu_vacancy":
        context.user_data["form_type"] = "vacancy"
        await query.message.reply_text("📌 Название вакансии?", reply_markup=get_step_keyboard())
        return TGState.V_TITLE
    elif query.data == "menu_resume":
        context.user_data["form_type"] = "resume"
        await query.message.reply_text("👤 Ваше имя?", reply_markup=get_step_keyboard())
        return TGState.R_NAME
    elif query.data == "my_publications":
        await my_publications_from_menu(update, context)
        return TGState.MAIN_MENU
    return ConversationHandler.END

async def my_publications_from_menu(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Показать активные публикации из меню"""
    query = update.callback_query
    user_id = str(query.from_user.id)
    publications = get_user_publications(user_id, platform='tg')
    
    if not publications:
        await query.message.reply_text("📭 У вас нет активных публикаций.")
        return
    
    total = len(publications)
    await query.message.reply_text(f"📊 Ваши активные публикации (всего: {total}):")
    
    for pub in publications[:10]:
        pub_type = "📌 Вакансия" if pub['type'] == 'vacancy' else "📄 Резюме"
        preview = pub['text'][:150] + "..." if len(pub['text']) > 150 else pub['text']
        
        status_emoji = {
            'pending': '⏳ На модерации',
            'approved': '✅ Опубликовано',
            'rejected': '❌ Отклонено',
            'deleted': '🗑 Удалено',
            'expired': '⏰ Истекло'
        }.get(pub['status'], '❓')
        
        keyboard = InlineKeyboardMarkup([
            [InlineKeyboardButton("🗑 Удалить", callback_data=f"delete:{pub['id']}")]
        ])
        
        await query.message.reply_text(
            f"{status_emoji} {pub_type} от {pub['created_at'][:10]}\n\n{preview}",
            reply_markup=keyboard
        )

async def back_to_main(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Возврат в главное меню"""
    query = update.callback_query
    await query.answer()
    context.user_data.clear()
    keyboard = InlineKeyboardMarkup([
        [InlineKeyboardButton("📌 Создать вакансию", callback_data="menu_vacancy")],
        [InlineKeyboardButton("📄 Создать резюме", callback_data="menu_resume")],
        [InlineKeyboardButton("📋 Мои публикации", callback_data="my_publications")],
    ])
    await query.message.reply_text("👋 Что вы хотите сделать?", reply_markup=keyboard)
    return TGState.MAIN_MENU

# ============================================
# Telegram: Модерация и уведомления
# ============================================
async def _notify_user(pub: dict, message: str, pub_id: Optional[str] = None):
    """Отправить уведомление пользователю (поддерживает TG и VK)"""
    
    if pub["platform"] == "tg":
        try:
            app = get_telegram_app()
            if app:
                keyboard = None
                if pub_id:
                    keyboard = InlineKeyboardMarkup([
                        [InlineKeyboardButton("🗑 Удалить", callback_data=f"delete:{pub_id}")]
                    ])
                await app.bot.send_message(
                    chat_id=int(pub["user_id"]),
                    text=message,
                    reply_markup=keyboard
                )
                print(f"✅ Уведомление отправлено в TG пользователю {pub['user_id']}", flush=True)
        except Exception as e:
            print(f"⚠️ Не удалось отправить уведомление в TG: {e}", flush=True)
    
    elif pub["platform"] == "vk":
        try:
            await send_vk_message(
                user_id=pub["user_id"],
                text=message
            )
            print(f"✅ Уведомление отправлено в VK пользователю {pub['user_id']}", flush=True)
        except Exception as e:
            print(f"⚠️ Не удалось отправить уведомление в VK: {e}", flush=True)

async def moderation_buttons(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Обработчик кнопок модерации"""
    query = update.callback_query
    await query.answer()
    try:
        action, pub_id = query.data.split(":")
    except ValueError:
        return
    
    pub = get_publication(pub_id)
    if not pub:
        await query.answer("⚠️ Публикация не найдена в базе данных", show_alert=True)
        await safe_edit(query, query.message.text + "\n\n⚠️ Публикация не найдена")
        return
    
    if action == "approve":
        success = await publish_to_both_platforms(pub_id, context)
        if success:
            await _notify_user(pub, "✅ Ваша публикация одобрена и опубликована!", pub_id)
            await safe_edit(query, query.message.text + "\n\n✅ Опубликовано в TG и VK")
        else:
            await query.answer("❌ Ошибка публикации", show_alert=True)
    
    elif action == "reject":
        update_publication_status(pub_id, 'rejected')
        await _notify_user(pub, "❌ Ваша публикация отклонена.")
        await safe_edit(query, query.message.text + "\n\n❌ Отклонено")

async def delete_publication(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Удаление публикации пользователем"""
    query = update.callback_query
    await query.answer()
    
    try:
        _, pub_id = query.data.split(":")
    except ValueError:
        return
    
    pub = get_publication(pub_id)
    if not pub:
        await query.answer("⚠️ Публикация не найдена.", show_alert=True)
        return
    
    if str(query.from_user.id) != pub["user_id"] and pub["platform"] == "tg":
        await query.answer("⛔ Это не ваша публикация.", show_alert=True)
        return
    
    if pub["status"] in ('deleted', 'expired'):
        await query.answer("⚠️ Публикация уже удалена.", show_alert=True)
        return
    
    await delete_from_both_platforms(pub_id, context)
    await safe_edit(query, f"🗑 Публикация удалена.\n\n{pub['text']}")
    try:
        await context.bot.send_message(
            MODERATION_GROUP_ID,
            f"🗑 Пользователь удалил публикацию:\n\n{pub['text']}\n\n❌ Удалено"
        )
    except Exception as e:
        print(f"⚠️ Не удалось уведомить модераторов: {e}", flush=True)

# ============================================
# Telegram: Бот-чистильщик
# ============================================
async def delete_system_messages(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Удалять системные сообщения в чате"""
    if not update.message:
        return
    if update.message.new_chat_members or update.message.left_chat_member:
        try:
            await update.message.delete()
            print(f"🧹 Удалено системное сообщение в чате {update.message.chat.title}", flush=True)
        except Exception as e:
            print(f"⚠️ Не удалось удалить системное сообщение: {e}", flush=True)

# ============================================
# VK: Отправка сообщений
# ============================================
async def send_vk_message(user_id: str, text: str, keyboard: Optional[Dict] = None):
    """Отправить сообщение в VK"""
    global vk_api_instance
    if not vk_api_instance:
        print(f"❌ VK: vk_api_instance не инициализирован!", flush=True)
        return
    
    try:
        params = {
            'user_id': int(user_id),
            'message': text,
            'random_id': get_random_id()
        }
        if keyboard:
            params['keyboard'] = json.dumps(keyboard, ensure_ascii=False)
        
        result = vk_api_instance.messages.send(**params)
        print(f"📤 VK: сообщение отправлено пользователю {user_id}, результат: {result}", flush=True)
    except Exception as e:
        print(f"❌ Ошибка отправки VK-сообщения: {e}", flush=True)
        traceback.print_exc()

# ============================================
# VK: Клавиатуры
# ============================================
def get_vk_main_keyboard():
    """Клавиатура главного меню VK"""
    return {
        "inline": True,
        "buttons": [
            [{"action": {"type": "text", "label": "📌 Создать вакансию", "payload": "{\"action\": \"vacancy\"}"}}],
            [{"action": {"type": "text", "label": "📄 Создать резюме", "payload": "{\"action\": \"resume\"}"}}],
            [{"action": {"type": "text", "label": "📋 Мои публикации", "payload": "{\"action\": \"my_publications\"}"}}]
        ]
    }

def get_vk_cancel_only_keyboard():
    """Клавиатура только с кнопкой Отменить (для обязательных полей)"""
    return {
        "inline": True,
        "buttons": [
            [{"action": {"type": "text", "label": "❌ Отменить", "payload": "{\"action\": \"cancel\"}"}}]
        ]
    }

def get_vk_skip_cancel_keyboard():
    """Клавиатура с кнопками Пропустить и Отменить (для необязательных полей)"""
    return {
        "inline": True,
        "buttons": [
            [
                {"action": {"type": "text", "label": "⏭ Пропустить", "payload": "{\"action\": \"skip\"}"}},
                {"action": {"type": "text", "label": "❌ Отменить", "payload": "{\"action\": \"cancel\"}"}}
            ]
        ]
    }

def get_vk_preview_keyboard():
    """Клавиатура предпросмотра"""
    return {
        "inline": True,
        "buttons": [
            [{"action": {"type": "text", "label": "✅ Отправить", "payload": "{\"action\": \"send\"}"}}],
            [{"action": {"type": "text", "label": "✏️ Заполнить заново", "payload": "{\"action\": \"restart\"}"}}],
            [{"action": {"type": "text", "label": "❌ Отменить", "payload": "{\"action\": \"cancel\"}"}}]
        ]
    }

# ============================================
# VK: Показ публикаций
# ============================================
async def show_vk_publications(user_id: str):
    """Показать активные публикации пользователя VK"""
    publications = get_user_publications(user_id, platform='vk')
    
    if not publications:
        await send_vk_message(user_id, "📭 У вас нет активных публикаций.", get_vk_main_keyboard())
        return
    
    total = len(publications)
    await send_vk_message(user_id, f"📊 Ваши активные публикации (всего: {total}):")
    
    for pub in publications[:10]:
        pub_type = "📌 Вакансия" if pub['type'] == 'vacancy' else "📄 Резюме"
        preview = pub['text'][:150] + "..." if len(pub['text']) > 150 else pub['text']
        
        status_emoji = {
            'pending': '⏳ На модерации',
            'approved': '✅ Опубликовано',
            'rejected': '❌ Отклонено',
            'deleted': '🗑 Удалено',
            'expired': '⏰ Истекло'
        }.get(pub['status'], '❓')
        
        if pub['status'] == 'approved':
            delete_keyboard = {
                "inline": True,
                "buttons": [
                    [{"action": {"type": "text", "label": f"🗑 Удалить эту публикацию", "payload": json.dumps({"action": "delete", "pub_id": pub['id']})}}]
                ]
            }
        else:
            delete_keyboard = None
        
        await send_vk_message(user_id, f"{status_emoji} {pub_type} от {pub['created_at'][:10]}\n\n{preview}", delete_keyboard)
    
    await send_vk_message(user_id, "👋 Что хотите сделать дальше?", get_vk_main_keyboard())

# ============================================
# VK: Главное меню
# ============================================
async def show_vk_main_menu(user_id: str):
    """Показать главное меню в VK"""
    clear_vk_state(user_id)
    await send_vk_message(user_id, "👋 Привет! Я бот для публикации вакансий и резюме.\n\nВыберите действие:", get_vk_main_keyboard())

# ============================================
# VK: Обработка удаления
# ============================================
async def handle_vk_delete(user_id: str, pub_id: str):
    """Обработка удаления публикации в VK"""
    pub = get_publication(pub_id)
    if not pub:
        await send_vk_message(user_id, "⚠️ Публикация не найдена.")
        return
    
    if str(user_id) != pub["user_id"]:
        await send_vk_message(user_id, "⛔ Это не ваша публикация.")
        return
    
    if pub["status"] in ('deleted', 'expired'):
        await send_vk_message(user_id, "⚠️ Публикация уже удалена.")
        return
    
    if pub['vk_message_id'] and pub['vk_chat_id']:
        try:
            if vk_api_instance:
                vk_api_instance.messages.delete(message_ids=[pub['vk_message_id']], delete_for_all=1)
        except Exception as e:
            print(f"⚠️ Не удалось удалить из VK: {e}", flush=True)
    
    app = get_telegram_app()
    if pub['tg_channel_message_id'] and app:
        try:
            await app.bot.delete_message(chat_id=CHANNEL_USERNAME, message_id=pub['tg_channel_message_id'])
        except Exception as e:
            print(f"⚠️ Не удалось удалить из Telegram: {e}", flush=True)
    
    delete_publication_from_db(pub_id)
    
    await send_vk_message(user_id, f"🗑 Публикация удалена.\n\n{pub['text'][:200]}...", get_vk_main_keyboard())
    
    if app:
        try:
            await app.bot.send_message(MODERATION_GROUP_ID, f"🗑 Пользователь VK удалил публикацию:\n\n{pub['text']}\n\n❌ Удалено")
        except Exception as e:
            print(f"⚠️ Не удалось уведомить модераторов: {e}", flush=True)

# ============================================
# VK: Машина состояний
# ============================================
async def handle_vk_step(user_id: str, state: str, data: dict, text: str) -> str:
    """Обработка шагов VK (машина состояний)"""
    
    print(f"🔍 VK: step user={user_id}, state={state}, text='{text[:80]}'", flush=True)
    
    try:
        payload_data = json.loads(text) if text.startswith('{') else {}
    except:
        payload_data = {}
    
    action = payload_data.get("action", "")
    
    # ----- Специальные команды -----
    if action == "delete":
        pub_id = payload_data.get("pub_id", "")
        if pub_id:
            await handle_vk_delete(user_id, pub_id)
        return VKState.MAIN_MENU
    
    if action == "my_publications" or text.lower() in ["мои публикации", "📋 мои публикации"]:
        clear_vk_state(user_id)
        await show_vk_publications(user_id)
        return VKState.MAIN_MENU
    
    if action == "cancel" or text.lower() in ["отменить", "отмена", "❌ отменить", "/cancel"]:
        clear_vk_state(user_id)
        await send_vk_message(user_id, "❌ Действие отменено.", get_vk_main_keyboard())
        return VKState.MAIN_MENU
    
    is_skip = (action == "skip" or text.lower() in ['пропустить', '⏭ пропустить'])
    
    def save_field(field_name: str):
        if is_skip:
            data[field_name] = None
        else:
            data[field_name] = text
    
    # ----- Главное меню -----
    if state == VKState.MAIN_MENU:
        if action == "vacancy" or 'ваканси' in text.lower():
            save_vk_state(user_id, VKState.V_TITLE, {})
            await send_vk_message(user_id, "📌 Название вакансии?", get_vk_cancel_only_keyboard())
            return VKState.V_TITLE
        elif action == "resume" or 'резюме' in text.lower():
            save_vk_state(user_id, VKState.R_NAME, {})
            await send_vk_message(user_id, "👤 Ваше имя?", get_vk_cancel_only_keyboard())
            return VKState.R_NAME
        else:
            await show_vk_main_menu(user_id)
            return VKState.MAIN_MENU
    
    # ========== ВАКАНСИЯ ==========
    if state == VKState.V_TITLE:
        if is_skip:
            await send_vk_message(user_id, "⚠️ Название обязательно. Введите название вакансии:", get_vk_cancel_only_keyboard())
            return VKState.V_TITLE
        data['title'] = text
        save_vk_state(user_id, VKState.V_COMPANY, data)
        await send_vk_message(user_id, "🏢 Компания?", get_vk_cancel_only_keyboard())
        return VKState.V_COMPANY
    
    if state == VKState.V_COMPANY:
        if is_skip:
            await send_vk_message(user_id, "⚠️ Компания обязательна. Введите название компании:", get_vk_cancel_only_keyboard())
            return VKState.V_COMPANY
        data['company'] = text
        save_vk_state(user_id, VKState.V_SALARY, data)
        await send_vk_message(user_id, "💰 Зарплата?", get_vk_skip_cancel_keyboard())
        return VKState.V_SALARY
    
    if state == VKState.V_SALARY:
        save_field("salary")
        save_vk_state(user_id, VKState.V_SCHEDULE, data)
        await send_vk_message(user_id, "🕒 График работы?", get_vk_skip_cancel_keyboard())
        return VKState.V_SCHEDULE
    
    if state == VKState.V_SCHEDULE:
        save_field("schedule")
        save_vk_state(user_id, VKState.V_DESCRIPTION, data)
        await send_vk_message(user_id, "📋 Описание вакансии?", get_vk_skip_cancel_keyboard())
        return VKState.V_DESCRIPTION
    
    if state == VKState.V_DESCRIPTION:
        save_field("description")
        save_vk_state(user_id, VKState.V_CONTACT, data)
        await send_vk_message(user_id, "📞 Контакты для связи?", get_vk_cancel_only_keyboard())
        return VKState.V_CONTACT
    
    if state == VKState.V_CONTACT:
        if is_skip:
            await send_vk_message(user_id, "⚠️ Контакты обязательны. Укажите контакты:", get_vk_cancel_only_keyboard())
            return VKState.V_CONTACT
        data['contact'] = text
        preview = build_vacancy_text(data)
        save_vk_state(user_id, VKState.V_PREVIEW, data)
        await send_vk_message(user_id, f"📌 Предпросмотр вакансии:\n\n{preview}\n\nВыберите действие:", get_vk_preview_keyboard())
        return VKState.V_PREVIEW
    
    if state == VKState.V_PREVIEW:
        if action == "send" or text.upper() == 'ОТПРАВИТЬ' or text == '✅ Отправить':
            pub_id = f"vk_vac_{user_id}_{int(datetime.now().timestamp())}"
            full_text = build_vacancy_text(data)
            
            save_publication(pub_id, user_id, 'vk', full_text, 'vacancy')
            
            mod_keyboard = InlineKeyboardMarkup([
                [InlineKeyboardButton("✅ Одобрить", callback_data=f"approve:{pub_id}"),
                 InlineKeyboardButton("❌ Отклонить", callback_data=f"reject:{pub_id}")]
            ])
            
            app = get_telegram_app()
            if app:
                try:
                    await app.bot.send_message(MODERATION_GROUP_ID, f"📥 Новая вакансия (из VK)\n\n{full_text}", reply_markup=mod_keyboard)
                except Exception as e:
                    print(f"⚠️ Не удалось отправить на модерацию: {e}", flush=True)
            
            await send_vk_message(user_id, "✅ Ваша вакансия отправлена на модерацию!", get_vk_main_keyboard())
            clear_vk_state(user_id)
            return VKState.MAIN_MENU
        elif action == "restart" or text == '✏️ Заполнить заново':
            save_vk_state(user_id, VKState.V_TITLE, {})
            await send_vk_message(user_id, "📌 Название вакансии?", get_vk_cancel_only_keyboard())
            return VKState.V_TITLE
        else:
            await send_vk_message(user_id, "Используйте кнопки для выбора действия.", get_vk_preview_keyboard())
            return VKState.V_PREVIEW
    
    # ========== РЕЗЮМЕ ==========
    if state == VKState.R_NAME:
        if is_skip:
            await send_vk_message(user_id, "⚠️ Имя обязательно. Введите ваше имя:", get_vk_cancel_only_keyboard())
            return VKState.R_NAME
        data['name'] = text
        save_vk_state(user_id, VKState.R_AGE, data)
        await send_vk_message(user_id, "🎂 Ваш возраст?", get_vk_skip_cancel_keyboard())
        return VKState.R_AGE
    
    if state == VKState.R_AGE:
        save_field("age")
        save_vk_state(user_id, VKState.R_POSITION, data)
        await send_vk_message(user_id, "💼 Желаемая должность?", get_vk_skip_cancel_keyboard())
        return VKState.R_POSITION
    
    if state == VKState.R_POSITION:
        save_field("position")
        save_vk_state(user_id, VKState.R_EXPERIENCE, data)
        await send_vk_message(user_id, "📅 Опыт работы?", get_vk_skip_cancel_keyboard())
        return VKState.R_EXPERIENCE
    
    if state == VKState.R_EXPERIENCE:
        save_field("experience")
        save_vk_state(user_id, VKState.R_SKILLS, data)
        await send_vk_message(user_id, "🛠 Ключевые навыки?", get_vk_skip_cancel_keyboard())
        return VKState.R_SKILLS
    
    if state == VKState.R_SKILLS:
        save_field("skills")
        save_vk_state(user_id, VKState.R_EDUCATION, data)
        await send_vk_message(user_id, "🎓 Образование?", get_vk_skip_cancel_keyboard())
        return VKState.R_EDUCATION
    
    if state == VKState.R_EDUCATION:
        save_field("education")
        save_vk_state(user_id, VKState.R_CONTACT, data)
        await send_vk_message(user_id, "📞 Контакты для связи?", get_vk_cancel_only_keyboard())
        return VKState.R_CONTACT
    
    if state == VKState.R_CONTACT:
        if is_skip:
            await send_vk_message(user_id, "⚠️ Контакты обязательны. Укажите контакты:", get_vk_cancel_only_keyboard())
            return VKState.R_CONTACT
        data['contact'] = text
        preview = build_resume_text(data)
        save_vk_state(user_id, VKState.R_PREVIEW, data)
        await send_vk_message(user_id, f"📄 Предпросмотр резюме:\n\n{preview}\n\nВыберите действие:", get_vk_preview_keyboard())
        return VKState.R_PREVIEW
    
    if state == VKState.R_PREVIEW:
        if action == "send" or text.upper() == 'ОТПРАВИТЬ' or text == '✅ Отправить':
            pub_id = f"vk_res_{user_id}_{int(datetime.now().timestamp())}"
            full_text = build_resume_text(data)
            
            save_publication(pub_id, user_id, 'vk', full_text, 'resume')
            
            mod_keyboard = InlineKeyboardMarkup([
                [InlineKeyboardButton("✅ Одобрить", callback_data=f"approve:{pub_id}"),
                 InlineKeyboardButton("❌ Отклонить", callback_data=f"reject:{pub_id}")]
            ])
            
            app = get_telegram_app()
            if app:
                try:
                    await app.bot.send_message(MODERATION_GROUP_ID, f"📥 Новое резюме (из VK)\n\n{full_text}", reply_markup=mod_keyboard)
                except Exception as e:
                    print(f"⚠️ Не удалось отправить на модерацию: {e}", flush=True)
            
            await send_vk_message(user_id, "✅ Ваше резюме отправлено на модерацию!", get_vk_main_keyboard())
            clear_vk_state(user_id)
            return VKState.MAIN_MENU
        elif action == "restart" or text == '✏️ Заполнить заново':
            save_vk_state(user_id, VKState.R_NAME, {})
            await send_vk_message(user_id, "👤 Ваше имя?", get_vk_cancel_only_keyboard())
            return VKState.R_NAME
        else:
            await send_vk_message(user_id, "Используйте кнопки для выбора действия.", get_vk_preview_keyboard())
            return VKState.R_PREVIEW
    
    # Сброс при неизвестном состоянии
    clear_vk_state(user_id)
    await show_vk_main_menu(user_id)
    return VKState.MAIN_MENU

# ============================================
# VK: Слушатель LongPoll (с авто-восстановлением)
# ============================================
async def vk_listener_async():
    """Фоновый слушатель VK LongPoll с автоматическим восстановлением при ошибках"""
    global vk_api_instance, vk_longpoll
    
    if not VK_TOKEN or VK_GROUP_ID == 0:
        print("⚠️ VK_TOKEN или VK_GROUP_ID не заданы — VK-часть отключена", flush=True)
        return
    
    while True:
        try:
            vk_session = vk_api.VkApi(token=VK_TOKEN)
            vk_api_instance = vk_session.get_api()
            
            group_info = vk_api_instance.groups.getById()
            print(f"🔵 VK: подключен к группе '{group_info[0]['name']}' (ID: {group_info[0]['id']})", flush=True)
            
            vk_longpoll = VkBotLongPoll(vk_session, VK_GROUP_ID)
            print("🔵 VK LongPoll запущен, ожидание сообщений...", flush=True)
            
            for event in vk_longpoll.listen():
                try:
                    if event.type == VkBotEventType.MESSAGE_NEW:
                        msg = event.message
                        user_id = str(msg['from_id'])
                        text = msg.get('text', '').strip()
                        payload = msg.get('payload', '')
                        
                        if user_id.startswith('-'):
                            continue
                        
                        if payload:
                            try:
                                payload_data = json.loads(payload)
                                action = payload_data.get("action", "")
                                
                                if action == "delete":
                                    pub_id = payload_data.get("pub_id", "")
                                    if pub_id:
                                        await handle_vk_delete(user_id, pub_id)
                                        continue
                                
                                if action in ["skip", "cancel", "send", "restart", "vacancy", "resume", "my_publications"]:
                                    text = json.dumps(payload_data)
                            except:
                                pass
                        
                        if text == '/start':
                            await show_vk_main_menu(user_id)
                            continue
                        
                        state, data = get_vk_state(user_id)
                        await handle_vk_step(user_id, state, data, text)
                            
                except Exception as e:
                    print(f"⚠️ Ошибка в VK-обработчике: {e}", flush=True)
                    traceback.print_exc()
                    
        except Exception as e:
            print(f"❌ VK LongPoll упал с ошибкой: {e}", flush=True)
            traceback.print_exc()
            print("🔄 Перезапуск VK LongPoll через 10 секунд...", flush=True)
            await asyncio.sleep(10)

def start_vk_listener():
    """Запуск VK-слушателя в отдельном потоке"""
    loop = asyncio.new_event_loop()
    asyncio.set_event_loop(loop)
    loop.run_until_complete(vk_listener_async())

# ============================================
# Фоновый планировщик
# ============================================
async def maintenance_scheduler():
    """Плановое обслуживание БД (запускается раз в час)"""
    while True:
        try:
            await asyncio.sleep(3600)
            
            archived = archive_old_publications()
            rejected = auto_reject_stale_publications()
            cleaned = cleanup_old_vk_states()
            
            if archived > 0 or rejected > 0 or cleaned > 0:
                print(f"✅ Обслуживание: заархивировано {archived}, отклонено {rejected}, очищено состояний VK {cleaned}", flush=True)
                
        except Exception as e:
            print(f"⚠️ Ошибка в обслуживании: {e}", flush=True)
            await asyncio.sleep(60)

# ============================================
# Главная функция запуска
# ============================================
async def main():
    global _telegram_app
    
    if not VACANCY_BOT_TOKEN:
        raise ValueError("❌ VACANCY_BOT_TOKEN не задан!")
    if MODERATION_GROUP_ID == 0:
        print("⚠️ MODERATION_GROUP_ID не задан!", flush=True)
    
    init_database()
    threading.Thread(target=run_server, daemon=True).start()
    
    # --- Telegram ---
    telegram_app = Application.builder().token(VACANCY_BOT_TOKEN).build()
    _telegram_app = telegram_app
    
    # Глобальный /start — работает всегда, даже вне диалога
    telegram_app.add_handler(CommandHandler("start", global_start))
    
    conv_handler = ConversationHandler(
        entry_points=[],
        states={
            TGState.MAIN_MENU: [
                CallbackQueryHandler(main_menu_handler, pattern="^menu_"),
                CallbackQueryHandler(my_publications_from_menu, pattern="^my_publications$"),
            ],
            # Вакансия
            TGState.V_TITLE: [
                MessageHandler(filters.TEXT & ~filters.COMMAND, make_step_handler("title", TGState.V_COMPANY, "🏢 Компания?")),
                CallbackQueryHandler(handle_tg_cancel, pattern="^cancel_action$")
            ],
            TGState.V_COMPANY: [
                MessageHandler(filters.TEXT & ~filters.COMMAND, make_step_handler("company", TGState.V_SALARY, "💰 Зарплата?", "v_skip_salary")),
                CallbackQueryHandler(handle_tg_cancel, pattern="^cancel_action$")
            ],
            TGState.V_SALARY: [
                MessageHandler(filters.TEXT & ~filters.COMMAND, make_step_handler("salary", TGState.V_SCHEDULE, "🕒 График?", "v_skip_schedule")),
                CallbackQueryHandler(make_skip_callback("salary", TGState.V_SCHEDULE, "🕒 График?", "v_skip_schedule"), pattern="^v_skip_salary$"),
                CallbackQueryHandler(handle_tg_cancel, pattern="^cancel_action$")
            ],
            TGState.V_SCHEDULE: [
                MessageHandler(filters.TEXT & ~filters.COMMAND, make_step_handler("schedule", TGState.V_DESCRIPTION, "📋 Описание?", "v_skip_description")),
                CallbackQueryHandler(make_skip_callback("schedule", TGState.V_DESCRIPTION, "📋 Описание?", "v_skip_description"), pattern="^v_skip_schedule$"),
                CallbackQueryHandler(handle_tg_cancel, pattern="^cancel_action$")
            ],
            TGState.V_DESCRIPTION: [
                MessageHandler(filters.TEXT & ~filters.COMMAND, make_step_handler("description", TGState.V_CONTACT, "📞 Контакты?")),
                CallbackQueryHandler(make_skip_callback("description", TGState.V_CONTACT, "📞 Контакты?"), pattern="^v_skip_description$"),
                CallbackQueryHandler(handle_tg_cancel, pattern="^cancel_action$")
            ],
            TGState.V_CONTACT: [
                MessageHandler(filters.TEXT & ~filters.COMMAND, make_contact_handler()),
                CallbackQueryHandler(handle_tg_cancel, pattern="^cancel_action$")
            ],
            TGState.V_PREVIEW: [
                CallbackQueryHandler(handle_preview_callback, pattern="^(v_send|v_cancel|v_restart)$"),
            ],
            # Резюме
            TGState.R_NAME: [
                MessageHandler(filters.TEXT & ~filters.COMMAND, make_step_handler("name", TGState.R_AGE, "🎂 Возраст?", "r_skip_age")),
                CallbackQueryHandler(handle_tg_cancel, pattern="^cancel_action$")
            ],
            TGState.R_AGE: [
                MessageHandler(filters.TEXT & ~filters.COMMAND, make_step_handler("age", TGState.R_POSITION, "💼 Желаемая должность?", "r_skip_position")),
                CallbackQueryHandler(make_skip_callback("age", TGState.R_POSITION, "💼 Желаемая должность?", "r_skip_position"), pattern="^r_skip_age$"),
                CallbackQueryHandler(handle_tg_cancel, pattern="^cancel_action$")
            ],
            TGState.R_POSITION: [
                MessageHandler(filters.TEXT & ~filters.COMMAND, make_step_handler("position", TGState.R_EXPERIENCE, "📅 Опыт работы?", "r_skip_experience")),
                CallbackQueryHandler(make_skip_callback("position", TGState.R_EXPERIENCE, "📅 Опыт работы?", "r_skip_experience"), pattern="^r_skip_position$"),
                CallbackQueryHandler(handle_tg_cancel, pattern="^cancel_action$")
            ],
            TGState.R_EXPERIENCE: [
                MessageHandler(filters.TEXT & ~filters.COMMAND, make_step_handler("experience", TGState.R_SKILLS, "🛠 Навыки?", "r_skip_skills")),
                CallbackQueryHandler(make_skip_callback("experience", TGState.R_SKILLS, "🛠 Навыки?", "r_skip_skills"), pattern="^r_skip_experience$"),
                CallbackQueryHandler(handle_tg_cancel, pattern="^cancel_action$")
            ],
            TGState.R_SKILLS: [
                MessageHandler(filters.TEXT & ~filters.COMMAND, make_step_handler("skills", TGState.R_EDUCATION, "🎓 Образование?", "r_skip_education")),
                CallbackQueryHandler(make_skip_callback("skills", TGState.R_EDUCATION, "🎓 Образование?", "r_skip_education"), pattern="^r_skip_skills$"),
                CallbackQueryHandler(handle_tg_cancel, pattern="^cancel_action$")
            ],
            TGState.R_EDUCATION: [
                MessageHandler(filters.TEXT & ~filters.COMMAND, make_step_handler("education", TGState.R_CONTACT, "📞 Контакты?")),
                CallbackQueryHandler(make_skip_callback("education", TGState.R_CONTACT, "📞 Контакты?"), pattern="^r_skip_education$"),
                CallbackQueryHandler(handle_tg_cancel, pattern="^cancel_action$")
            ],
            TGState.R_CONTACT: [
                MessageHandler(filters.TEXT & ~filters.COMMAND, make_contact_handler()),
                CallbackQueryHandler(handle_tg_cancel, pattern="^cancel_action$")
            ],
            TGState.R_PREVIEW: [
                CallbackQueryHandler(handle_preview_callback, pattern="^(r_send|r_cancel|r_restart)$"),
            ],
        },
        fallbacks=[
            CommandHandler("start", start),
            CallbackQueryHandler(back_to_main, pattern="^main_menu$"),
            CallbackQueryHandler(main_menu_handler, pattern="^menu_"),
            CallbackQueryHandler(my_publications_from_menu, pattern="^my_publications$"),
        ],
        per_message=False
    )
    
    telegram_app.add_handler(conv_handler)
    telegram_app.add_handler(CommandHandler("my", my_publications))
    telegram_app.add_handler(CommandHandler("adminstats", admin_stats))
    telegram_app.add_handler(CommandHandler("export", admin_export))
    telegram_app.add_handler(CallbackQueryHandler(moderation_buttons, pattern="^(approve|reject):"))
    telegram_app.add_handler(CallbackQueryHandler(delete_publication, pattern="^delete:"))
    
    # Бот-чистильщик
    if CLEANER_BOT_TOKEN:
        try:
            cleaner_app = Application.builder().token(CLEANER_BOT_TOKEN).build()
            cleaner_app.add_handler(
                MessageHandler(
                    filters.StatusUpdate.NEW_CHAT_MEMBERS | filters.StatusUpdate.LEFT_CHAT_MEMBER,
                    delete_system_messages,
                )
            )
            await cleaner_app.initialize()
            await cleaner_app.start()
            await cleaner_app.updater.start_polling(drop_pending_updates=True)
            print("🧹 Бот-чистильщик запущен", flush=True)
        except Exception as e:
            print(f"⚠️ Не удалось запустить бота-чистильщика: {e}", flush=True)
    
    # --- VK ---
    if VK_TOKEN and VK_GROUP_ID != 0:
        vk_thread = threading.Thread(target=start_vk_listener, daemon=True)
        vk_thread.start()
        print("📱 VK: поток запущен", flush=True)
    else:
        print("⚠️ VK отключен", flush=True)
    
    asyncio.create_task(maintenance_scheduler())
    
    await telegram_app.initialize()
    await telegram_app.start()
    await telegram_app.updater.start_polling(drop_pending_updates=True)
    
    print("=" * 50, flush=True)
    print("🎉 БОТ ЗАПУЩЕН!", flush=True)
    print(f"📱 Telegram: активен", flush=True)
    print(f"📱 VK: {'активен' if (VK_TOKEN and VK_GROUP_ID != 0) else 'отключён'}", flush=True)
    print("=" * 50, flush=True)
    sys.stdout.flush()
    
    await asyncio.Event().wait()


if __name__ == "__main__":
    asyncio.run(main())