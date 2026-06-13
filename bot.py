import os

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
    user_id = update.effective_user.id

    keyboard = InlineKeyboardMarkup(
        [
            [
                InlineKeyboardButton(
                    "✅ Одобрить",
                    callback_data=f"approve:{user_id}",
                ),
                InlineKeyboardButton(
                    "❌ Отклонить",
                    callback_data=f"reject:{user_id}",
                ),
            ]
        ]
    )

    await context.bot.send_message(
        chat_id=ADMIN_ID,
        text=f"📥 Новая вакансия\n\n{text}",
        reply_markup=keyboard,
    )

    await update.message.reply_text(
        "✅ Вакансия отправлена на модерацию."
    )


async def button_handler(
    update: Update,
    context: ContextTypes.DEFAULT_TYPE,
):
    query = update.callback_query
    await query.answer()

    action, user_id = query.data.split(":")
    user_id = int(user_id)

    if action == "approve":
        await context.bot.send_message(
            chat_id=user_id,
            text="✅ Ваша вакансия одобрена."
        )

        await query.edit_message_text(
            query.message.text + "\n\n✅ Одобрено"
        )

    elif action == "reject":
        await context.bot.send_message(
            chat_id=user_id,
            text="❌ Ваша вакансия отклонена."
        )

        await query.edit_message_text(
            query.message.text + "\n\n❌ Отклонено"
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

    app.add_handler(
        CallbackQueryHandler(button_handler)
    )

    print("Bot started...")
    app.run_polling()


if __name__ == "__main__":
    main()