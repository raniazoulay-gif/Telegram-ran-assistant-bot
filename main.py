import os
import json
import logging
import tempfile
import requests
import asyncio
from datetime import datetime
from uuid import uuid4
from telegram import Update
from telegram.ext import Application, MessageHandler, filters, ContextTypes
import anthropic
from groq import Groq
from playwright.async_api import async_playwright

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

TELEGRAM_TOKEN = os.environ["TELEGRAM_TOKEN"]
GROQ_API_KEY = os.environ["GROQ_API_KEY"]
ANTHROPIC_API_KEY = os.environ["ANTHROPIC_API_KEY"]
BREVO_API_KEY = os.environ["BREVO_API_KEY"]
MY_EMAIL = "ranaz@matrix.co.il"
MY_NAME = "רן אזולאי"
ALLOWED_CHAT_ID = int(os.environ.get("ALLOWED_CHAT_ID", "0"))

groq_client = Groq(api_key=GROQ_API_KEY)
anthropic_client = anthropic.Anthropic(api_key=ANTHROPIC_API_KEY)


def get_system_prompt():
    today = datetime.now().strftime("%Y-%m-%d")
    return f"""אתה עוזר אישי חכם של רן אזולאי. היום: {today}.
חובה להחזיר JSON בלבד — ללא טקסט נוסף, ללא הסברים, ללא markdown.

פורמטים:

1. גלישה/חיפוש/מחירים/חדשות/כל מידע מהאינטרנט:
{{"action": "browse", "url": "https://...", "task": "מה לחפש"}}

2. קביעת פגישה:
{{"action": "calendar", "title": "כותרת", "date": "YYYY-MM-DD", "start_time": "HH:MM", "end_time": "HH:MM", "description": "תיאור"}}

3. שאלה שלא דורשת אינטרנט:
{{"action": "answer", "text": "תשובה בעברית"}}

כללי URL:
- טיסות אל-על: https://booking.elal.com/booking/flights?market=IL&lang=he&tripType=ONE_WAY&origin=TLV&destination=CDG&departureDate=2026-07-01&adults=1&children=0&infants=0
- טיסות כלליות: https://www.google.com/travel/flights
- חדשות: https://www.ynet.co.il אם לא צוין אחר
- מזג אוויר: https://www.weather.com/he-IL/weather/today/l/Tel+Aviv
- כל בקשה לאינטרנט → חובה action=browse"""


def is_authorized(update: Update) -> bool:
    if ALLOWED_CHAT_ID == 0:
        return True
    return update.effective_chat.id == ALLOWED_CHAT_ID


async def browse_url(url: str, task: str) -> str:
    try:
        async with async_playwright() as p:
            browser = await p.chromium.launch(
                headless=True,
                args=["--no-sandbox", "--disable-dev-shm-usage"]
            )
            context = await browser.new_context(
                user_agent="Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 Chrome/120.0.0.0 Safari/537.36",
                locale="he-IL"
            )
            page = await context.new_page()
            await page.goto(url, wait_until="domcontentloaded", timeout=30000)
            await asyncio.sleep(4)
            content = await page.evaluate("() => document.body.innerText")
            await browser.close()
            return content[:8000]
    except Exception as e:
        logger.error(f"Browse error: {e}")
        return f"שגיאה בגלישה: {str(e)}"


def create_ics(title, date, start_time, end_time, description):
    start_dt = datetime.strptime(f"{date} {start_time}", "%Y-%m-%d %H:%M")
    end_dt = datetime.strptime(f"{date} {end_time}", "%Y-%m-%d %H:%M")
    uid = str(uuid4())
    now = datetime.utcnow().strftime("%Y%m%dT%H%M%SZ")
    return f"""BEGIN:VCALENDAR\nVERSION:2.0\nPRODID:-//Ran Assistant//HE\nBEGIN:VEVENT\nUID:{uid}\nDTSTAMP:{now}\nDTSTART:{start_dt.strftime('%Y%m%dT%H%M%S')}\nDTEND:{end_dt.strftime('%Y%m%dT%H%M%S')}\nSUMMARY:{title}\nDESCRIPTION:{description}\nEND:VEVENT\nEND:VCALENDAR"""


