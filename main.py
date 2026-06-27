import os
import logging
import tempfile
from telegram import Update
from telegram.ext import Application, MessageHandler, filters, ContextTypes
import anthropic
from groq import Groq

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

TELEGRAM_TOKEN = os.environ["TELEGRAM_TOKEN"]
GROQ_API_KEY = os.environ["GROQ_API_KEY"]
ANTHROPIC_API_KEY = os.environ["ANTHROPIC_API_KEY"]

groq_client = Groq(api_key=GROQ_API_KEY)
anthropic_client = anthropic.Anthropic(api_key=ANTHROPIC_API_KEY)

SYSTEM_PROMPT = """אתה עוזר אישי חכם של רן אזולאי.
אתה מקבל הוראות קוליות ממנו ועליך לבצע אותן או לתת תשובה שימושית.
ענה תמיד בעברית, בצורה ממוקדת וברורה.
אם ההוראה דורשת פעולה שאינה בסמכותך (כמו גלישה באינטרנט), הסבר מה תצטרך לעשות ושאל אישור."""


async def handle_voice(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """מקבל הודעה קולית, ממיר לטקסט, ושולח לClaude."""
    await update.message.reply_text("🎙️ שומע אותך...")

    voice = update.message.voice
    file = await context.bot.get_file(voice.file_id)

    with tempfile.NamedTemporaryFile(suffix=".ogg", delete=False) as tmp:
        tmp_path = tmp.name

    await file.download_to_drive(tmp_path)

    try:
        with open(tmp_path, "rb") as audio_file:
            transcript = groq_client.audio.transcriptions.create(
                model="whisper-large-v3",
                file=audio_file,
                language="he"
            )
        text = transcript.text
        logger.info(f"Transcribed: {text}")

        await update.message.reply_text(f"🗣️ שמעתי: *{text}*", parse_mode="Markdown")

        response = anthropic_client.messages.create(
            model="claude-sonnet-4-6",
            max_tokens=1024,
            system=SYSTEM_PROMPT,
            messages=[{"role": "user", "content": text}]
        )

        reply = response.content[0].text
        await update.message.reply_text(reply)

    finally:
        os.unlink(tmp_path)


async def handle_text(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """מקבל הודעת טקסט ושולח לClaude."""
    text = update.message.text

    response = anthropic_client.messages.create(
        model="claude-sonnet-4-6",
        max_tokens=1024,
        system=SYSTEM_PROMPT,
        messages=[{"role": "user", "content": text}]
    )

    reply = response.content[0].text
    await update.message.reply_text(reply)


def main():
    app = Application.builder().token(TELEGRAM_TOKEN).build()
    app.add_handler(MessageHandler(filters.VOICE, handle_voice))
    app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, handle_text))
    logger.info("Bot is running...")
    app.run_polling()


if __name__ == "__main__":
    main()
