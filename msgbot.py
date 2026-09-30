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
    ChatMemberUpdated
)
from telegram.ext import (
    ApplicationBuilder,
    CommandHandler,
    MessageHandler,
    CallbackQueryHandler,
    ChatMemberHandler,
    ContextTypes,
    filters
)

# ----------------- Configuration & Logging -----------------
TOKEN = "8863781796:AAFTF6HVU5fD653V3lCgnJw2echi4iENRM0"
DATA_FILE = "bot_data.json"
TIMEZONE = pytz.timezone("Asia/Kolkata")

logging.basicConfig(
    format="%(asctime)s - %(name)s - %(levelname)s - %(message)s",
    level=logging.INFO
)

# ----------------- Database / State -----------------
def load_data():
    if os.path.exists(DATA_FILE):
        try:
            with open(DATA_FILE, "r") as f:
                return json.load(f)
        except Exception:
            pass
    return {
        "channels": {},
        "scheduled_times": [],
        "send_count": 1,
        "backup_channel": "",
        "queue": []
    }

def save_data():
    with open(DATA_FILE, "w") as f:
        json.dump(bot_db, f, indent=4)

bot_db = load_data()

# ----------------- Flask Web Server -----------------
flask_app = Flask(__name__)

@flask_app.route('/')
@flask_app.route('/healthz')
def home():
    return "Bot is running!"

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

# ----------------- Auto-Admin Tracker -----------------
# Jokhon-i user bot-ke kono channel-e admin banabe, Telegram ei event trigger korbe
async def handle_bot_promoted(update: Update, context: ContextTypes.DEFAULT_TYPE):
    result = update.my_chat_member
    if not result:
        return

    chat = result.chat
    new_status = result.new_chat_member.status

    # Channel-e Bot promote hole auto-register
    if chat.type == "channel" and new_status == "administrator":
        chat_id = str(chat.id)
        title = chat.title or f"Channel {chat_id}"
        bot_db["channels"][chat_id] = title
        save_data()

        # Bot admin bananowar por channel owner/user-ke DM pathabe
        try:
            user_id = result.from_user.id
            await context.bot.send_message(
                chat_id=user_id,
                text=f"✅ **Channel Auto-Added!**\n\n"
                     f"📢 **Channel:** {title}\n"
                     f"🆔 **ID:** `{chat_id}`\n\n"
                     f"Bot has been successfully linked and ready to post!",
                parse_mode="Markdown",
                reply_markup=get_main_keyboard()
            )
        except Exception:
            pass

# ----------------- Command & Message Handlers -----------------
async def start_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    context.user_data.clear()
    bot_info = await context.bot.get_me()
    context.bot_data["username"] = bot_info.username

    await update.message.reply_text(
        "👋 Welcome! Automated Channel Dispatcher is Online.\n"
        "Click the buttons below to manage your channels and posts.",
        reply_markup=get_main_keyboard()
    )

async def handle_text(update: Update, context: ContextTypes.DEFAULT_TYPE):
    text = update.message.text.strip()
    user_state = context.user_data.get("state")

    # Bot username fetch
    bot_username = context.bot_data.get("username")
    if not bot_username:
        bot_info = await context.bot.get_me()
        bot_username = bot_info.username
        context.bot_data["username"] = bot_username

    # 1. ➕ Add Channel - Instant One-Click Link
    if text == "➕ Add Channel":
        context.user_data.clear()
        add_channel_url = f"https://t.me/{bot_username}?startchannel=botstart&admin=post_messages+edit_messages+delete_messages"
        
        inline_kb = InlineKeyboardMarkup([
            [InlineKeyboardButton("➕ Choose Channel & Add as Admin", url=add_channel_url)]
        ])
        
        await update.message.reply_text(
            "👇 **Tap the button below** to see all the channels you own/manage.\n\n"
            "Select your channel and confirm to add the bot as Admin. "
            "Once added, the bot will automatically register the channel!",
            reply_markup=inline_kb,
            parse_mode="Markdown"
        )
        return

    elif text == "⏰ Set Time":
        context.user_data.clear()
        await show_time_slots(update)
        return

    elif text == "🔢 Set Count":
        context.user_data["state"] = "awaiting_count"
        await update.message.reply_text(
            f"Current count: **{bot_db.get('send_count', 1)}**\n"
            "Send the number of messages to post at each scheduled time:"
        )
        return

    elif text == "🗑 Delete Channel":
        context.user_data.clear()
        if not bot_db["channels"]:
            await update.message.reply_text("ℹ️ No channels added yet.")
            return

        keyboard = []
        for chat_id, title in bot_db["channels"].items():
            keyboard.append([
                InlineKeyboardButton(f"🗑 {title}", callback_data=f"del_chan_{chat_id}")
            ])
        await update.message.reply_text(
            "Select a channel to remove:",
            reply_markup=InlineKeyboardMarkup(keyboard)
        )
        return

    elif text == "📋 Queue":
        context.user_data.clear()
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
        return

    elif text == "❌ Delete Queue":
        if not bot_db["queue"]:
            await update.message.reply_text("The queue is currently empty.")
            return
        context.user_data["state"] = "awaiting_delete_queue"
        await update.message.reply_text(
            f"Current queue size: **{len(bot_db['queue'])}**\n"
            "How many messages do you want to delete from the end of the queue?"
        )
        return

    elif text == "🔗 Add Backup Channel":
        context.user_data["state"] = "awaiting_backup"
        await update.message.reply_text(
            "Send the full link for your **NEW BACKUP CHANNEL** (e.g. `https://t.me/yourchannel`):"
        )
        return

    # Handle State Responses
    if user_state == "awaiting_count":
        if text.isdigit() and int(text) > 0:
            bot_db["send_count"] = int(text)
            save_data()
            context.user_data.clear()
            await update.message.reply_text(
                f"✅ Send count set to: **{bot_db['send_count']}**",
                reply_markup=get_main_keyboard()
            )
        else:
            await update.message.reply_text("⚠️ Please send a valid positive number.")
        return

    if user_state == "awaiting_backup":
        bot_db["backup_channel"] = text
        save_data()
        context.user_data.clear()
        await update.message.reply_text(
            f"✅ Backup channel link saved:\n`{bot_db['backup_channel']}`",
            parse_mode="Markdown",
            reply_markup=get_main_keyboard()
        )
        return

    if user_state == "awaiting_delete_queue":
        if text.isdigit():
            count = int(text)
            current_len = len(bot_db["queue"])
            del_count = min(count, current_len)
            if del_count > 0:
                bot_db["queue"] = bot_db["queue"][:-del_count]
                save_data()
                await update.message.reply_text(
                    f"✅ Deleted last {del_count} message(s).\nRemaining in queue: {len(bot_db['queue'])}",
                    reply_markup=get_main_keyboard()
                )
            else:
                await update.message.reply_text("Queue is empty.", reply_markup=get_main_keyboard())
            context.user_data.clear()
        else:
            await update.message.reply_text("⚠️ Please send a valid number.")
        return

