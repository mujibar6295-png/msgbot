import os
import json
import logging
import threading
from datetime import datetime
import pytz
from flask import Flask

from telegram import (
    Update,
    ReplyKeyboardMarkup,
    InlineKeyboardButton,
    InlineKeyboardMarkup,
    KeyboardButtonRequestChat
)
from telegram.ext import (
    ApplicationBuilder,
    CommandHandler,
    MessageHandler,
    CallbackQueryHandler,
    ContextTypes,
    filters
)
from apscheduler.schedulers.background import BackgroundScheduler

# ----------------- Configuration & Logging -----------------
TOKEN = "8863781796:AAFTF6HVU5fD653V3lCgnJw2echi4iENRM0"
DATA_FILE = "bot_data.json"
TIMEZONE = pytz.timezone("Asia/Kolkata")  # Adjust your preferred timezone here

logging.basicConfig(
    format="%(asctime)s - %(name)s - %(levelname)s - %(message)s",
    level=logging.INFO
)

# ----------------- Database / State Management -----------------
def load_data():
    if os.path.exists(DATA_FILE):
        try:
            with open(DATA_FILE, "r") as f:
                return json.load(f)
        except Exception:
            pass
    return {
        "channels": {},        # {str(chat_id): title}
        "scheduled_times": [], # ["01:00", "13:30", ...]
        "send_count": 1,
        "backup_channel": "",  # Link or username
        "queue": []            # List of queued messages
    }

def save_data():
    with open(DATA_FILE, "w") as f:
        json.dump(bot_db, f, indent=4)

bot_db = load_data()

# ----------------- Flask Web Server (For Render) -----------------
flask_app = Flask(__name__)

@flask_app.route('/')
def home():
    return "Bot is running perfectly!"

def run_flask():
    port = int(os.environ.get("PORT", 8080))
    flask_app.run(host="0.0.0.0", port=port)

# ----------------- Keyboards -----------------
def get_main_keyboard():
    return ReplyKeyboardMarkup([
        ["➕ Add Channel", "⏰ Set Time"],
        ["🔢 Set Count", "🗑 Delete Channel"],
        ["📋 Queue", "❌ Delete Queue"],
        ["🔗 Add Backup Channel"]
    ], resize_keyboard=True)

# ----------------- Command & Message Handlers -----------------
async def start_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    context.user_data.clear()
    await update.message.reply_text(
        "👋 Welcome! Your Automated Channel Scheduler Bot is active.\n"
        "Use the buttons below to configure your channels, schedules, and queues.",
        reply_markup=get_main_keyboard()
    )

