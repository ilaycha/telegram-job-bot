import os
import threading
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

BOT_TOKEN = os.getenv("BOT_TOKEN")
ADMIN_ID = int(os.getenv("ADMIN_ID"))

CHANNEL_USERNAME = "@poslesmenperm"

TITLE, COMPANY, SALARY, SCHEDULE, DESCRIPTION, CONTACT, PREVIEW = range(7)


# =========================
# HTTP SERVER FOR RENDER
# =========================

class SimpleHandler(BaseHTTPRequestHandler):
    def do_GET(self):
        self.send_response(200)
        self.send_header("Content-type", "text/plain")
        self.end_headers()
        self.wfile.write(b"Bot is running")

    def log_message(self, format, *args):
        pass


def run_server():
    port = int(os.environ.get("PORT", 10000))
    server = HTTPServer(("0.0.0.0", port), SimpleHandler)
    server.serve_forever()


threading.Thread(target=run_server, daemon=True).start()


# =========================
# HELPERS
# =========================

def build_vacancy_text(data):
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


# =========================
# START
# =========================

async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    context.user_data.clear()

    await update.message.reply_text(
        "📌 Название вакансии?"
    )

    return TITLE


# =========================
# TITLE
# =========================

async def title_step(update: Update, context: ContextTypes.DEFAULT_TYPE):
    context.user_data["title"] = update.message.text

    await update.message.reply_text(
        "🏢 Компания?"
    )

    return COMPANY


# =========================
# COMPANY
# =========================

async def company_step(update: Update, context: ContextTypes.DEFAULT_TYPE):
    context.user_data["company"] = update.message.text

    keyboard = InlineKeyboardMarkup([
        [InlineKeyboardButton("⏭ Пропустить", callback_data="skip_salary")]
    ])

    await update.message.reply_text(
        "💰 Зарплата?",
        reply_markup=keyboard
    )

    return SALARY


# =========================
# SALARY
# =========================

async def salary_step(update: Update, context: ContextTypes.DEFAULT_TYPE):
    context.user_data["salary"] = update.message.text

    keyboard = InlineKeyboardMarkup([
        [InlineKeyboardButton("⏭ Пропустить", callback_data="skip_schedule")]
    ])

    await update.message.reply_text(
        "🕒 График?",
        reply_markup=keyboard
    )

    return SCHEDULE


# =========================
# SCHEDULE
# =========================

async def schedule_step(update: Update, context: ContextTypes.DEFAULT_TYPE):
    context.user_data["schedule"] = update.message.text

    keyboard = InlineKeyboardMarkup([
        [InlineKeyboardButton("⏭ Пропустить", callback_data="skip_description")]
    ])

    await update.message.reply_text(
        "📋 Описание?",
        reply_markup=keyboard
    )

    return DESCRIPTION


# =========================
# DESCRIPTION
# =========================

async def description_step(update: Update, context: ContextTypes.DEFAULT_TYPE):
    context.user_data["description"] = update.message.text

    await update.message.reply_text(
        "📞 Контакты?"
    )

    return CONTACT


# =========================
# CONTACT
# =========================

async def contact_step(update: Update, context: ContextTypes.DEFAULT_TYPE):
    context.user_data["contact"] = update.message.text

    return await show_preview(update, context)


# =========================
# SKIPS
# =========================