# ----------------- Time Matrix -----------------
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

# ----------------- Callbacks -----------------
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

# ----------------- Forward / Queue Listener -----------------
def extract_flezen_link(caption_or_text: str) -> str:
    if not caption_or_text:
        return ""
    
    words = caption_or_text.split()
    
    # 1. First check if there is an explicit http/https URL anywhere in the text
    for word in words:
        if word.startswith("http://") or word.startswith("https://") or "t.me/" in word:
            return word
            
    # 2. If "flezen" exists, look for the next word which might be the link/identifier
    for i, word in enumerate(words):
        if "flezen" in word.lower():
            # Jodi flezen-er por kono word thake, ta return korbe
            if i + 1 < len(words):
                return words[i + 1]
            return word
            
    return ""

async def handle_forwarded_content(update: Update, context: ContextTypes.DEFAULT_TYPE):
    msg = update.message
    if not (msg.forward_origin or msg.forward_from_chat or msg.forward_from or msg.forward_date):
        return

    content_type = "photo" if msg.photo else "video" if msg.video else "text"
    file_id = msg.photo[-1].file_id if msg.photo else msg.video.file_id if msg.video else None
    raw_caption = msg.caption or msg.text or ""

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
        f"📥 Message added to queue!\n"
        f"Position: **#{len(bot_db['queue'])}**\n"
        f"Detected Link: `{extracted_link or 'None'}`",
        parse_mode="Markdown"
    )

# ----------------- Dispatcher -----------------
async def dispatch_scheduled_batch(context: ContextTypes.DEFAULT_TYPE):
    now_str = datetime.now(TIMEZONE).strftime("%H:%M")
    if now_str not in bot_db.get("scheduled_times", []):
        return

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
                    await context.bot.send_photo(
                        chat_id=int(chat_id),
                        photo=item["file_id"],
                        caption=formatted_caption
                    )
                elif item["type"] == "video":
                    await context.bot.send_video(
                        chat_id=int(chat_id),
                        video=item["file_id"],
                        caption=formatted_caption
                    )
                else:
                    await context.bot.send_message(
                        chat_id=int(chat_id),
                        text=formatted_caption
                    )
            except Exception as e:
                logging.error(f"Error sending to {chat_id}: {e}")

# ----------------- Main -----------------
def main():
    threading.Thread(target=run_flask, daemon=True).start()

    application = ApplicationBuilder().token(TOKEN).build()

    # Handlers
    application.add_handler(CommandHandler("start", start_command))
    application.add_handler(ChatMemberHandler(handle_bot_promoted, ChatMemberHandler.MY_CHAT_MEMBER))
    application.add_handler(CallbackQueryHandler(handle_callbacks))
    application.add_handler(MessageHandler(filters.FORWARDED, handle_forwarded_content))
    application.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, handle_text))

    job_queue = application.job_queue
    if job_queue:
        job_queue.run_repeating(dispatch_scheduled_batch, interval=30, first=5)

    application.run_polling(drop_pending_updates=True)

if __name__ == "__main__":
    main()
