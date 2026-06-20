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
import hashlib
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
# ГЛОБАЛЬНЫЕ ПЕРЕМЕННЫЕ
# ============================================
vk_api_instance = None
vk_longpoll = None
_telegram_app = None
CHANNEL_CHAT_ID = None

# ============================================
# ДИАГНОСТИКА
# ============================================
print("=" * 60, flush=True)
print("🚀 ЗАПУСК БОТА...", flush=True)
print(f"Python версия: {sys.version}", flush=True)
print(f"VK_TOKEN задан: {bool(VK_TOKEN)}", flush=True)
print(f"VK_GROUP_ID: {VK_GROUP_ID}", flush=True)
print(f"VK_CHAT_VACANCIES: {VK_CHAT_VACANCIES}", flush=True)
print(f"VK_CHAT_RESUMES: {VK_CHAT_RESUMES}", flush=True)
print(f"CHANNEL_USERNAME: {CHANNEL_USERNAME}", flush=True)
print(f"MODERATION_GROUP_ID: {MODERATION_GROUP_ID}", flush=True)
print(f"ADMIN_IDS: {ADMIN_IDS}", flush=True)
print("=" * 60, flush=True)
sys.stdout.flush()

DEBUG_CALLBACKS = True

# ============================================
# ДЕКОРАТОРЫ
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
# РАБОТА С БД
# ============================================
def get_db_connection():
    conn = sqlite3.connect(DB_NAME)
    conn.row_factory = sqlite3.Row
    return conn

def init_database():
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