def send_calendar_invite(title, date, start_time, end_time, description):
    import base64
    ics_b64 = base64.b64encode(create_ics(title, date, start_time, end_time, description).encode()).decode()
    payload = {
        "sender": {"name": "Ran Assistant Bot", "email": "raniazoulay@gmail.com"},
        "to": [{"email": MY_EMAIL, "name": MY_NAME}],
        "subject": f"📅 פגישה חדשה: {title}",
        "htmlContent": f"<p><b>{title}</b><br>{date} | {start_time}–{end_time}</p><p>{description}</p>",
        "attachment": [{"name": "invite.ics", "content": ics_b64}]
    }
    resp = requests.post(
        "https://api.brevo.com/v3/smtp/email",
        headers={"api-key": BREVO_API_KEY, "Content-Type": "application/json"},
        json=payload, timeout=10
    )
    return resp.status_code == 201


def extract_json(text: str) -> str:
    if "```" in text:
        import re
        match = re.search(r"```(?:json)?\s*(\{.*?\})\s*```", text, re.DOTALL)
        if match:
            return match.group(1).strip()
    if "{" in text:
        start = text.index("{")
        end = text.rindex("}") + 1
        return text[start:end]
    return text


async def process_message(text: str, update: Update):
    try:
        response = anthropic_client.messages.create(
            model="claude-sonnet-4-6",
            max_tokens=512,
            system=get_system_prompt(),
            messages=[{"role": "user", "content": text}]
        )
        reply = response.content[0].text.strip()
    except Exception as e:
        await update.message.reply_text("⚠️ שגיאה בתקשורת עם Claude. נסה שוב.")
        logger.error(f"Claude error: {e}")
        return

    cleaned = extract_json(reply)

    try:
        data = json.loads(cleaned)
        action = data.get("action")

        if action == "browse":
            url = data.get("url", "")
            await update.message.reply_text(f"🌐 גולש ל-{url}...")
            content = await browse_url(url, data.get("task", text))
            try:
                summary = anthropic_client.messages.create(
                    model="claude-sonnet-4-6",
                    max_tokens=1024,
                    messages=[{"role": "user", "content": f"המשתמש ביקש: {data.get('task', text)}\n\nתוכן הדף:\n{content}\n\nענה בעברית בצורה ממוקדת וברורה."}]
                )
                await update.message.reply_text(summary.content[0].text)
            except Exception as e:
                await update.message.reply_text("⚠️ שגיאה בעיבוד תוכן הדף.")
                logger.error(f"Summary error: {e}")

        elif action == "calendar":
            title = data.get("title", "פגישה")
            date = data.get("date", "")
            start_time = data.get("start_time", "10:00")
            end_time = data.get("end_time", "11:00")
            description = data.get("description", "")
            success = send_calendar_invite(title, date, start_time, end_time, description)
            if success:
                await update.message.reply_text(
                    f"✅ פגישה נקבעה!\n\n📌 {title}\n📅 {date} | {start_time}–{end_time}\n\nזימון נשלח ל-{MY_EMAIL}"
                )
            else:
                await update.message.reply_text("⚠️ לא הצלחתי לשלוח את הזימון.")

        elif action == "answer":
            await update.message.reply_text(data.get("text", reply))

        else:
            await update.message.reply_text(reply)

    except json.JSONDecodeError:
        await update.message.reply_text(reply)


async def handle_voice(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not is_authorized(update):
        return
    await update.message.reply_text("🎙️ שומע אותך...")
    voice = update.message.voice
    file = await context.bot.get_file(voice.file_id)
    with tempfile.NamedTemporaryFile(suffix=".ogg", delete=False) as tmp:
        tmp_path = tmp.name
    await file.download_to_drive(tmp_path)
    try:
        with open(tmp_path, "rb") as audio_file:
            transcript = groq_client.audio.transcriptions.create(
                model="whisper-large-v3", file=audio_file, language="he"
            )
        text = transcript.text
        logger.info(f"Transcribed: {text}")
        await update.message.reply_text(f"🗣️ שמעתי: *{text}*", parse_mode="Markdown")
        await process_message(text, update)
    except Exception as e:
        await update.message.reply_text("⚠️ שגיאה בזיהוי הקול. נסה שוב.")
        logger.error(f"Voice error: {e}")
    finally:
        try:
            os.unlink(tmp_path)
        except Exception:
            pass


async def handle_text(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not is_authorized(update):
        return
    await process_message(update.message.text, update)


def main():
    app = Application.builder().token(TELEGRAM_TOKEN).build()
    app.add_handler(MessageHandler(filters.VOICE, handle_voice))
    app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, handle_text))
    logger.info("Bot is running...")
    app.run_polling()


if __name__ == "__main__":
    main()
