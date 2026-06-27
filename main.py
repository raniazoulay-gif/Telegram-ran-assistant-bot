import os
import json
import logging
import tempfile
import requests
from datetime import datetime, timedelta
from uuid import uuid4
from telegram import Update
from telegram.ext import Application, MessageHandler, filters, ContextTypes
import anthropic
from groq import Groq

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

TELEGRAM_TOKEN = os.environ["TELEGRAM_TOKEN"]
GROQ_API_KEY = os.environ["GROQ_API_KEY"]
ANTHROPIC_API_KEY = os.environ["ANTHROPIC_API_KEY"]
BREVO_API_KEY = os.environ["BREVO_API_KEY"]
MY_EMAIL = "ranaz@matrix.co.il"
MY_NAME = "רן אזולאי"

groq_client = Groq(api_key=GROQ_API_KEY)
anthropic_client = anthropic.Anthropic(api_key=ANTHROPIC_API_KEY)

TODAY = datetime.now().strftime("%Y-%m-%d")

SYSTEM_PROMPT = f"""אתה עוזר אישי חכם של רן אזולאי. היום: {TODAY}.
אתה מקבל הוראות קוליות ממנו ועליך לבצע אותן או לתת תשובה שימושית.
ענה תמיד בעברית, בצורה ממוקדת וברורה.

כשרן מבקש לקבוע פגישה/אירוע ביומן, החזר JSON בפורמט הזה בלבד (ללא טקסט נוסף):
{{
  "action": "calendar",
  "title": "כותרת האירוע",
  "date": "YYYY-MM-DD",
  "start_time": "HH:MM",
  "end_time": "HH:MM",
  "description": "תיאור קצר"
}}

אם זה שאלה רגילה (לא פגישה), ענה בטקסט רגיל בעברית."""


def create_ics(title: str, date: str, start_time: str, end_time: str, description: str) -> str:
    start_dt = datetime.strptime(f"{date} {start_time}", "%Y-%m-%d %H:%M")
    end_dt = datetime.strptime(f"{date} {end_time}", "%Y-%m-%d %H:%M")
    uid = str(uuid4())
    now = datetime.utcnow().strftime("%Y%m%dT%H%M%SZ")
    start_str = start_dt.strftime("%Y%m%dT%H%M%S")
    end_str = end_dt.strftime("%Y%m%dT%H%M%S")

    return f"""BEGIN:VCALENDAR
VERSION:2.0
PRODID:-//Ran Assistant Bot//HE
BEGIN:VEVENT
UID:{uid}
DTSTAMP:{now}
DTSTART:{start_str}
DTEND:{end_str}
SUMMARY:{title}
DESCRIPTION:{description}
END:VEVENT
END:VCALENDAR"""


def send_calendar_invite(title: str, date: str, start_time: str, end_time: str, description: str) -> bool:
    ics_content = create_ics(title, date, start_time, end_time, description)
    import base64
    ics_b64 = base64.b64encode(ics_content.encode()).decode()

    payload = {
        "sender": {"name": "Ran Assistant Bot", "email": "raniazoulay@gmail.com"},
        "to": [{"email": MY_EMAIL, "name": MY_NAME}],
        "subject": f"📅 פגישה חדשה: {title}",
        "htmlContent": f"<p>פגישה חדשה נקבעה ביומן שלך:</p><p><b>{title}</b><br>{date} | {start_time}–{end_time}</p><p>{description}</p>",
        "attachment": [{"name": "invite.ics", "content": ics_b64}]
    }

    resp = requests.post(
        "https://api.brevo.com/v3/smtp/email",
        headers={"api-key": BREVO_API_KEY, "Content-Type": "application/json"},
        json=payload,
        timeout=10
    )
    return resp.status_code == 201


async def process_message(text: str, update: Update):
    response = anthropic_client.messages.create(
        model="claude-sonnet-4-6",
        max_tokens=512,
        system=SYSTEM_PROMPT,
        messages=[{"role": "user", "content": text}]
    )
    reply = response.content[0].text.strip()

    # נקה markdown code blocks אם יש
    if "```" in reply:
        reply = reply.split("```")[-2] if reply.count("```") >= 2 else reply
        if reply.startswith("json"):
            reply = reply[4:].strip()

    # בדוק אם Claude החזיר JSON לפגישה
    if '"action": "calendar"' in reply:
        try:
            data = json.loads(reply)
            success = send_calendar_invite(
                title=data["title"],
                date=data["date"],
                start_time=data["start_time"],
                end_time=data["end_time"],
                description=data.get("description", "")
            )
            if success:
                await update.message.reply_text(
                    f"✅ פגישה נקבעה!\n\n"
                    f"📌 *{data['title']}*\n"
                    f"📅 {data['date']} | {data['start_time']}–{data['end_time']}\n\n"
                    f"זימון נשלח ל-{MY_EMAIL}",
                    parse_mode="Markdown"
                )
            else:
                await update.message.reply_text("⚠️ לא הצלחתי לשלוח את הזימון. נסה שוב.")
        except Exception as e:
            logger.error(f"Calendar error: {e}")
            await update.message.reply_text("⚠️ שגיאה בקביעת הפגישה.")
    else:
        await update.message.reply_text(reply)


async def handle_voice(update: Update, context: ContextTypes.DEFAULT_TYPE):
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
        await process_message(text, update)
    finally:
        os.unlink(tmp_path)


async def handle_text(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await process_message(update.message.text, update)


def main():
    app = Application.builder().token(TELEGRAM_TOKEN).build()
    app.add_handler(MessageHandler(filters.VOICE, handle_voice))
    app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, handle_text))
    logger.info("Bot is running...")
    app.run_polling()


if __name__ == "__main__":
    main()