async def skip_button(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()

    action = query.data

    if action == "skip_salary":
        context.user_data["salary"] = None

        keyboard = InlineKeyboardMarkup([
            [InlineKeyboardButton("⏭ Пропустить", callback_data="skip_schedule")]
        ])

        await query.message.reply_text(
            "🕒 График?",
            reply_markup=keyboard
        )

        return SCHEDULE

    if action == "skip_schedule":
        context.user_data["schedule"] = None

        keyboard = InlineKeyboardMarkup([
            [InlineKeyboardButton("⏭ Пропустить", callback_data="skip_description")]
        ])

        await query.message.reply_text(
            "📋 Описание?",
            reply_markup=keyboard
        )

        return DESCRIPTION

    if action == "skip_description":
        context.user_data["description"] = None

        await query.message.reply_text(
            "📞 Контакты?"
        )

        return CONTACT

    return ConversationHandler.END


# =========================
# PREVIEW
# =========================

async def show_preview(update, context):
    vacancy_text = build_vacancy_text(context.user_data)

    keyboard = InlineKeyboardMarkup([
        [
            InlineKeyboardButton(
                "✅ Отправить",
                callback_data="send_moderation"
            )
        ],
        [
            InlineKeyboardButton(
                "✏️ Заполнить заново",
                callback_data="restart_form"
            )
        ],
        [
            InlineKeyboardButton(
                "❌ Отмена",
                callback_data="cancel_form"
            )
        ]
    ])

    await update.message.reply_text(
        f"Предпросмотр:\n\n{vacancy_text}",
        reply_markup=keyboard
    )

    return PREVIEW


# =========================
# PREVIEW BUTTONS
# =========================

async def preview_buttons(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()

    action = query.data
    
    if action == "new_vacancy":
        context.user_data.clear()

        await query.message.reply_text(
        "📌 Название вакансии?"
        )

        return TITLE

    if action == "restart_form":
        context.user_data.clear()

        await query.message.reply_text(
            "📌 Название вакансии?"
        )

        return TITLE

    if action == "cancel_form":
        context.user_data.clear()

        await query.edit_message_text(
            "❌ Создание вакансии отменено."
        )

        return ConversationHandler.END

    if action == "send_moderation":

        vacancy_text = build_vacancy_text(context.user_data)

        vacancy_id = str(query.message.message_id)

        context.bot_data[vacancy_id] = {
            "text": vacancy_text,
            "user_id": query.from_user.id,
        }

        keyboard = InlineKeyboardMarkup([
            [
                InlineKeyboardButton(
                    "✅ Одобрить",
                    callback_data=f"approve:{vacancy_id}"
                ),
                InlineKeyboardButton(
                    "❌ Отклонить",
                    callback_data=f"reject:{vacancy_id}"
                ),
            ]
        ])

        await context.bot.send_message(
            ADMIN_ID,
            f"📥 Новая вакансия\n\n{vacancy_text}",
            reply_markup=keyboard
        )

        keyboard = InlineKeyboardMarkup([
            [
                InlineKeyboardButton(
                    "➕ Отправить ещё вакансию",
                    callback_data="new_vacancy"
                )
            ]
        ])

        await query.edit_message_text(
            "✅ Вакансия отправлена на модерацию.",
            reply_markup=keyboard
        )

        context.user_data.clear()

        return PREVIEW

    return ConversationHandler.END


# =========================
# MODERATION
# =========================

async def moderation_buttons(
    update: Update,
    context: ContextTypes.DEFAULT_TYPE
):
    query = update.callback_query
    await query.answer()

    action, vacancy_id = query.data.split(":")

    vacancy = context.bot_data.get(vacancy_id)

    if not vacancy:
        await query.edit_message_text(
            "⚠️ Вакансия не найдена."
        )
        return

    text = vacancy["text"]
    user_id = vacancy["user_id"]

    if action == "approve":

        await context.bot.send_message(
            chat_id=CHANNEL_USERNAME,
            text=text,
            message_thread_id=5
        )

        await context.bot.send_message(
            user_id,
            "✅ Ваша вакансия опубликована."
        )

        await query.edit_message_text(
            query.message.text + "\n\n✅ Опубликовано"
        )

    elif action == "reject":

        await context.bot.send_message(
            user_id,
            "❌ Ваша вакансия отклонена."
        )

        await query.edit_message_text(
            query.message.text + "\n\n❌ Отклонено"
        )


# =========================
# MAIN
# =========================

def main():
    app = Application.builder().token(BOT_TOKEN).build()

    conv_handler = ConversationHandler(
        entry_points=[
            CommandHandler("start", start)
        ],
        states={
            TITLE: [
                MessageHandler(filters.TEXT & ~filters.COMMAND, title_step)
            ],
            COMPANY: [
                MessageHandler(filters.TEXT & ~filters.COMMAND, company_step)
            ],
            SALARY: [
                MessageHandler(filters.TEXT & ~filters.COMMAND, salary_step),
                CallbackQueryHandler(skip_button, pattern="^skip_"),
            ],
            SCHEDULE: [
                MessageHandler(filters.TEXT & ~filters.COMMAND, schedule_step),
                CallbackQueryHandler(skip_button, pattern="^skip_"),
            ],
            DESCRIPTION: [
                MessageHandler(filters.TEXT & ~filters.COMMAND, description_step),
                CallbackQueryHandler(skip_button, pattern="^skip_"),
            ],
            CONTACT: [
                MessageHandler(filters.TEXT & ~filters.COMMAND, contact_step)
            ],
            PREVIEW: [
                CallbackQueryHandler(
                    preview_buttons,
                    pattern="^(send_moderation|restart_form|cancel_form|new_vacancy)$"
                )
            ],
        },
        fallbacks=[
            CommandHandler("start", start)
        ],
    )

    app.add_handler(conv_handler)

    app.add_handler(
        CallbackQueryHandler(
            moderation_buttons,
            pattern="^(approve|reject):"
        )
    )

    print("Bot started...")

    app.run_polling(
        drop_pending_updates=True
    )


if __name__ == "__main__":
    main()