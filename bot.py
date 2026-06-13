import os
from telegram import Update
from telegram.ext import (
    Application,
    CommandHandler,
    MessageHandler,
    ContextTypes,
    filters,
)

BOT_TOKEN = os.getenv("BOT_TOKEN")
ADMIN_ID = int(os.getenv("ADMIN_ID"))


async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text(
        "Отправьте вакансию одним сообщением.\n\n"
        "После проверки она будет опубликована."
    )


async def handle_message(update: Update, context: ContextTypes.DEFAULT_TYPE):
    text = update.message.text

    await context.bot.send_message(
        chat_id=ADMIN_ID,
        text=f"📥 Новая вакансия:\n\n{text}",
    )

    await update.message.reply_text(
        "✅ Спасибо! Вакансия отправлена на модерацию."
    )


def main():
    app = Application.builder().token(BOT_TOKEN).build()

    app.add_handler(CommandHandler("start", start))
    app.add_handler(
        MessageHandler(
            filters.TEXT & ~filters.COMMAND,
            handle_message,
        )
    )

    print("Bot started...")
    app.run_polling()


if __name__ == "__main__":
    main()