def save_publication(pub_id: str, user_id: str, platform: str, text: str, pub_type: str, status: str = 'pending'):
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
    """Обновляет статус публикации и сохраняет ID сообщений"""
    conn = get_db_connection()
    cursor = conn.cursor()
    
    # СОХРАНЯЕМ ВСЁ, даже если vk_message_id = None
    if tg_channel_message_id is not None:
        cursor.execute("""
            UPDATE publications 
            SET status = ?, 
                tg_channel_message_id = ?, 
                vk_message_id = ?, 
                vk_chat_id = ?, 
                moderated_at = CURRENT_TIMESTAMP
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
    conn = get_db_connection()
    cursor = conn.cursor()
    cursor.execute("SELECT * FROM publications WHERE id = ?", (pub_id,))
    row = cursor.fetchone()
    conn.close()
    return dict(row) if row else None

def get_user_publications(user_id: str, platform: Optional[str] = None, status: Optional[str] = None) -> list:
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
    conn = get_db_connection()
    cursor = conn.cursor()
    cursor.execute("UPDATE publications SET status = 'deleted' WHERE id = ?", (pub_id,))
    conn.commit()
    conn.close()

def archive_old_publications() -> int:
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

def get_vk_state(user_id: str) -> tuple:
    conn = get_db_connection()
    cursor = conn.cursor()
    cursor.execute("SELECT state, data FROM vk_user_states WHERE user_id = ?", (user_id,))
    row = cursor.fetchone()
    conn.close()
    if row:
        return row['state'], json.loads(row['data']) if row['data'] else {}
    return VKState.MAIN_MENU, {}

def save_vk_state(user_id: str, state: str, data: Optional[Dict] = None):
    conn = get_db_connection()
    cursor = conn.cursor()
    cursor.execute("""
        INSERT OR REPLACE INTO vk_user_states (user_id, state, data, updated_at)
        VALUES (?, ?, ?, CURRENT_TIMESTAMP)
    """, (user_id, state, json.dumps(data) if data else '{}'))
    conn.commit()
    conn.close()

def clear_vk_state(user_id: str):
    conn = get_db_connection()
    cursor = conn.cursor()
    cursor.execute("DELETE FROM vk_user_states WHERE user_id = ?", (user_id,))
    conn.commit()
    conn.close()

# ============================================
# ВСПОМОГАТЕЛЬНЫЕ ФУНКЦИИ
# ============================================
def build_vacancy_text(data: dict) -> str:
    lines = []
    if data.get("title"): lines.append(f"📌 {data['title']}")
    if data.get("company"): lines.append(f"🏢 {data['company']}")
    if data.get("salary"): lines.append(f"💰 {data['salary']}")
    if data.get("schedule"): lines.append(f"🕒 {data['schedule']}")
    if data.get("description"): lines.append(f"📋 {data['description']}")
    if data.get("contact"): lines.append(f"📞 {data['contact']}")
    return "\n\n".join(lines) if lines else "⚠️ Данные не заполнены"

def build_resume_text(data: dict) -> str:
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
    try:
        await query.edit_message_text(text, **kwargs)
    except Exception as e:
        print(f"⚠️ Не удалось отредактировать сообщение: {e}", flush=True)
        try:
            await query.message.reply_text(text, **kwargs)
        except Exception as e2:
            print(f"⚠️ Не удалось отправить новое сообщение: {e2}", flush=True)

def get_telegram_app():
    return _telegram_app

def create_callback_data(action: str, pub_id: str) -> str:
    data = f"{action}:{pub_id}"
    if len(data.encode('utf-8')) > 64:
        short_id = hashlib.md5(pub_id.encode()).hexdigest()[:16]
        data = f"{action}:{short_id}"
        print(f"⚠️ Callback data сокращена: {len(data.encode('utf-8'))} байт", flush=True)
    return data

# ============================================
# TELEGRAM: КЛАВИАТУРЫ
# ============================================
def get_step_keyboard(skip_callback: Optional[str] = None):
    buttons = []
    if skip_callback:
        buttons.append([InlineKeyboardButton("⏭ Пропустить", callback_data=skip_callback)])
    buttons.append([InlineKeyboardButton("❌ Отменить", callback_data="cancel_action")])
    return InlineKeyboardMarkup(buttons)

# ============================================
# ПУБЛИКАЦИЯ И УДАЛЕНИЕ
# ============================================
@retry_on_exception(tries=2, delay=2)
async def _publish_to_telegram(context, text: str, thread_id: int):
    sent = await context.bot.send_message(
        chat_id=CHANNEL_USERNAME,
        text=text,
        message_thread_id=thread_id
    )
    return sent.message_id

@retry_on_exception(tries=2, delay=2)
async def _publish_to_vk(text: str, peer_id: int):
    """Публикация в VK - peer_id уже готовый"""
    global vk_api_instance
    if not vk_api_instance:
        print("❌ VK: vk_api_instance не инициализирован!", flush=True)
        return None
    
    try:
    print(f"📤 VK: отправка в peer_id={peer_id}, длина текста={len(text)}", flush=True)

    # ПРОВЕРКА ДОСТУПА К БЕСЕДЕ
    try:
        conv = vk_api_instance.messages.getConversationsById(
            peer_ids=peer_id
        )

        print("🔍 CONVERSATION INFO:", flush=True)
        print(conv, flush=True)

    except Exception as e:
        print(f"❌ НЕ МОГУ ПОЛУЧИТЬ ИНФОРМАЦИЮ О БЕСЕДЕ: {e}", flush=True)

    result = vk_api_instance.messages.send(
        peer_id=peer_id,
        message=text,
        random_id=get_random_id()
    )
        print(f"📤 VK: результат: {result}", flush=True)
        return result
    except Exception as e:
        print(f"❌ VK: ошибка отправки: {e}", flush=True)
        traceback.print_exc()
        return None

async def publish_to_both_platforms(pub_id: str, context) -> bool:
    """Публикует одобренную заявку в Telegram и VK"""
    global CHANNEL_CHAT_ID
    
    print("=" * 60, flush=True)
    print(f"📢📢📢 ПУБЛИКАЦИЯ: {pub_id} 📢📢📢", flush=True)
    print("=" * 60, flush=True)
    
    pub = get_publication(pub_id)
    if not pub:
        print(f"❌ Публикация {pub_id} не найдена", flush=True)
        return False
    
    text = pub['text']
    pub_type = pub['type']
    
    print(f"📢 Публикация {pub_id}: тип={pub_type}, платформа={pub['platform']}", flush=True)
    
    if pub_type == 'vacancy':
        tg_thread_id = VACANCY_THREAD_ID
        vk_peer_id = VK_CHAT_VACANCIES
    else:
        tg_thread_id = RESUME_THREAD_ID
        vk_peer_id = VK_CHAT_RESUMES
    
    print(f"📢 TG thread: {tg_thread_id}, VK peer_id: {vk_peer_id}", flush=True)
    
    tg_message_id = None
    vk_message_id = None
    
    # Публикация в Telegram
    try:
        tg_message_id = await _publish_to_telegram(context, text, tg_thread_id)
        print(f"✅ Опубликовано в Telegram: {pub_id} (msg_id={tg_message_id})", flush=True)
    except Exception as e:
        print(f"❌ Ошибка публикации в Telegram: {e}", flush=True)
        traceback.print_exc()
        return False
    
    # Публикация в VK (не критично, если не получится)
    try:
        vk_result = await _publish_to_vk(text, vk_peer_id)
        if vk_result:
            if isinstance(vk_result, int):
                vk_message_id = vk_result
            elif isinstance(vk_result, dict):
                vk_message_id = vk_result.get('message_id') or vk_result.get('response')
            print(f"✅ Опубликовано в VK (peer_id={vk_peer_id}): {pub_id} (msg_id={vk_message_id})", flush=True)
        else:
            print(f"⚠️ VK вернул пустой результат для {pub_id}", flush=True)
    except Exception as e:
        print(f"⚠️ Ошибка публикации в VK: {e}", flush=True)
        traceback.print_exc()
    
    # ===== СОХРАНЕНИЕ В БД (ИСПРАВЛЕНО!) =====
    print(f"💾 СОХРАНЯЮ В БД:", flush=True)
    print(f"   pub_id: {pub_id}", flush=True)
    print(f"   tg_channel_message_id: {tg_message_id}", flush=True)
    print(f"   vk_message_id: {vk_message_id}", flush=True)
    print(f"   vk_chat_id: {vk_peer_id}", flush=True)
    
    # Используем исправленную функцию update_publication_status
    update_publication_status(
        pub_id,
        'approved',
        tg_channel_message_id=tg_message_id,
        vk_message_id=vk_message_id,
        vk_chat_id=vk_peer_id
    )
    
    # ===== ПРОВЕРКА СОХРАНЕНИЯ =====
    pub_check = get_publication(pub_id)
    print(f"🔍 ПРОВЕРКА СОХРАНЕНИЯ {pub_id}:", flush=True)
    print(f"   tg_channel_message_id: {pub_check.get('tg_channel_message_id')}", flush=True)
    print(f"   vk_message_id: {pub_check.get('vk_message_id')}", flush=True)
    print(f"   vk_chat_id: {pub_check.get('vk_chat_id')}", flush=True)
    print(f"   status: {pub_check.get('status')}", flush=True)
    print("=" * 60, flush=True)
    
    if pub_check.get('tg_channel_message_id') is None:
        print("❌❌❌ ОШИБКА: tg_channel_message_id НЕ СОХРАНИЛСЯ!", flush=True)
    else:
        print("✅ tg_channel_message_id СОХРАНИЛСЯ!", flush=True)
    
    return True

async def delete_from_both_platforms(pub_id: str, context) -> bool:
    """Удаляет публикацию из Telegram и VK"""
    global CHANNEL_CHAT_ID
    
    print("=" * 60, flush=True)
    print(f"🗑🗑🗑 УДАЛЕНИЕ: {pub_id} 🗑🗑🗑", flush=True)
    print("=" * 60, flush=True)
    
    pub = get_publication(pub_id)
    if not pub:
        print(f"❌ DELETE: публикация {pub_id} не найдена", flush=True)
        return False
    
    # ДИАГНОСТИКА
    print(f"🔍 DELETE ДАННЫЕ:", flush=True)
    print(f"   ID: {pub.get('id')}", flush=True)
    print(f"   TG msg_id: {pub.get('tg_channel_message_id')}", flush=True)
    print(f"   VK msg_id: {pub.get('vk_message_id')}", flush=True)
    print(f"   VK peer_id: {pub.get('vk_chat_id')}", flush=True)
    print(f"   Status: {pub.get('status')}", flush=True)
    print(f"   Platform: {pub.get('platform')}", flush=True)
    
    success = True
    
    # ===== УДАЛЕНИЕ ИЗ TELEGRAM =====
    if pub.get('tg_channel_message_id'):
        try:
            if CHANNEL_CHAT_ID is None:
                chat = await context.bot.get_chat(CHANNEL_USERNAME)
                CHANNEL_CHAT_ID = chat.id
                print(f"📢 ID канала получен: {CHANNEL_CHAT_ID}", flush=True)
            
            print(f"🗑 TG: удаляю msg_id={pub['tg_channel_message_id']} из chat_id={CHANNEL_CHAT_ID}", flush=True)
            
            await context.bot.delete_message(
                chat_id=CHANNEL_CHAT_ID,
                message_id=pub['tg_channel_message_id']
            )
            print(f"✅ Удалено из Telegram: {pub['tg_channel_message_id']}", flush=True)
        except Exception as e:
            print(f"❌ ОШИБКА удаления из Telegram: {e}", flush=True)
            print(f"❌ Тип ошибки: {type(e).__name__}", flush=True)
            traceback.print_exc()
            success = False
    else:
        print(f"⚠️ Нет tg_channel_message_id для удаления", flush=True)
        success = False
    
    # ===== УДАЛЕНИЕ ИЗ VK =====
    if pub.get('vk_message_id') and pub.get('vk_chat_id'):
        try:
            if vk_api_instance:
                peer_id = pub['vk_chat_id']
                print(f"🗑 VK: удаляю msg_id={pub['vk_message_id']} из peer_id={peer_id}", flush=True)
                
                result = vk_api_instance.messages.delete(
                    message_ids=[pub['vk_message_id']],
                    peer_id=peer_id,
                    delete_for_all=1
                )
                print(f"✅ Удалено из VK: {result}", flush=True)
            else:
                print(f"⚠️ VK API не инициализирован", flush=True)
                success = False
        except Exception as e:
            print(f"❌ ОШИБКА удаления из VK: {e}", flush=True)
            print(f"❌ Тип ошибки: {type(e).__name__}", flush=True)
            traceback.print_exc()
            success = False
    else:
        print(f"⚠️ Нет данных VK для удаления", flush=True)
        if not pub.get('vk_message_id'):
            print(f"   vk_message_id = None", flush=True)
        if not pub.get('vk_chat_id'):
            print(f"   vk_chat_id = None", flush=True)
    
    if success:
        delete_publication_from_db(pub_id)
        print(f"🗑 Публикация {pub_id} помечена как удаленная", flush=True)
    else:
        print(f"⚠️ Удаление не полностью успешно, но статус всё равно меняем", flush=True)
        delete_publication_from_db(pub_id)
    
    print(f"🔍 DELETE ЗАВЕРШЕН: success={success}", flush=True)
    print("=" * 60, flush=True)
    return success

# ============================================
# HTTP-СЕРВЕР
# ============================================
class SimpleHandler(BaseHTTPRequestHandler):
    def do_GET(self):
        if self.path == '/health':
            self.send_response(200)
            self.send_header('Content-type', 'application/json')
            self.end_headers()
            health = {
                'status': 'healthy',
                'telegram': _telegram_app is not None,
                'vk': vk_api_instance is not None
            }
            self.wfile.write(json.dumps(health).encode())
        else:
            self.send_response(200)
            self.send_header("Content-type", "text/plain")
            self.end_headers()
            self.wfile.write(b"Bots are running")
    
    def do_HEAD(self):
        self.send_response(200)
        self.send_header("Content-type", "text/plain")
        self.end_headers()
    
    def log_message(self, format, *args):
        pass

def run_server():
    port = int(os.environ.get("PORT", 10000))
    server = HTTPServer(("0.0.0.0", port), SimpleHandler)
    print(f"🌐 HTTP Server running on port {port}", flush=True)
    server.serve_forever()

# ============================================
# TELEGRAM: ГЛОБАЛЬНЫЙ ОБРАБОТЧИК /start
# ============================================
async def global_start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if DEBUG_CALLBACKS:
        print(f"🔍 GLOBAL_START: user={update.effective_user.id}", flush=True)
    
    context.user_data.clear()
    keyboard = InlineKeyboardMarkup([
        [InlineKeyboardButton("📌 Создать вакансию", callback_data="menu_vacancy")],
        [InlineKeyboardButton("📄 Создать резюме", callback_data="menu_resume")],
        [InlineKeyboardButton("📋 Мои публикации", callback_data="my_publications")],
    ])
    await update.message.reply_text("👋 Привет! Что вы хотите сделать?", reply_markup=keyboard)
    return TGState.MAIN_MENU

# ============================================
# TELEGRAM: ГЛАВНОЕ МЕНЮ
# ============================================
async def main_menu_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    
    if DEBUG_CALLBACKS:
        print(f"🔍 MAIN_MENU: callback_data='{query.data}'", flush=True)
    
    context.user_data.clear()
    
    if query.data == "menu_vacancy":
        context.user_data["form_type"] = "vacancy"
        await query.message.reply_text("📌 Название вакансии?", reply_markup=get_step_keyboard())
        return TGState.V_TITLE
    
    elif query.data == "menu_resume":
        context.user_data["form_type"] = "resume"
        await query.message.reply_text("👤 Ваше имя?", reply_markup=get_step_keyboard())
        return TGState.R_NAME
    
    elif query.data == "my_publications":
        await show_my_publications(update, context)
        return TGState.MAIN_MENU
    
    elif query.data == "main_menu":
        return await back_to_main(update, context)
    
    else:
        if DEBUG_CALLBACKS:
            print(f"⚠️ MAIN_MENU: неизвестная команда '{query.data}'", flush=True)
        return await back_to_main(update, context)

async def show_my_publications(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    user_id = str(query.from_user.id)
    publications = get_user_publications(user_id, platform='tg')
    
    if not publications:
        keyboard = InlineKeyboardMarkup([
            [InlineKeyboardButton("🏠 В главное меню", callback_data="main_menu")],
        ])
        await query.message.reply_text("📭 У вас нет активных публикаций.", reply_markup=keyboard)
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
        
        if pub['status'] == 'approved':
            keyboard = InlineKeyboardMarkup([
                [InlineKeyboardButton("🗑 Удалить", callback_data=f"delete:{pub['id']}")]
            ])
        else:
            keyboard = None
        
        await query.message.reply_text(
            f"{status_emoji} {pub_type} от {pub['created_at'][:10]}\n\n{preview}",
            reply_markup=keyboard
        )
    
    if total > 10:
        await query.message.reply_text(f"🔍 Показаны последние 10 из {total}.")
    
    keyboard = InlineKeyboardMarkup([
        [InlineKeyboardButton("🏠 В главное меню", callback_data="main_menu")],
    ])
    await query.message.reply_text("Что хотите сделать дальше?", reply_markup=keyboard)

async def back_to_main(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    context.user_data.clear()
    
    keyboard = InlineKeyboardMarkup([
        [InlineKeyboardButton("📌 Создать вакансию", callback_data="menu_vacancy")],
        [InlineKeyboardButton("📄 Создать резюме", callback_data="menu_resume")],
        [InlineKeyboardButton("📋 Мои публикации", callback_data="my_publications")],
    ])
    
    if query.message:
        await query.message.reply_text("👋 Что вы хотите сделать?", reply_markup=keyboard)
    else:
        await query.edit_message_text("👋 Что вы хотите сделать?", reply_markup=keyboard)
    
    return TGState.MAIN_MENU

# ============================================
# TELEGRAM: КОМАНДЫ
# ============================================
async def start_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    return await global_start(update, context)

async def my_publications_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
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
        
        if pub['status'] == 'approved':
            keyboard = InlineKeyboardMarkup([
                [InlineKeyboardButton("🗑 Удалить", callback_data=f"delete:{pub['id']}")]
            ])
        else:
            keyboard = None
        
        await update.message.reply_text(
            f"{status_emoji} {pub_type} от {pub['created_at'][:10]}\n\n{preview}",
            reply_markup=keyboard
        )
    
    if total > 10:
        await update.message.reply_text(f"🔍 Показаны последние 10 из {total}.")

# ============================================
# TELEGRAM: АДМИН-КОМАНДЫ
# ============================================
async def admin_stats(update: Update, context: ContextTypes.DEFAULT_TYPE):
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
# TELEGRAM: ОБРАБОТЧИКИ ШАГОВ
# ============================================
def make_step_handler(field: str, next_state: TGState, prompt: str, skip_callback: Optional[str] = None):
    async def handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
        if not update.message:
            return next_state
        
        text = update.message.text.strip()
        context.user_data[field] = text
        
        reply_markup = get_step_keyboard(skip_callback)
        await update.message.reply_text(prompt, reply_markup=reply_markup)
        return next_state
    return handler

def make_skip_callback(field: str, next_state: TGState, prompt: str, next_skip: Optional[str] = None):
    async def handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
        query = update.callback_query
        await query.answer()
        
        context.user_data[field] = None
        
        reply_markup = get_step_keyboard(next_skip)
        await query.message.reply_text(prompt, reply_markup=reply_markup)
        return next_state
    return handler

def make_contact_handler():
    async def handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
        if not update.message:
            return TGState.MAIN_MENU
        
        text = update.message.text.strip()
        context.user_data["contact"] = text
        return await show_tg_preview(update, context)
    return handler

async def show_tg_preview(update: Update, context: ContextTypes.DEFAULT_TYPE):
    form_type = context.user_data.get("form_type")
    form_data = context.user_data
    
    if DEBUG_CALLBACKS:
        print(f"🔍 PREVIEW: form_type={form_type}", flush=True)
    
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
        [InlineKeyboardButton("❌ Отменить", callback_data=cancel_cb)],
    ])
    
    if update.message:
        await update.message.reply_text(
            f"📌 Предпросмотр {type_name}:\n\n{text}",
            reply_markup=keyboard
        )
    elif update.callback_query:
        await update.callback_query.message.reply_text(
            f"📌 Предпросмотр {type_name}:\n\n{text}",
            reply_markup=keyboard
        )
    
    return next_state

async def handle_tg_cancel(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    context.user_data.clear()
    
    if DEBUG_CALLBACKS:
        print("🔍 CANCEL: действие отменено", flush=True)
    
    keyboard = InlineKeyboardMarkup([
        [InlineKeyboardButton("🏠 В главное меню", callback_data="main_menu")],
    ])
    await safe_edit(query, "❌ Создание отменено.", reply_markup=keyboard)
    return ConversationHandler.END

async def handle_preview_callback(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    
    if DEBUG_CALLBACKS:
        print(f"🔍 HANDLE_PREVIEW: data='{query.data}', form_type='{context.user_data.get('form_type')}'", flush=True)
    
    form_type = context.user_data.get("form_type")
    
    if not form_type:
        if DEBUG_CALLBACKS:
            print("⚠️ PREVIEW: form_type не найден!", flush=True)
        await safe_edit(query, "⚠️ Сессия устарела. Начните с /start")
        return ConversationHandler.END
    
    if query.data in ["v_cancel", "r_cancel"]:
        context.user_data.clear()
        keyboard = InlineKeyboardMarkup([
            [InlineKeyboardButton("🏠 В главное меню", callback_data="main_menu")],
        ])
        await safe_edit(query, "❌ Создание отменено.", reply_markup=keyboard)
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
        return await send_to_moderation(update, context, form_type)
    
    if DEBUG_CALLBACKS:
        print(f"⚠️ PREVIEW: неизвестная команда '{query.data}'", flush=True)
    
    return ConversationHandler.END

async def send_to_moderation(update: Update, context: ContextTypes.DEFAULT_TYPE, form_type: str):
    query = update.callback_query
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
        [InlineKeyboardButton("✅ Одобрить", callback_data=create_callback_data("approve", pub_id)),
         InlineKeyboardButton("❌ Отклонить", callback_data=create_callback_data("reject", pub_id))]
    ])
    
    try:
        await context.bot.send_message(
            MODERATION_GROUP_ID,
            f"📥 Новое объявление (из TG)\n\n{text}",
            reply_markup=mod_keyboard
        )
        if DEBUG_CALLBACKS:
            print(f"✅ Отправлено на модерацию: {pub_id}", flush=True)
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
    return ConversationHandler.END

# ============================================
# TELEGRAM: МОДЕРАЦИЯ
# ============================================
async def _notify_user(pub: dict, message: str, pub_id: Optional[str] = None):
    if pub["platform"] == "tg":
        try:
            app = get_telegram_app()
            if app:
                keyboard = None
                if pub_id and pub["status"] == "approved":
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
    query = update.callback_query
    await query.answer()
    
    try:
        action, pub_id = query.data.split(":", 1)
    except ValueError:
        print(f"⚠️ MODERATION: неверный формат callback_data: {query.data}", flush=True)
        return
    
    if DEBUG_CALLBACKS:
        print(f"🔍 MODERATION: action='{action}', pub_id='{pub_id}'", flush=True)
    
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
    # ПРИНУДИТЕЛЬНЫЙ ВЫВОД В ЛОГИ
    print("=" * 50, flush=True)
    print("🔴🔴🔴 КНОПКА УДАЛЕНИЯ НАЖАТА! 🔴🔴🔴", flush=True)
    print(f"🔴 update: {update}", flush=True)
    print(f"🔴 callback_query: {update.callback_query}", flush=True)
    if update.callback_query:
        print(f"🔴 data: {update.callback_query.data}", flush=True)
    print("=" * 50, flush=True)
    sys.stdout.flush()
    
    query = update.callback_query
    await query.answer()
    
    try:
        _, pub_id = query.data.split(":", 1)
    except ValueError:
        print(f"⚠️ DELETE: неверный формат callback_data: {query.data}", flush=True)
        return ConversationHandler.END
    
    if DEBUG_CALLBACKS:
        print(f"🔍 DELETE: pub_id='{pub_id}', user_id='{query.from_user.id}'", flush=True)
    
    pub = get_publication(pub_id)
    if not pub:
        await query.answer("⚠️ Публикация не найдена.", show_alert=True)
        return ConversationHandler.END
    
    if str(query.from_user.id) != pub["user_id"] and pub["platform"] == "tg":
        await query.answer("⛔ Это не ваша публикация.", show_alert=True)
        return ConversationHandler.END
    
    if pub["status"] in ('deleted', 'expired'):
        await query.answer("⚠️ Публикация уже удалена.", show_alert=True)
        return ConversationHandler.END
    
    success = await delete_from_both_platforms(pub_id, context)
    
    if success:
        keyboard = InlineKeyboardMarkup([
            [InlineKeyboardButton("🏠 В главное меню", callback_data="main_menu")],
        ])
        await safe_edit(query, "🗑 Публикация удалена.", reply_markup=keyboard)
    else:
        await query.answer("⚠️ Не удалось удалить полностью.", show_alert=True)
    
    try:
        await context.bot.send_message(
            MODERATION_GROUP_ID,
            f"🗑 Пользователь удалил публикацию:\n\n{pub['text'][:200]}\n\n❌ Удалено"
        )
    except Exception as e:
        print(f"⚠️ Не удалось уведомить модераторов: {e}", flush=True)
    
    return ConversationHandler.END

# ============================================
# TELEGRAM: БОТ-ЧИСТИЛЬЩИК
# ============================================
async def delete_system_messages(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not update.message:
        return
    if update.message.new_chat_members or update.message.left_chat_member:
        try:
            await update.message.delete()
            print(f"🧹 Удалено системное сообщение в чате {update.message.chat.title}", flush=True)
        except Exception as e:
            print(f"⚠️ Не удалось удалить системное сообщение: {e}", flush=True)

# ============================================
# VK: ОТПРАВКА СООБЩЕНИЙ
# ============================================
async def send_vk_message(user_id: str, text: str, keyboard: Optional[Dict] = None):
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
# VK: КЛАВИАТУРЫ
# ============================================
def get_vk_main_keyboard():
    return {
        "inline": True,
        "buttons": [
            [{"action": {"type": "text", "label": "📌 Создать вакансию", "payload": "{\"action\": \"vacancy\"}"}}],
            [{"action": {"type": "text", "label": "📄 Создать резюме", "payload": "{\"action\": \"resume\"}"}}],
            [{"action": {"type": "text", "label": "📋 Мои публикации", "payload": "{\"action\": \"my_publications\"}"}}]
        ]
    }

def get_vk_cancel_only_keyboard():
    return {
        "inline": True,
        "buttons": [
            [{"action": {"type": "text", "label": "❌ Отменить", "payload": "{\"action\": \"cancel\"}"}}]
        ]
    }

def get_vk_skip_cancel_keyboard():
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
    return {
        "inline": True,
        "buttons": [
            [{"action": {"type": "text", "label": "✅ Отправить", "payload": "{\"action\": \"send\"}"}}],
            [{"action": {"type": "text", "label": "✏️ Заполнить заново", "payload": "{\"action\": \"restart\"}"}}],
            [{"action": {"type": "text", "label": "❌ Отменить", "payload": "{\"action\": \"cancel\"}"}}]
        ]
    }

# ============================================
# VK: ПОКАЗ ПУБЛИКАЦИЙ
# ============================================
async def show_vk_publications(user_id: str):
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
# VK: ГЛАВНОЕ МЕНЮ
# ============================================
async def show_vk_main_menu(user_id: str):
    clear_vk_state(user_id)
    await send_vk_message(user_id, "👋 Привет! Я бот для публикации вакансий и резюме.\n\nВыберите действие:", get_vk_main_keyboard())

# ============================================
# VK: ОБРАБОТКА УДАЛЕНИЯ
# ============================================
async def handle_vk_delete(user_id: str, pub_id: str):
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
    
    app = get_telegram_app()
    if app:
        await delete_from_both_platforms(pub_id, app)
    
    await send_vk_message(user_id, f"🗑 Публикация удалена.\n\n{pub['text'][:200]}...", get_vk_main_keyboard())

# ============================================
# VK: МАШИНА СОСТОЯНИЙ (сокращена для экономии места)
# ============================================
async def handle_vk_step(user_id: str, state: str, data: dict, text: str) -> str:
    print(f"🔍 VK: step user={user_id}, state={state}, text='{text[:80]}'", flush=True)
    
    try:
        payload_data = json.loads(text) if text.startswith('{') else {}
    except:
        payload_data = {}
    
    action = payload_data.get("action", "")
    
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
    
    # MAIN MENU
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
    
    # VACANCY STEPS
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
                [InlineKeyboardButton("✅ Одобрить", callback_data=create_callback_data("approve", pub_id)),
                 InlineKeyboardButton("❌ Отклонить", callback_data=create_callback_data("reject", pub_id))]
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
    
    # RESUME STEPS (аналогично)
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
                [InlineKeyboardButton("✅ Одобрить", callback_data=create_callback_data("approve", pub_id)),
                 InlineKeyboardButton("❌ Отклонить", callback_data=create_callback_data("reject", pub_id))]
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
    
    clear_vk_state(user_id)
    await show_vk_main_menu(user_id)
    return VKState.MAIN_MENU

# ============================================
# VK: СЛУШАТЕЛЬ LONGPOLL
# ============================================
async def vk_listener_async():
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
    loop = asyncio.new_event_loop()
    asyncio.set_event_loop(loop)
    loop.run_until_complete(vk_listener_async())

# ============================================
# ФОНОВЫЙ ПЛАНИРОВЩИК
# ============================================
async def maintenance_scheduler():
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
# ОБРАБОТЧИК ОШИБОК TELEGRAM
# ============================================
async def error_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    print(f"❌ Ошибка Telegram: {context.error}", flush=True)
    traceback.print_exception(type(context.error), context.error, context.error.__traceback__)
    
    if update and update.callback_query:
        try:
            await update.callback_query.answer("Произошла ошибка. Попробуйте снова.")
        except:
            pass
    
    if update and update.effective_message:
        try:
            await update.effective_message.reply_text(
                "⚠️ Произошла ошибка. Пожалуйста, начните с /start"
            )
        except:
            pass

# ============================================
# ГЛАВНАЯ ФУНКЦИЯ ЗАПУСКА
# ============================================
async def main():
    global _telegram_app, CHANNEL_CHAT_ID
    
    print("=" * 50, flush=True)
    print("🔧 ВЕРСИЯ КОДА: 5.2 (ФИКС СОХРАНЕНИЯ ID)", flush=True)
    print("=" * 50, flush=True)
    
    if not VACANCY_BOT_TOKEN:
        raise ValueError("❌ VACANCY_BOT_TOKEN не задан!")
    if MODERATION_GROUP_ID == 0:
        print("⚠️ MODERATION_GROUP_ID не задан!", flush=True)
    
    init_database()
    threading.Thread(target=run_server, daemon=True).start()
    
    # --- Telegram ---
    telegram_app = Application.builder().token(VACANCY_BOT_TOKEN).build()
    _telegram_app = telegram_app
    
    telegram_app.add_error_handler(error_handler)
    
    # Группа 0: Команды
    telegram_app.add_handler(CommandHandler("start", global_start), group=0)
    telegram_app.add_handler(CommandHandler("my", my_publications_command), group=0)
    telegram_app.add_handler(CommandHandler("adminstats", admin_stats), group=0)
    telegram_app.add_handler(CommandHandler("export", admin_export), group=0)
    
    # Группа 1: Специфичные callback'и
    telegram_app.add_handler(CallbackQueryHandler(moderation_buttons, pattern="^(approve|reject):"), group=1)
    telegram_app.add_handler(CallbackQueryHandler(delete_publication, pattern="^delete:"), group=1)
    
    # Группа 2: ConversationHandler
    conv_handler = ConversationHandler(
        entry_points=[
            CallbackQueryHandler(main_menu_handler, pattern="^(menu_vacancy|menu_resume|my_publications|main_menu)$"),
        ],
        states={
            TGState.MAIN_MENU: [
                CallbackQueryHandler(main_menu_handler, pattern="^(menu_vacancy|menu_resume|my_publications|main_menu)$"),
            ],
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
                CallbackQueryHandler(handle_tg_cancel, pattern="^cancel_action$"),
            ],
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
                CallbackQueryHandler(handle_tg_cancel, pattern="^cancel_action$"),
            ],
        },
        fallbacks=[
            CommandHandler("start", start_command),
            CallbackQueryHandler(handle_tg_cancel, pattern="^cancel_action$"),
        ],
        per_message=False,
        name="main_conversation"
    )
    
    telegram_app.add_handler(conv_handler, group=2)
    
    print(f"📋 Зарегистрировано групп обработчиков: {len(telegram_app.handlers)}", flush=True)
    for group_name, group_handlers in telegram_app.handlers.items():
        print(f"  Группа {group_name}: {len(group_handlers)} обработчиков", flush=True)
        for i, handler in enumerate(group_handlers):
            handler_type = type(handler).__name__
            try:
                if isinstance(handler, ConversationHandler):
                    print(f"    [{i}] {handler_type}: name='{handler.name}'", flush=True)
                elif isinstance(handler, CommandHandler):
                    print(f"    [{i}] {handler_type}: commands={list(handler.commands)}", flush=True)
                elif isinstance(handler, CallbackQueryHandler):
                    print(f"    [{i}] {handler_type}: pattern='{handler.pattern}'", flush=True)
                else:
                    print(f"    [{i}] {handler_type}", flush=True)
            except Exception as e:
                print(f"    [{i}] {handler_type}: (ошибка вывода: {e})", flush=True)
    
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
    
    if VK_TOKEN and VK_GROUP_ID != 0:
        vk_thread = threading.Thread(target=start_vk_listener, daemon=True)
        vk_thread.start()
        print("📱 VK: поток запущен", flush=True)
    else:
        print("⚠️ VK отключен", flush=True)
    
    asyncio.create_task(maintenance_scheduler())
    
    await telegram_app.initialize()
    await telegram_app.start()
    
    bot_info = await telegram_app.bot.get_me()
    print(f"✅ Бот @{bot_info.username} запущен (ID: {bot_info.id})", flush=True)
    
    try:
        chat = await telegram_app.bot.get_chat(CHANNEL_USERNAME)
        CHANNEL_CHAT_ID = chat.id
        print(f"📢 ID канала '{CHANNEL_USERNAME}': {CHANNEL_CHAT_ID}", flush=True)
    except Exception as e:
        print(f"⚠️ Не удалось получить ID канала: {e}", flush=True)
    
    try:
        chat = await telegram_app.bot.get_chat(MODERATION_GROUP_ID)
        print(f"✅ Доступ к чату модерации '{chat.title}' подтвержден", flush=True)
    except Exception as e:
        print(f"❌ Нет доступа к чату модерации {MODERATION_GROUP_ID}: {e}", flush=True)
    
    await telegram_app.updater.start_polling(drop_pending_updates=True)
    
    print("=" * 50, flush=True)
    print("🎉 БОТ ЗАПУЩЕН!", flush=True)
    print(f"📱 Telegram: активен", flush=True)
    print(f"📱 VK: {'активен' if (VK_TOKEN and VK_GROUP_ID != 0) else 'отключён'}", flush=True)
    print(f"📋 VK чат вакансий (peer_id): {VK_CHAT_VACANCIES}", flush=True)
    print(f"📋 VK чат резюме (peer_id): {VK_CHAT_RESUMES}", flush=True)
    print(f"📢 CHANNEL_CHAT_ID: {CHANNEL_CHAT_ID}", flush=True)
    print("=" * 50, flush=True)
    sys.stdout.flush()
    
    await asyncio.Event().wait()

if __name__ == "__main__":
    asyncio.run(main())