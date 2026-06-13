import os
from telegram import Update
from telegram.ext import Application, MessageHandler, ContextTypes, filters

ADMIN_ID = int(os.getenv("ADMIN_ID"))

async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text(
        "Отправьте вакансию одним сообщением. После проверки она будет опубликована."
    )

async def handle_message(update: Update, context: ContextTypes.DEFAULT_TYPE):
    text = update.message.text

    await context.bot.send_message(
        chat_id=ADMIN_ID,
        text=f"Новая вакансия:\n\n{text}"
    )

    await update.message.reply_text(
        "Спасибо. Вакансия отправлена на модерацию."
    )

app = Application.builder().token(os.getenv("BOT_TOKEN")).build()

app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, handle_message))

app.run_polling()