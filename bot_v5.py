import os
import threading
import asyncio
from http.server import HTTPServer, BaseHTTPRequestHandler

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

# ---------- ТОКЕНЫ ----------
VACANCY_BOT_TOKEN = os.getenv("VACANCY_BOT_TOKEN")
CLEANER_BOT_TOKEN = os.getenv("CLEANER_BOT_TOKEN")
MODERATION_GROUP_ID = int(os.getenv("MODERATION_GROUP_ID", "0"))

CHANNEL_USERNAME = "@poslesmenperm"
VACANCY_THREAD_ID = 5
RESUME_THREAD_ID = 6

# Состояния
MAIN_MENU = 0
V_TITLE, V_COMPANY, V_SALARY, V_SCHEDULE, V_DESCRIPTION, V_CONTACT, V_PREVIEW = range(1, 8)
R_NAME, R_AGE, R_POSITION, R_EXPERIENCE, R_SKILLS, R_EDUCATION, R_CONTACT, R_PREVIEW = range(10, 18)


# ============================
# HTTP SERVER (один на двоих)
# ============================
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
    port = int(os.environ.get("PORT", 10000))
    server = HTTPServer(("0.0.0.0", port), SimpleHandler)
    print(f"🌐 HTTP Server running on port {port}")
    server.serve_forever()


# ============================
# HELPERS
# ============================
def build_skip_keyboard(next_callback: str) -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup([
        [InlineKeyboardButton("⏭ Пропустить", callback_data=next_callback)]
    ])


def build_vacancy_text(data: dict) -> str:
    lines = []
    if data.get("title"): lines.append(f"📌 {data['title']}")
    if data.get("company"): lines.append(f"🏢 {data['company']}")
    if data.get("salary"): lines.append(f"💰 {data['salary']}")
    if data.get("schedule"): lines.append(f"🕒 {data['schedule']}")
    if data.get("description"): lines.append(f"📋 {data['description']}")
    if data.get("contact"): lines.append(f"📞 {data['contact']}")
    return "\n\n".join(lines)


def build_resume_text(data: dict) -> str:
    lines = []
    if data.get("name"): lines.append(f"👤 {data['name']}")
    if data.get("age"): lines.append(f"🎂 {data['age']}")
    if data.get("position"): lines.append(f"💼 Желаемая должность: {data['position']}")
    if data.get("experience"): lines.append(f"📅 Опыт работы:\n{data['experience']}")
    if data.get("skills"): lines.append(f"🛠 Навыки:\n{data['skills']}")
    if data.get("education"): lines.append(f"🎓 Образование:\n{data['education']}")
    if data.get("contact"): lines.append(f"📞 Контакты: {data['contact']}")
    return "\n\n".join(lines)


async def safe_edit(query, text, **kwargs):
    try:
        await query.edit_message_text(text, **kwargs)
    except Exception:
        pass


def get_reply_target(update):
    if update.message:
        return update.message
    return update.callback_query.message


# ============================
# ГЛАВНОЕ МЕНЮ
# ============================
async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    context.user_data.clear()
    keyboard = InlineKeyboardMarkup([
        [InlineKeyboardButton("📌 Создать вакансию", callback_data="menu_vacancy")],
        [InlineKeyboardButton("📄 Создать резюме", callback_data="menu_resume")],
    ])
    await update.message.reply_text("👋 Привет! Что вы хотите создать?", reply_markup=keyboard)
    return MAIN_MENU


async def main_menu_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    if query.data == "menu_vacancy":
        await query.message.reply_text("📌 Название вакансии?")
        return V_TITLE
    elif query.data == "menu_resume":
        await query.message.reply_text("👤 Ваше имя?")
        return R_NAME
    return ConversationHandler.END


# ============================
# ВАКАНСИЯ — ШАГИ
# ============================
async def v_title_step(update: Update, context: ContextTypes.DEFAULT_TYPE):
    text = update.message.text.strip()
    if not text:
        await update.message.reply_text("⚠️ Название не может быть пустым. Введите название:")
        return V_TITLE
    context.user_data["title"] = text
    await update.message.reply_text("🏢 Компания?")
    return V_COMPANY


async def v_company_step(update: Update, context: ContextTypes.DEFAULT_TYPE):
    context.user_data["company"] = update.message.text.strip()
    await update.message.reply_text("💰 Зарплата?", reply_markup=build_skip_keyboard("v_skip_salary"))
    return V_SALARY


