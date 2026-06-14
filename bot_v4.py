import os
import threading
import traceback
from http.server import HTTPServer, BaseHTTPRequestHandler

from telegram import (
    Update,
    InlineKeyboardButton,
    InlineKeyboardMarkup,
)
from telegram.ext import (
    Application,
    CommandHandler,
    MessageHandler,
    CallbackQueryHandler,
    ConversationHandler,
    ContextTypes,
    filters,
)

# ---------- ENV ----------
BOT_TOKEN = os.getenv("BOT_TOKEN")
ADMIN_ID = int(os.getenv("ADMIN_ID", "0"))

CHANNEL_USERNAME = "@poslesmenperm"

TITLE, COMPANY, SALARY, SCHEDULE, DESCRIPTION, CONTACT, PREVIEW = range(7)

# ---------- HTTP SERVER (для Render / health check) ----------
class SimpleHandler(BaseHTTPRequestHandler):
    def do_GET(self):
        self._send_ok()

    def do_HEAD(self):
        """HEAD-запрос от Uptime Robot — отвечаем без тела."""
        self._send_ok()

    def _send_ok(self):
        """Общий метод для 200 OK."""
        self.send_response(200)
        self.send_header("Content-type", "text/plain")
        self.end_headers()
        # Тело нужно только для GET
        if self.command == "GET":
            self.wfile.write(b"Bot is running")

    def log_message(self, format, *args):
        pass
# ---------- HELPERS ----------
def build_skip_keyboard(next_callback: str) -> InlineKeyboardMarkup:
    """Универсальная кнопка «Пропустить»."""
    return InlineKeyboardMarkup([
        [InlineKeyboardButton("⏭ Пропустить", callback_data=next_callback)]
    ])

def build_vacancy_text(data: dict) -> str:
    """Формирует текст вакансии из user_data."""
    lines = []
    if data.get("title"):
        lines.append(f"📌 {data['title']}")
    if data.get("company"):
        lines.append(f"🏢 {data['company']}")
    if data.get("salary"):
        lines.append(f"💰 {data['salary']}")
    if data.get("schedule"):
        lines.append(f"🕒 {data['schedule']}")
    if data.get("description"):
        lines.append(f"📋 {data['description']}")
    if data.get("contact"):
        lines.append(f"📞 {data['contact']}")
    return "\n\n".join(lines)

async def safe_edit(query, text, **kwargs):
    """Безопасное редактирование сообщения."""
    try:
        await query.edit_message_text(text, **kwargs)
    except Exception:
        pass  # игнорируем ошибку, если сообщение не изменилось

# ---------- ШАГИ ----------
async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Начало сценария (команда /start)."""
    context.user_data.clear()
    await update.message.reply_text("📌 Название вакансии?")
    return TITLE

async def title_step(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Шаг 1: название."""
    text = update.message.text.strip()
    if not text:
        await update.message.reply_text("⚠️ Название не может быть пустым. Введите название:")
        return TITLE

    context.user_data["title"] = text
    await update.message.reply_text("🏢 Компания?")
    return COMPANY