async def handle_text(update: Update, context: ContextTypes.DEFAULT_TYPE):
    text = update.message.text
    user_state = context.user_data.get("state")

    # State: Awaiting Send Count
    if user_state == "awaiting_count":
        if text.isdigit() and int(text) > 0:
            bot_db["send_count"] = int(text)
            save_data()
            context.user_data.clear()
            await update.message.reply_text(
                f"✅ Send count set to: **{bot_db['send_count']}** messages per schedule.",
                parse_mode="Markdown",
                reply_markup=get_main_keyboard()
            )
        else:
            await update.message.reply_text("⚠️ Please send a valid positive number.")
        return

    # State: Awaiting Backup Channel Link
    if user_state == "awaiting_backup":
        bot_db["backup_channel"] = text.strip()
        save_data()
        context.user_data.clear()
        await update.message.reply_text(
            f"✅ Backup channel link saved:\n`{bot_db['backup_channel']}`",
            parse_mode="Markdown",
            reply_markup=get_main_keyboard()
        )
        return

    # State: Awaiting Delete Queue Count
    if user_state == "awaiting_delete_queue":
        if text.isdigit():
            count = int(text)
            current_len = len(bot_db["queue"])
            del_count = min(count, current_len)
            if del_count > 0:
                bot_db["queue"] = bot_db["queue"][:-del_count]
                save_data()
                await update.message.reply_text(
                    f"✅ Successfully deleted last {del_count} message(s) from the queue.\n"
                    f"Remaining in queue: {len(bot_db['queue'])}",
                    reply_markup=get_main_keyboard()
                )
            else:
                await update.message.reply_text("Queue is already empty.", reply_markup=get_main_keyboard())
            context.user_data.clear()
        else:
            await update.message.reply_text("⚠️ Please send a valid number.")
        return

    # Menu Triggers
    if text == "➕ Add Channel":
        # Native Telegram chat selector button requesting administrator rights
        btn = KeyboardButtonRequestChat(
            request_id=1,
            chat_is_channel=True,
            bot_is_member=True
        )
        await update.message.reply_text(
            "👇 Tap the button below to pick a channel where you are an admin. "
            "Ensure the bot is added as an administrator with posting rights.",
            reply_markup=ReplyKeyboardMarkup([
                [{"text": "📢 Select Channel", "request_chat": btn}],
                ["🔙 Back to Menu"]
            ], resize_keyboard=True)
        )

    elif text == "🔙 Back to Menu":
        context.user_data.clear()
        await update.message.reply_text("Main Menu:", reply_markup=get_main_keyboard())

    elif text == "⏰ Set Time":
        await show_time_slots(update)

    elif text == "🔢 Set Count":
        context.user_data["state"] = "awaiting_count"
        await update.message.reply_text(
            f"Current count: **{bot_db.get('send_count', 1)}**\n"
            "Send the number of messages to post at each scheduled time:"
        )

    elif text == "🗑 Delete Channel":
        if not bot_db["channels"]:
            await update.message.reply_text("ℹ️ No channels have been added yet.")
            return

        keyboard = []
        for chat_id, title in bot_db["channels"].items():
            keyboard.append([
                InlineKeyboardButton(f"🗑 {title}", callback_data=f"del_chan_{chat_id}")
            ])
        await update.message.reply_text(
            "Select a channel below to remove it from the schedule:",
            reply_markup=InlineKeyboardMarkup(keyboard)
        )

    elif text == "📋 Queue":
        chan_list = "\n".join([f"• {title} (`{cid}`)" for cid, title in bot_db["channels"].items()]) or "None"
        times_list = ", ".join(sorted(bot_db["scheduled_times"])) or "None"
        backup = bot_db.get("backup_channel") or "Not set"
        queue_count = len(bot_db["queue"])

        msg = (
            f"📊 **System Status & Queue Summary**\n\n"
            f"📦 **Queued Messages:** {queue_count}\n"
            f"🔢 **Batch Send Count:** {bot_db['send_count']}\n"
            f"🔗 **Backup Link:** {backup}\n\n"
            f"⏰ **Active Schedule Times:**\n{times_list}\n\n"
            f"📢 **Connected Channels:**\n{chan_list}"
        )
        await update.message.reply_text(msg, parse_mode="Markdown")

    elif text == "❌ Delete Queue":
        if not bot_db["queue"]:
            await update.message.reply_text("The queue is currently empty.")
            return
        context.user_data["state"] = "awaiting_delete_queue"
        await update.message.reply_text(
            f"Current queue size: **{len(bot_db['queue'])}**\n"
            "How many messages do you want to remove from the tail of the queue? Send the number:"
        )

    elif text == "🔗 Add Backup Channel":
        context.user_data["state"] = "awaiting_backup"
        await update.message.reply_text(
            "Send the full link for your **NEW BACKUP CHANNEL** (e.g. `https://t.me/yourbackup`):"
        )

# Chat selection result handling
async def handle_chat_shared(update: Update, context: ContextTypes.DEFAULT_TYPE):
    chat_shared = update.message.chat_shared
    if chat_shared:
        chat_id = str(chat_shared.chat_id)
        try:
            chat = await context.bot.get_chat(chat_id)
            title = chat.title or f"Channel {chat_id}"
        except Exception:
            title = f"Channel {chat_id}"

        bot_db["channels"][chat_id] = title
        save_data()
        await update.message.reply_text(
            f"✅ Channel added successfully!\n**Title:** {title}\n**ID:** `{chat_id}`",
            parse_mode="Markdown",
            reply_markup=get_main_keyboard()
        )

# ----------------- Time Matrix (30 min gaps) -----------------
async def show_time_slots(update: Update):
    keyboard = []
    current_selected = set(bot_db.get("scheduled_times", []))

    for hour in range(24):
        row = []
        for minute in (0, 30):
            t_str = f"{hour:02d}:{minute:02d}"
            check = "✅ " if t_str in current_selected else ""
            row.append(InlineKeyboardButton(f"{check}{t_str}", callback_data=f"toggle_time_{t_str}"))
        keyboard.append(row)

    keyboard.append([InlineKeyboardButton("💾 Save / Close", callback_data="close_time_menu")])

    reply_markup = InlineKeyboardMarkup(keyboard)
    if update.message:
        await update.message.reply_text("Select execution times (24-hour format):", reply_markup=reply_markup)
    elif update.callback_query:
        await update.callback_query.edit_message_reply_markup(reply_markup=reply_markup)

