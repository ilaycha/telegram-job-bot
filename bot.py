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

CHANNEL_USERNAME = "@poslesmenperm"


async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text(
        "Отправьте вакансию одним сообщением.\n\n"
        "После проверки она будет опубликована."
    )


async def handle_message(update: Update, context: ContextTypes.DEFAULT_TYPE):
    text = update.message.text
    user_id = update.effective_user.id

    vacancy_id = str(update.message.message_id)

    context.bot_data[vacancy_id] = {
        "text": text,
        "user_id": user_id,
    }

    keyboard = InlineKeyboardMarkup(
        [
            [
                InlineKeyboardButton(
                    "✅ Одобрить",
                    callback_data=f"approve:{vacancy_id}",
                ),
                InlineKeyboardButton(
                    "❌ Отклонить",
                    callback_data=f"reject:{vacancy_id}",
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

        try:
            await context.bot.send_message(
                chat_id=CHANNEL_USERNAME,
                text=text,
            )

            await context.bot.send_message(
                chat_id=user_id,
                text="✅ Ваша вакансия одобрена и опубликована."
            )

            await query.edit_message_text(
                query.message.text + "\n\n✅ Опубликовано"
            )

        except Exception as e:
            await query.edit_message_text(
                f"Ошибка публикации:\n{e}"
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

    app.add_handler(
        CommandHandler(
            "start",
            start,
        )
    )

    app.add_handler(
        MessageHandler(
            filters.TEXT & ~filters.COMMAND,
            handle_message,
        )
    )

    app.add_handler(
        CallbackQueryHandler(
            button_handler
        )
    )

    print("Bot started...")

    app.run_polling(
        drop_pending_updates=True
    )


if __name__ == "__main__":
    main()