async def company_step(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Шаг 2: компания."""
    context.user_data["company"] = update.message.text.strip()
    await update.message.reply_text("💰 Зарплата?", reply_markup=build_skip_keyboard("skip_salary"))
    return SALARY

async def salary_step(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Шаг 3: зарплата."""
    context.user_data["salary"] = update.message.text.strip()
    await update.message.reply_text("🕒 График?", reply_markup=build_skip_keyboard("skip_schedule"))
    return SCHEDULE

async def schedule_step(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Шаг 4: график."""
    context.user_data["schedule"] = update.message.text.strip()
    await update.message.reply_text("📋 Описание?", reply_markup=build_skip_keyboard("skip_description"))
    return DESCRIPTION

async def description_step(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Шаг 5: описание."""
    context.user_data["description"] = update.message.text.strip()
    await update.message.reply_text("📞 Контакты?")
    return CONTACT

async def contact_step(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Шаг 6: контакты → превью."""
    context.user_data["contact"] = update.message.text.strip()
    return await show_preview(update, context)

# ---------- ПРОПУСКИ ----------
async def skip_button(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Обработка нажатий на кнопки «Пропустить»."""
    query = update.callback_query
    await query.answer()

    skip_map = {
        "skip_salary":    ("salary",    "🕒 График?",       "skip_schedule",    SCHEDULE),
        "skip_schedule":  ("schedule",  "📋 Описание?",      "skip_description", DESCRIPTION),
        "skip_description": ("description", "📞 Контакты?",  None,               CONTACT),
    }

    if query.data not in skip_map:
        return ConversationHandler.END

    field, prompt, next_cb, next_state = skip_map[query.data]
    context.user_data[field] = None

    keyboard = build_skip_keyboard(next_cb) if next_cb else None
    await query.message.reply_text(prompt, reply_markup=keyboard)
    return next_state

# ---------- ПРЕВЬЮ ----------
def get_reply_target(update):
    """Возвращает message (из update.message или query.message)."""
    if update.message:
        return update.message
    return update.callback_query.message

async def show_preview(update, context):
    """Показывает превью вакансии."""
    vacancy_text = build_vacancy_text(context.user_data)
    keyboard = InlineKeyboardMarkup([
        [InlineKeyboardButton("✅ Отправить", callback_data="send_moderation")],
        [InlineKeyboardButton("✏️ Заполнить заново", callback_data="restart_form")],
        [InlineKeyboardButton("❌ Отмена", callback_data="cancel_form")],
    ])

    msg = get_reply_target(update)
    await msg.reply_text(f"Предпросмотр:\n\n{vacancy_text}", reply_markup=keyboard)
    return PREVIEW

async def preview_buttons(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Обработка кнопок на этапе превью."""
    query = update.callback_query
    await query.answer()

    if query.data == "cancel_form":
        context.user_data.clear()
        await safe_edit(query, "❌ Создание вакансии отменено.")
        return ConversationHandler.END

    if query.data == "restart_form":
        context.user_data.clear()
        await query.message.reply_text("📌 Название вакансии?")
        return TITLE

    if query.data == "send_moderation":
        vacancy_text = build_vacancy_text(context.user_data)
        vacancy_id = str(query.message.message_id)

        # Сохраняем во временное хранилище (в памяти)
        context.bot_data[vacancy_id] = {
            "text": vacancy_text,
            "user_id": query.from_user.id,
        }

        # Отправляем админу на модерацию
        keyboard = InlineKeyboardMarkup([
            [
                InlineKeyboardButton("✅ Одобрить", callback_data=f"approve:{vacancy_id}"),
                InlineKeyboardButton("❌ Отклонить", callback_data=f"reject:{vacancy_id}"),
            ]
        ])

        try:
            await context.bot.send_message(
                ADMIN_ID,
                f"📥 Новая вакансия\n\n{vacancy_text}",
                reply_markup=keyboard,
            )
        except Exception:
            await query.message.reply_text("⚠️ Не удалось отправить вакансию на модерацию. Попробуйте позже.")
            return PREVIEW

        # Сообщаем пользователю
        keyboard = InlineKeyboardMarkup([
            [InlineKeyboardButton("➕ Отправить ещё вакансию", callback_data="new_vacancy")]
        ])
        await safe_edit(query, "✅ Вакансия отправлена на модерацию.", reply_markup=keyboard)
        context.user_data.clear()
        return PREVIEW

    if query.data == "new_vacancy":
        context.user_data.clear()
        await query.message.reply_text("📌 Название вакансии?")
        return TITLE

    return ConversationHandler.END

# ---------- МОДЕРАЦИЯ ----------
async def moderation_buttons(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Обработка кнопок админом (одобрить / отклонить)."""
    query = update.callback_query

    # Только админ
    if query.from_user.id != ADMIN_ID:
        await query.answer("⛔ У вас нет прав для модерации.", show_alert=True)
        return

    await query.answer()
    try:
        action, vacancy_id = query.data.split(":")
    except ValueError:
        return

    vacancy = context.bot_data.get(vacancy_id)
    if not vacancy:
        await safe_edit(query, "⚠️ Вакансия не найдена.")
        return

    text = vacancy["text"]
    user_id = vacancy["user_id"]

    if action == "approve":
        try:
            await context.bot.send_message(
                chat_id=CHANNEL_USERNAME,
                text=text,
                message_thread_id=5,
            )
            await context.bot.send_message(user_id, "✅ Ваша вакансия опубликована.")
            await safe_edit(query, query.message.text + "\n\n✅ Опубликовано")
        except Exception as e:
            await query.answer(f"❌ Ошибка публикации: {e}", show_alert=True)

    elif action == "reject":
        try:
            await context.bot.send_message(user_id, "❌ Ваша вакансия отклонена.")
            await safe_edit(query, query.message.text + "\n\n❌ Отклонено")
        except Exception as e:
            await query.answer(f"❌ Ошибка: {e}", show_alert=True)

# ---------- MAIN ----------
def main():
    if not BOT_TOKEN:
        raise ValueError("❌ BOT_TOKEN не задан в переменных окружения!")
    if ADMIN_ID == 0:
        print("⚠️ ADMIN_ID не задан – модерация не будет работать.")

    app = Application.builder().token(BOT_TOKEN).build()

    conv_handler = ConversationHandler(
        entry_points=[CommandHandler("start", start)],
        states={
            TITLE:       [MessageHandler(filters.TEXT & ~filters.COMMAND, title_step)],
            COMPANY:     [MessageHandler(filters.TEXT & ~filters.COMMAND, company_step)],
            SALARY:      [
                MessageHandler(filters.TEXT & ~filters.COMMAND, salary_step),
                CallbackQueryHandler(skip_button, pattern="^skip_"),
            ],
            SCHEDULE:    [
                MessageHandler(filters.TEXT & ~filters.COMMAND, schedule_step),
                CallbackQueryHandler(skip_button, pattern="^skip_"),
            ],
            DESCRIPTION: [
                MessageHandler(filters.TEXT & ~filters.COMMAND, description_step),
                CallbackQueryHandler(skip_button, pattern="^skip_"),
            ],
            CONTACT:     [MessageHandler(filters.TEXT & ~filters.COMMAND, contact_step)],
            PREVIEW:     [
                CallbackQueryHandler(
                    preview_buttons,
                    pattern="^(send_moderation|restart_form|cancel_form|new_vacancy)$",
                )
            ],
        },
        fallbacks=[CommandHandler("start", start)],
    )

    app.add_handler(conv_handler)
    app.add_handler(CallbackQueryHandler(moderation_buttons, pattern="^(approve|reject):"))

    print("✅ Бот запущен...")
    app.run_polling(drop_pending_updates=True)

if __name__ == "__main__":
    main()