# ----------------- Callback Query Handler -----------------
async def handle_callbacks(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    data = query.data
    await query.answer()

    if data.startswith("toggle_time_"):
        t_str = data.replace("toggle_time_", "")
        times = bot_db.get("scheduled_times", [])
        if t_str in times:
            times.remove(t_str)
        else:
            times.append(t_str)
        bot_db["scheduled_times"] = times
        save_data()
        await show_time_slots(update)

    elif data == "close_time_menu":
        await query.edit_message_text(
            f"✅ Schedule updated.\nActive times: {', '.join(sorted(bot_db['scheduled_times'])) or 'None'}"
        )

    elif data.startswith("del_chan_"):
        chat_id = data.replace("del_chan_", "")
        removed = bot_db["channels"].pop(chat_id, None)
        save_data()
        await query.edit_message_text(f"🗑 Channel removed: **{removed or chat_id}**", parse_mode="Markdown")

# ----------------- Message Queue Listener -----------------
def extract_flezen_link(caption_or_text: str) -> str:
    if not caption_or_text:
        return ""
    words = caption_or_text.split()
    for word in words:
        if "flezen" in word.lower():
            return word
    # Fallback to any http URL if 'flezen' is not directly matched
    for word in words:
        if word.startswith("http://") or word.startswith("https://"):
            return word
    return ""

async def handle_forwarded_content(update: Update, context: ContextTypes.DEFAULT_TYPE):
    msg = update.message
    # Process only forwarded content
    if not (msg.forward_origin or msg.forward_from_chat or msg.forward_from or msg.forward_date):
        return

    content_type = None
    file_id = None
    raw_caption = msg.caption or msg.text or ""

    if msg.photo:
        content_type = "photo"
        file_id = msg.photo[-1].file_id
    elif msg.video:
        content_type = "video"
        file_id = msg.video.file_id
    else:
        content_type = "text"

    extracted_link = extract_flezen_link(raw_caption)

    queue_item = {
        "type": content_type,
        "file_id": file_id,
        "raw_text": raw_caption,
        "link": extracted_link
    }

    bot_db["queue"].append(queue_item)
    save_data()

    await msg.reply_text(
        f"📥 Message pushed directly to queue!\n"
        f"Queue position: **#{len(bot_db['queue'])}**\n"
        f"Detected Link: `{extracted_link or 'None'}`",
        parse_mode="Markdown"
    )

# ----------------- Dispatcher / Broadcast Execution -----------------
async def dispatch_scheduled_batch(application):
    if not bot_db["queue"] or not bot_db["channels"]:
        return

    count = bot_db.get("send_count", 1)
    backup_link = bot_db.get("backup_channel", "").strip()

    items_to_send = bot_db["queue"][:count]
    bot_db["queue"] = bot_db["queue"][count:]
    save_data()

    for item in items_to_send:
        link_str = item.get("link", "")
        formatted_caption = (
            f"FULL VIDEO LINK 👇👇\n"
            f"{link_str}\n\n"
            f"NEW BACKUP CHANNEL 👇\n"
            f"{backup_link}"
        )

        for chat_id in bot_db["channels"].keys():
            try:
                if item["type"] == "photo":
                    await application.bot.send_photo(
                        chat_id=int(chat_id),
                        photo=item["file_id"],
                        caption=formatted_caption
                    )
                elif item["type"] == "video":
                    await application.bot.send_video(
                        chat_id=int(chat_id),
                        video=item["file_id"],
                        caption=formatted_caption
                    )
                else:
                    await application.bot.send_message(
                        chat_id=int(chat_id),
                        text=formatted_caption
                    )
            except Exception as e:
                logging.error(f"Error sending to {chat_id}: {e}")

def run_scheduler_check(application, loop):
    now = datetime.now(TIMEZONE).strftime("%H:%M")
    if now in bot_db.get("scheduled_times", []):
        import asyncio
        asyncio.run_coroutine_threadsafe(dispatch_scheduled_batch(application), loop)

# ----------------- Main Bootstrapper -----------------
def main():
    # 1. Start Flask web server in a background thread for Render keep-alive
    threading.Thread(target=run_flask, daemon=True).start()

    # 2. Build Telegram Application
    application = ApplicationBuilder().token(TOKEN).build()

    # Register Handlers
    application.add_handler(CommandHandler("start", start_command))
    application.add_handler(MessageHandler(filters.StatusUpdate.CHAT_SHARED, handle_chat_shared))
    application.add_handler(CallbackQueryHandler(handle_callbacks))
    application.add_handler(MessageHandler(filters.FORWARDED, handle_forwarded_content))
    application.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, handle_text))

    # 3. Setup APScheduler for exact time polling (checks every minute)
    import asyncio
    loop = asyncio.get_event_loop()
    scheduler = BackgroundScheduler(timezone=TIMEZONE)
    scheduler.add_job(
        run_scheduler_check,
        trigger="cron",
        second=0,
        args=[application, loop]
    )
    scheduler.start()

    # 4. Start polling
    application.run_polling()

if __name__ == "__main__":
    main()