async def v_salary_step(update: Update, context: ContextTypes.DEFAULT_TYPE):
    context.user_data["salary"] = update.message.text.strip()
    await update.message.reply_text("🕒 График?", reply_markup=build_skip_keyboard("v_skip_schedule"))
    return V_SCHEDULE


async def v_schedule_step(update: Update, context: ContextTypes.DEFAULT_TYPE):
    context.user_data["schedule"] = update.message.text.strip()
    await update.message.reply_text("📋 Описание?", reply_markup=build_skip_keyboard("v_skip_description"))
    return V_DESCRIPTION


async def v_description_step(update: Update, context: ContextTypes.DEFAULT_TYPE):
    context.user_data["description"] = update.message.text.strip()
    await update.message.reply_text("📞 Контакты?")
    return V_CONTACT


async def v_contact_step(update: Update, context: ContextTypes.DEFAULT_TYPE):
    context.user_data["contact"] = update.message.text.strip()
    return await v_show_preview(update, context)


async def v_skip_button(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    skip_map = {
        "v_skip_salary": ("salary", "🕒 График?", "v_skip_schedule", V_SCHEDULE),
        "v_skip_schedule": ("schedule", "📋 Описание?", "v_skip_description", V_DESCRIPTION),
        "v_skip_description": ("description", "📞 Контакты?", None, V_CONTACT),
    }
    if query.data not in skip_map:
        return ConversationHandler.END
    field, prompt, next_cb, next_state = skip_map[query.data]
    context.user_data[field] = None
    keyboard = build_skip_keyboard(next_cb) if next_cb else None
    await query.message.reply_text(prompt, reply_markup=keyboard)
    return next_state


async def v_show_preview(update, context):
    text = build_vacancy_text(context.user_data)
    keyboard = InlineKeyboardMarkup([
        [InlineKeyboardButton("✅ Отправить", callback_data="v_send")],
        [InlineKeyboardButton("✏️ Заполнить заново", callback_data="v_restart")],
        [InlineKeyboardButton("❌ Отмена", callback_data="v_cancel")],
    ])
    msg = get_reply_target(update)
    await msg.reply_text(f"📌 Предпросмотр вакансии:\n\n{text}", reply_markup=keyboard)
    return V_PREVIEW


async def v_preview_buttons(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    if query.data == "v_cancel":
        context.user_data.clear()
        await safe_edit(query, "❌ Создание вакансии отменено.")
        return ConversationHandler.END
    if query.data == "v_restart":
        context.user_data.clear()
        await query.message.reply_text("📌 Название вакансии?")
        return V_TITLE
    if query.data == "v_send":
        text = build_vacancy_text(context.user_data)
        vacancy_id = f"vac_{query.message.message_id}"
        context.bot_data[vacancy_id] = {
            "text": text, "user_id": query.from_user.id,
            "type": "vacancy", "thread_id": VACANCY_THREAD_ID,
        }
        keyboard = InlineKeyboardMarkup([
            [InlineKeyboardButton("✅ Одобрить", callback_data=f"approve:{vacancy_id}"),
             InlineKeyboardButton("❌ Отклонить", callback_data=f"reject:{vacancy_id}")]
        ])
        try:
            await context.bot.send_message(MODERATION_GROUP_ID, f"📥 Новая вакансия\n\n{text}", reply_markup=keyboard)
        except Exception:
            await query.message.reply_text("⚠️ Не удалось отправить на модерацию.")
            return V_PREVIEW
        keyboard = InlineKeyboardMarkup([
            [InlineKeyboardButton("➕ Создать ещё", callback_data="menu_vacancy")],
            [InlineKeyboardButton("🏠 В главное меню", callback_data="main_menu")],
        ])
        await safe_edit(query, "✅ Вакансия отправлена на модерацию.", reply_markup=keyboard)
        context.user_data.clear()
        return V_PREVIEW
    return ConversationHandler.END


# ============================
# РЕЗЮМЕ — ШАГИ
# ============================
async def r_name_step(update: Update, context: ContextTypes.DEFAULT_TYPE):
    text = update.message.text.strip()
    if not text:
        await update.message.reply_text("⚠️ Имя не может быть пустым. Введите имя:")
        return R_NAME
    context.user_data["name"] = text
    await update.message.reply_text("🎂 Возраст?", reply_markup=build_skip_keyboard("r_skip_age"))
    return R_AGE


async def r_age_step(update: Update, context: ContextTypes.DEFAULT_TYPE):
    context.user_data["age"] = update.message.text.strip()
    await update.message.reply_text("💼 Желаемая должность?", reply_markup=build_skip_keyboard("r_skip_position"))
    return R_POSITION


async def r_position_step(update: Update, context: ContextTypes.DEFAULT_TYPE):
    context.user_data["position"] = update.message.text.strip()
    await update.message.reply_text("📅 Опыт работы?", reply_markup=build_skip_keyboard("r_skip_experience"))
    return R_EXPERIENCE


async def r_experience_step(update: Update, context: ContextTypes.DEFAULT_TYPE):
    context.user_data["experience"] = update.message.text.strip()
    await update.message.reply_text("🛠 Ключевые навыки?", reply_markup=build_skip_keyboard("r_skip_skills"))
    return R_SKILLS


async def r_skills_step(update: Update, context: ContextTypes.DEFAULT_TYPE):
    context.user_data["skills"] = update.message.text.strip()
    await update.message.reply_text("🎓 Образование?", reply_markup=build_skip_keyboard("r_skip_education"))
    return R_EDUCATION


async def r_education_step(update: Update, context: ContextTypes.DEFAULT_TYPE):
    context.user_data["education"] = update.message.text.strip()
    await update.message.reply_text("📞 Контакты?")
    return R_CONTACT


async def r_contact_step(update: Update, context: ContextTypes.DEFAULT_TYPE):
    context.user_data["contact"] = update.message.text.strip()
    return await r_show_preview(update, context)


async def r_skip_button(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    skip_map = {
        "r_skip_age": ("age", "💼 Желаемая должность?", "r_skip_position", R_POSITION),
        "r_skip_position": ("position", "📅 Опыт работы?", "r_skip_experience", R_EXPERIENCE),
        "r_skip_experience": ("experience", "🛠 Ключевые навыки?", "r_skip_skills", R_SKILLS),
        "r_skip_skills": ("skills", "🎓 Образование?", "r_skip_education", R_EDUCATION),
        "r_skip_education": ("education", "📞 Контакты?", None, R_CONTACT),
    }
    if query.data not in skip_map:
        return ConversationHandler.END
    field, prompt, next_cb, next_state = skip_map[query.data]
    context.user_data[field] = None
    keyboard = build_skip_keyboard(next_cb) if next_cb else None
    await query.message.reply_text(prompt, reply_markup=keyboard)
    return next_state


async def r_show_preview(update, context):
    text = build_resume_text(context.user_data)
    keyboard = InlineKeyboardMarkup([
        [InlineKeyboardButton("✅ Отправить", callback_data="r_send")],
        [InlineKeyboardButton("✏️ Заполнить заново", callback_data="r_restart")],
        [InlineKeyboardButton("❌ Отмена", callback_data="r_cancel")],
    ])
    msg = get_reply_target(update)
    await msg.reply_text(f"📄 Предпросмотр резюме:\n\n{text}", reply_markup=keyboard)
    return R_PREVIEW


async def r_preview_buttons(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    if query.data == "r_cancel":
        context.user_data.clear()
        await safe_edit(query, "❌ Создание резюме отменено.")
        return ConversationHandler.END
    if query.data == "r_restart":
        context.user_data.clear()
        await query.message.reply_text("👤 Ваше имя?")
        return R_NAME
    if query.data == "r_send":
        text = build_resume_text(context.user_data)
        resume_id = f"res_{query.message.message_id}"
        context.bot_data[resume_id] = {
            "text": text, "user_id": query.from_user.id,
            "type": "resume", "thread_id": RESUME_THREAD_ID,
        }
        keyboard = InlineKeyboardMarkup([
            [InlineKeyboardButton("✅ Одобрить", callback_data=f"approve:{resume_id}"),
             InlineKeyboardButton("❌ Отклонить", callback_data=f"reject:{resume_id}")]
        ])
        try:
            await context.bot.send_message(MODERATION_GROUP_ID, f"📥 Новое резюме\n\n{text}", reply_markup=keyboard)
        except Exception:
            await query.message.reply_text("⚠️ Не удалось отправить на модерацию.")
            return R_PREVIEW
        keyboard = InlineKeyboardMarkup([
            [InlineKeyboardButton("➕ Создать ещё", callback_data="menu_resume")],
            [InlineKeyboardButton("🏠 В главное меню", callback_data="main_menu")],
        ])
        await safe_edit(query, "✅ Резюме отправлено на модерацию.", reply_markup=keyboard)
        context.user_data.clear()
        return R_PREVIEW
    return ConversationHandler.END


# ============================
# МОДЕРАЦИЯ
# ============================
async def moderation_buttons(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    try:
        action, item_id = query.data.split(":")
    except ValueError:
        return
    item = context.bot_data.get(item_id)
    if not item:
        await safe_edit(query, "⚠️ Публикация не найдена.")
        return
    text, user_id, thread_id = item["text"], item["user_id"], item["thread_id"]

    if action == "approve":
        try:
            sent_message = await context.bot.send_message(
                chat_id=CHANNEL_USERNAME,
                text=text,
                message_thread_id=thread_id
            )
            # Сохраняем ID сообщения в канале для удаления
            item["channel_message_id"] = sent_message.message_id
            item["channel_thread_id"] = thread_id
            # Кнопка удаления для пользователя
            keyboard = InlineKeyboardMarkup([
                [InlineKeyboardButton("🗑 Удалить публикацию", callback_data=f"delete:{item_id}")]
            ])
            await context.bot.send_message(
                user_id,
                "✅ Ваша публикация одобрена и опубликована.",
                reply_markup=keyboard
            )
            await safe_edit(query, query.message.text + "\n\n✅ Опубликовано")
        except Exception as e:
            await query.answer(f"❌ Ошибка публикации: {e}", show_alert=True)

    elif action == "reject":
        try:
            await context.bot.send_message(user_id, "❌ Ваша публикация отклонена.")
            await safe_edit(query, query.message.text + "\n\n❌ Отклонено")
        except Exception as e:
            await query.answer(f"❌ Ошибка: {e}", show_alert=True)


# ============================
# УДАЛЕНИЕ ПУБЛИКАЦИИ
# ============================
async def delete_publication(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Удаление публикации пользователем"""
    query = update.callback_query
    await query.answer()

    try:
        _, item_id = query.data.split(":")
    except ValueError:
        return

    item = context.bot_data.get(item_id)
    if not item:
        await query.answer("⚠️ Публикация не найдена.", show_alert=True)
        return

    # Проверяем, что удаляет автор
    if query.from_user.id != item["user_id"]:
        await query.answer("⛔ Это не ваша публикация.", show_alert=True)
        return

    # Удаляем из канала
    try:
        await context.bot.delete_message(
            chat_id=CHANNEL_USERNAME,
            message_id=item["channel_message_id"]
        )
    except Exception:
        await query.answer("⚠️ Не удалось удалить из канала.", show_alert=True)
        return

    # Убираем кнопку у пользователя
    await safe_edit(query, query.message.text + "\n\n🗑 Публикация удалена.")

    # Уведомляем модерацию
    await context.bot.send_message(
        MODERATION_GROUP_ID,
        f"🗑 Пользователь удалил публикацию:\n\n{item['text']}\n\n❌ Удалено"
    )

    # Очищаем данные
    del context.bot_data[item_id]


async def back_to_main(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    context.user_data.clear()
    keyboard = InlineKeyboardMarkup([
        [InlineKeyboardButton("📌 Создать вакансию", callback_data="menu_vacancy")],
        [InlineKeyboardButton("📄 Создать резюме", callback_data="menu_resume")],
    ])
    await query.message.reply_text("👋 Что вы хотите создать?", reply_markup=keyboard)
    return MAIN_MENU


# ============================
# БОТ-ЧИСТИЛЬЩИК
# ============================
async def delete_system_messages(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not update.message:
        return
    if update.message.new_chat_members or update.message.left_chat_member:
        try:
            await update.message.delete()
            print(f"🧹 Удалено системное сообщение в чате {update.message.chat.title}")
        except Exception as e:
            print(f"Не удалось удалить сообщение: {e}")


# ============================
# MAIN — запуск двух ботов
# ============================
def run_vacancy_bot():
    """Бот вакансий/резюме"""
    app = Application.builder().token(VACANCY_BOT_TOKEN).build()

    conv_handler = ConversationHandler(
        entry_points=[CommandHandler("start", start)],
        states={
            MAIN_MENU: [CallbackQueryHandler(main_menu_handler, pattern="^menu_")],
            V_TITLE: [MessageHandler(filters.TEXT & ~filters.COMMAND, v_title_step)],
            V_COMPANY: [MessageHandler(filters.TEXT & ~filters.COMMAND, v_company_step)],
            V_SALARY: [MessageHandler(filters.TEXT & ~filters.COMMAND, v_salary_step),
                       CallbackQueryHandler(v_skip_button, pattern="^v_skip_")],
            V_SCHEDULE: [MessageHandler(filters.TEXT & ~filters.COMMAND, v_schedule_step),
                         CallbackQueryHandler(v_skip_button, pattern="^v_skip_")],
            V_DESCRIPTION: [MessageHandler(filters.TEXT & ~filters.COMMAND, v_description_step),
                            CallbackQueryHandler(v_skip_button, pattern="^v_skip_")],
            V_CONTACT: [MessageHandler(filters.TEXT & ~filters.COMMAND, v_contact_step)],
            V_PREVIEW: [CallbackQueryHandler(v_preview_buttons, pattern="^v_"),
                        CallbackQueryHandler(back_to_main, pattern="^main_menu$"),
                        CallbackQueryHandler(main_menu_handler, pattern="^menu_vacancy$")],
            R_NAME: [MessageHandler(filters.TEXT & ~filters.COMMAND, r_name_step)],
            R_AGE: [MessageHandler(filters.TEXT & ~filters.COMMAND, r_age_step),
                    CallbackQueryHandler(r_skip_button, pattern="^r_skip_")],
            R_POSITION: [MessageHandler(filters.TEXT & ~filters.COMMAND, r_position_step),
                         CallbackQueryHandler(r_skip_button, pattern="^r_skip_")],
            R_EXPERIENCE: [MessageHandler(filters.TEXT & ~filters.COMMAND, r_experience_step),
                           CallbackQueryHandler(r_skip_button, pattern="^r_skip_")],
            R_SKILLS: [MessageHandler(filters.TEXT & ~filters.COMMAND, r_skills_step),
                       CallbackQueryHandler(r_skip_button, pattern="^r_skip_")],
            R_EDUCATION: [MessageHandler(filters.TEXT & ~filters.COMMAND, r_education_step),
                          CallbackQueryHandler(r_skip_button, pattern="^r_skip_")],
            R_CONTACT: [MessageHandler(filters.TEXT & ~filters.COMMAND, r_contact_step)],
            R_PREVIEW: [CallbackQueryHandler(r_preview_buttons, pattern="^r_"),
                        CallbackQueryHandler(back_to_main, pattern="^main_menu$"),
                        CallbackQueryHandler(main_menu_handler, pattern="^menu_resume$")],
        },
        fallbacks=[CommandHandler("start", start)],
    )

    app.add_handler(conv_handler)
    app.add_handler(CallbackQueryHandler(moderation_buttons, pattern="^(approve|reject):"))
    app.add_handler(CallbackQueryHandler(delete_publication, pattern="^delete:"))

    print("✅ Бот вакансий запущен...")
    app.run_polling(drop_pending_updates=True)


def run_cleaner_bot():
    """Бот-чистильщик"""
    app = Application.builder().token(CLEANER_BOT_TOKEN).build()

    app.add_handler(
        MessageHandler(
            filters.StatusUpdate.NEW_CHAT_MEMBERS | filters.StatusUpdate.LEFT_CHAT_MEMBER,
            delete_system_messages,
        )
    )

    print("🧹 Бот-чистильщик запущен...")
    app.run_polling(drop_pending_updates=True)


def main():
    if not VACANCY_BOT_TOKEN:
        raise ValueError("❌ VACANCY_BOT_TOKEN не задан!")
    if not CLEANER_BOT_TOKEN:
        raise ValueError("❌ CLEANER_BOT_TOKEN не задан!")
    if MODERATION_GROUP_ID == 0:
        print("⚠️ MODERATION_GROUP_ID не задан — модерация не будет работать.")

    # Запускаем HTTP-сервер
    threading.Thread(target=run_server, daemon=True).start()

    # Запускаем ботов в отдельных потоках
    vacancy_thread = threading.Thread(target=run_vacancy_bot, daemon=True)
    cleaner_thread = threading.Thread(target=run_cleaner_bot, daemon=True)

    vacancy_thread.start()
    cleaner_thread.start()

    # Держим главный поток живым
    vacancy_thread.join()
    cleaner_thread.join()


if __name__ == "__main__":
    main()