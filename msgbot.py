import os
import re
import sqlite3
import threading
from datetime import datetime
import pytz
from flask import Flask
from telegram import (
    Update,
    ReplyKeyboardMarkup,
    KeyboardButton,
    InlineKeyboardMarkup,
    InlineKeyboardButton,
    KeyboardButtonRequestChat
)
from telegram.ext import (
    Application,
    CommandHandler,
    MessageHandler,
    CallbackQueryHandler,
    ConversationHandler,
    ContextTypes,
    filters
)

BOT_TOKEN = "8863781796:AAFTF6HVU5fD653V3lCgnJw2echi4iENRM0"
TIMEZONE = pytz.timezone("Asia/Dhaka")

# State definitions for conversations
SET_COUNT_STATE, DELETE_QUEUE_STATE, SET_BACKUP_STATE = range(3)

# ----------------- FLASK DUMMY SERVER FOR RENDER -----------------
app = Flask(__name__)

@app.route('/')
def home():
    return "Bot is running 24/7!"

def run_flask():
    port = int(os.environ.get("PORT", 8080))
    app.run(host="0.0.0.0", port=port)

# ----------------- DATABASE SETUP -----------------
DB_PATH = "bot_data.db"

def init_db():
    conn = sqlite3.connect(DB_PATH)
    cursor = conn.cursor()
    cursor.execute('''
        CREATE TABLE IF NOT EXISTS channels (
            channel_id TEXT PRIMARY KEY,
            title TEXT
        )
    ''')
    cursor.execute('''
        CREATE TABLE IF NOT EXISTS timeslots (
            time_str TEXT PRIMARY KEY
        )
    ''')
    cursor.execute('''
        CREATE TABLE IF NOT EXISTS queue (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            file_id TEXT,
            media_type TEXT,
            caption TEXT,
            flezen_link TEXT
        )
    ''')
    cursor.execute('''
        CREATE TABLE IF NOT EXISTS settings (
            key TEXT PRIMARY KEY,
            val TEXT
        )
    ''')
    # Default settings
    cursor.execute("INSERT OR IGNORE INTO settings (key, val) VALUES ('batch_count', '1')")
    cursor.execute("INSERT OR IGNORE INTO settings (key, val) VALUES ('backup_link', '')")
    conn.commit()
    conn.close()

init_db()

# DB Helpers
def get_db():
    return sqlite3.connect(DB_PATH)

def get_setting(key):
    conn = get_db()
    c = conn.cursor()
    c.execute("SELECT val FROM settings WHERE key=?", (key,))
    row = c.fetchone()
    conn.close()
    return row[0] if row else None

def set_setting(key, val):
    conn = get_db()
    c = conn.cursor()
    c.execute("UPDATE settings SET val=? WHERE key=?", (val, key))
    conn.commit()
    conn.close()

# ----------------- KEYBOARD MENUS -----------------
def get_main_menu():
    keyboard = [
        [
            KeyboardButton("📢 Add Channel", request_chat=KeyboardButtonRequestChat(request_id=1, chat_is_channel=True)),
            KeyboardButton("⏰ Set Time")
        ],
        [
            KeyboardButton("🔢 Set Count"),
            KeyboardButton("🗑️ Delete Channel")
        ],
        [
            KeyboardButton("📋 Queue"),
            KeyboardButton("❌ Delete Queue")
        ],
        [
            KeyboardButton("🔗 Set Backup Link")
        ]
    ]
    return ReplyKeyboardMarkup(keyboard, resize_keyboard=True)

# ----------------- HANDLERS -----------------
async def start_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    welcome_text = (
        "👋 **Welcome to Channel Broadcaster Bot!**\n\n"
        "Here are the controls:\n"
        "• **Add Channel**: Add destination channels.\n"
        "• **Set Time**: Select daily broadcast slots (every 30 mins).\n"
        "• **Set Count**: How many posts to send per time slot.\n"
        "• **Delete Channel**: Remove channels from broadcast list.\n"
        "• **Queue**: View current queue status, channels & scheduled times.\n"
        "• **Delete Queue**: Drop last items from queue.\n"
        "• **Set Backup Link**: Configure the backup channel link.\n\n"
        "💡 *Forward any photo or video with Flezen link to automatically add it to the queue!*"
    )
    await update.message.reply_text(welcome_text, reply_markup=get_main_menu(), parse_mode="Markdown")

# Add Channel Handler via request_chat or forward
async def handle_chat_shared(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if update.message.chat_shared:
        chat_id = str(update.message.chat_shared.chat_id)
        try:
            chat = await context.bot.get_chat(chat_id)
            title = chat.title or f"Channel ({chat_id})"
        except Exception:
            title = f"Channel ({chat_id})"
        
        conn = get_db()
        c = conn.cursor()
        c.execute("INSERT OR REPLACE INTO channels (channel_id, title) VALUES (?, ?)", (chat_id, title))
        conn.commit()
        conn.close()
        
        await update.message.reply_text(f"✅ Successfully added channel:\n**{title}** (`{chat_id}`)\n\n*Make sure this bot is added as an administrator in the channel!*", parse_mode="Markdown")

# Set Time Slot Matrix (24 hours with 30 min interval)
async def show_time_matrix(update: Update, context: ContextTypes.DEFAULT_TYPE):
    conn = get_db()
    c = conn.cursor()
    c.execute("SELECT time_str FROM timeslots")
    active_slots = set(r[0] for r in c.fetchall())
    conn.close()

    keyboard = []
    row = []
    for h in range(24):
        for m in (0, 30):
            t_str = f"{h:02d}:{m:02d}"
            status = "✅ " if t_str in active_slots else ""
            btn = InlineKeyboardButton(f"{status}{t_str}", callback_data=f"toggle_time_{t_str}")
            row.append(btn)
            if len(row) == 4:
                keyboard.append(row)
                row = []
    if row:
        keyboard.append(row)
    
    keyboard.append([InlineKeyboardButton("💾 Done / Close", callback_data="close_time_menu")])
    reply_markup = InlineKeyboardMarkup(keyboard)
    
    if update.callback_query:
        await update.callback_query.edit_message_text("Select schedule times (Click to toggle on/off):", reply_markup=reply_markup)
    else:
        await update.message.reply_text("Select schedule times (Click to toggle on/off):", reply_markup=reply_markup)

async def toggle_time_callback(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    
    if query.data == "close_time_menu":
        await query.edit_message_text("✅ Time schedule configuration saved!")
        return

    time_str = query.data.replace("toggle_time_", "")
    conn = get_db()
    c = conn.cursor()
    c.execute("SELECT 1 FROM timeslots WHERE time_str=?", (time_str,))
    exists = c.fetchone()
    if exists:
        c.execute("DELETE FROM timeslots WHERE time_str=?", (time_str,))
    else:
        c.execute("INSERT INTO timeslots (time_str) VALUES (?)", (time_str,))
    conn.commit()
    conn.close()

    await show_time_matrix(update, context)

# Set Count Flow
async def ask_set_count(update: Update, context: ContextTypes.DEFAULT_TYPE):
    curr = get_setting("batch_count")
    await update.message.reply_text(f"Currently sending **{curr}** post(s) per scheduled time.\n\nPlease type the new number of messages to send at once:")
    return SET_COUNT_STATE

async def save_set_count(update: Update, context: ContextTypes.DEFAULT_TYPE):
    text = update.message.text.strip()
    if not text.isdigit() or int(text) <= 0:
        await update.message.reply_text("❌ Invalid input! Please enter a positive number (e.g., 1, 2, 5).")
        return SET_COUNT_STATE
    
    set_setting("batch_count", text)
    await update.message.reply_text(f"✅ Batch count updated to **{text}** messages per slot.", reply_markup=get_main_menu(), parse_mode="Markdown")
    return ConversationHandler.END

# Delete Channel Flow
async def list_delete_channels(update: Update, context: ContextTypes.DEFAULT_TYPE):
    conn = get_db()
    c = conn.cursor()
    c.execute("SELECT channel_id, title FROM channels")
    rows = c.fetchall()
    conn.close()

    if not rows:
        await update.message.reply_text("ℹ️ No channels have been added yet.")
        return

    keyboard = []
    for cid, title in rows:
        btn = InlineKeyboardButton(f"🗑️ Delete: {title[:20]}", callback_data=f"del_chan_{cid}")
        keyboard.append([btn])
    
    await update.message.reply_text("Select a channel to remove:", reply_markup=InlineKeyboardMarkup(keyboard))

async def delete_channel_callback(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    cid = query.data.replace("del_chan_", "")
    
    conn = get_db()
    c = conn.cursor()
    c.execute("DELETE FROM channels WHERE channel_id=?", (cid,))
    conn.commit()
    conn.close()
    
    await query.edit_message_text("✅ Channel successfully removed.")

# Queue Status Flow
async def show_queue_status(update: Update, context: ContextTypes.DEFAULT_TYPE):
    conn = get_db()
    c = conn.cursor()
    
    c.execute("SELECT COUNT(*) FROM queue")
    queue_count = c.fetchone()[0]

    c.execute("SELECT title, channel_id FROM channels")
    channels = c.fetchall()

    c.execute("SELECT time_str FROM timeslots ORDER BY time_str ASC")
    timeslots = [r[0] for r in c.fetchall()]
    conn.close()

    batch_count = get_setting("batch_count")
    backup_link = get_setting("backup_link") or "Not set"

    channels_txt = "\n".join([f"• {title} (`{cid}`)" for cid, title in channels]) if channels else "None"
    times_txt = ", ".join(timeslots) if timeslots else "None"

    status_msg = (
        f"📊 **Current System Status**\n\n"
        f"📦 **Queue Count:** {queue_count} post(s) waiting\n"
        f"🔢 **Posts per Batch:** {batch_count}\n"
        f"🔗 **Backup Link:** {backup_link}\n\n"
        f"📢 **Connected Channels ({len(channels)}):**\n{channels_txt}\n\n"
        f"⏰ **Active Time Slots:**\n{times_txt}"
    )
    await update.message.reply_text(status_msg, parse_mode="Markdown")

# Delete Queue Flow
async def ask_delete_queue(update: Update, context: ContextTypes.DEFAULT_TYPE):
    conn = get_db()
    c = conn.cursor()
    c.execute("SELECT COUNT(*) FROM queue")
    queue_count = c.fetchone()[0]
    conn.close()

    if queue_count == 0:
        await update.message.reply_text("ℹ️ The queue is already empty.")
        return ConversationHandler.END

    await update.message.reply_text(f"Currently **{queue_count}** item(s) in queue.\nHow many latest items would you like to delete from the queue?")
    return DELETE_QUEUE_STATE

async def save_delete_queue(update: Update, context: ContextTypes.DEFAULT_TYPE):
    text = update.message.text.strip()
    if not text.isdigit() or int(text) <= 0:
        await update.message.reply_text("❌ Please enter a valid positive number.")
        return DELETE_QUEUE_STATE
    
    count = int(text)
    conn = get_db()
    c = conn.cursor()
    c.execute("DELETE FROM queue WHERE id IN (SELECT id FROM queue ORDER BY id DESC LIMIT ?)", (count,))
    deleted = c.rowcount
    conn.commit()
    conn.close()

    await update.message.reply_text(f"✅ Deleted {deleted} item(s) from the end of the queue.", reply_markup=get_main_menu())
    return ConversationHandler.END

# Set Backup Link Flow
async def ask_backup_link(update: Update, context: ContextTypes.DEFAULT_TYPE):
    current = get_setting("backup_link") or "None"
    await update.message.reply_text(f"Current backup link: {current}\n\nPlease send the new backup channel link (e.g. `https://t.me/yourbackup`):")
    return SET_BACKUP_STATE

async def save_backup_link(update: Update, context: ContextTypes.DEFAULT_TYPE):
    link = update.message.text.strip()
    set_setting("backup_link", link)
    await update.message.reply_text(f"✅ Backup channel link updated to:\n{link}", reply_markup=get_main_menu())
    return ConversationHandler.END

# Forward Receiver (Auto Queue)
async def handle_incoming_media(update: Update, context: ContextTypes.DEFAULT_TYPE):
    msg = update.message
    caption = msg.caption or ""
    
    # Extract flezen link
    flezen_match = re.search(r'(https?://[^\s]*flezen[^\s]*)', caption, re.IGNORECASE)
    if not flezen_match:
        # Fallback to check any URL if flezen domain differs
        urls = re.findall(r'(https?://[^\s]+)', caption)
        flezen_link = urls[0] if urls else ""
    else:
        flezen_link = flezen_match.group(1)

    file_id = None
    media_type = None

    if msg.photo:
        file_id = msg.photo[-1].file_id
        media_type = "photo"
    elif msg.video:
        file_id = msg.video.file_id
        media_type = "video"
    else:
        await msg.reply_text("⚠️ Please send or forward a photo or video.")
        return

    conn = get_db()
    c = conn.cursor()
    c.execute(
        "INSERT INTO queue (file_id, media_type, caption, flezen_link) VALUES (?, ?, ?, ?)",
        (file_id, media_type, caption, flezen_link)
    )
    conn.commit()
    c.execute("SELECT COUNT(*) FROM queue")
    total_q = c.fetchone()[0]
    conn.close()

    await msg.reply_text(f"📥 **Added to Queue!**\nTotal waiting items: **{total_q}**", parse_mode="Markdown")

# ----------------- BROADCAST WORKER -----------------
async def check_and_broadcast(context: ContextTypes.DEFAULT_TYPE):
    now = datetime.now(TIMEZONE)
    current_time_str = now.strftime("%H:%M")

    conn = get_db()
    c = conn.cursor()
    c.execute("SELECT 1 FROM timeslots WHERE time_str=?", (current_time_str,))
    matched = c.fetchone()
    
    if not matched:
        conn.close()
        return

    # Fetch batch count & backup link
    c.execute("SELECT val FROM settings WHERE key='batch_count'")
    batch_count = int(c.fetchone()[0])
    
    c.execute("SELECT val FROM settings WHERE key='backup_link'")
    backup_link = c.fetchone()[0]

    # Fetch channels
    c.execute("SELECT channel_id FROM channels")
    channels = [r[0] for r in c.fetchall()]

    if not channels:
        conn.close()
        return

    # Fetch items from queue
    c.execute("SELECT id, file_id, media_type, flezen_link FROM queue ORDER BY id ASC LIMIT ?", (batch_count,))
    items = c.fetchall()

    if not items:
        conn.close()
        return

    # Format caption
    for item_id, file_id, media_type, flezen_link in items:
        formatted_caption = (
            "FULL VIDEO LINK 👇👇\n"
            f"{flezen_link}\n\n"
            "NEW BACKUP CHANNEL 👇\n"
            f"{backup_link}"
        )

        for ch_id in channels:
            try:
                if media_type == "photo":
                    await context.bot.send_photo(chat_id=ch_id, photo=file_id, caption=formatted_caption)
                elif media_type == "video":
                    await context.bot.send_video(chat_id=ch_id, video=file_id, caption=formatted_caption)
            except Exception as e:
                print(f"Error sending to {ch_id}: {e}")

        # Remove sent item from queue
        c.execute("DELETE FROM queue WHERE id=?", (item_id,))
        conn.commit()

    conn.close()

# ----------------- MAIN INITIALIZER -----------------
def main():
    # Run Flask in background thread for Render keep-alive
    t = threading.Thread(target=run_flask)
    t.daemon = True
    t.start()

    # Build Telegram Bot
    application = Application.builder().token(BOT_TOKEN).build()

    # Conversation Handlers
    conv_set_count = ConversationHandler(
        entry_points=[MessageHandler(filters.Regex("^🔢 Set Count$"), ask_set_count)],
        states={SET_COUNT_STATE: [MessageHandler(filters.TEXT & ~filters.COMMAND, save_set_count)]},
        fallbacks=[]
    )
    conv_del_queue = ConversationHandler(
        entry_points=[MessageHandler(filters.Regex("^❌ Delete Queue$"), ask_delete_queue)],
        states={DELETE_QUEUE_STATE: [MessageHandler(filters.TEXT & ~filters.COMMAND, save_delete_queue)]},
        fallbacks=[]
    )
    conv_backup = ConversationHandler(
        entry_points=[MessageHandler(filters.Regex("^🔗 Set Backup Link$"), ask_backup_link)],
        states={SET_BACKUP_STATE: [MessageHandler(filters.TEXT & ~filters.COMMAND, save_backup_link)]},
        fallbacks=[]
    )

    # Handlers
    application.add_handler(CommandHandler("start", start_command))
    application.add_handler(conv_set_count)
    application.add_handler(conv_del_queue)
    application.add_handler(conv_backup)
    
    application.add_handler(MessageHandler(filters.Regex("^⏰ Set Time$"), show_time_matrix))
    application.add_handler(MessageHandler(filters.Regex("^🗑️ Delete Channel$"), list_delete_channels))
    application.add_handler(MessageHandler(filters.Regex("^📋 Queue$"), show_queue_status))
    application.add_handler(MessageHandler(filters.StatusUpdate.CHAT_SHARED, handle_chat_shared))
    
    # Callback queries
    application.add_handler(CallbackQueryHandler(toggle_time_callback, pattern="^(toggle_time_|close_time_menu)"))
    application.add_handler(CallbackQueryHandler(delete_channel_callback, pattern="^del_chan_"))

    # Media forward / send listener
    application.add_handler(MessageHandler(filters.PHOTO | filters.VIDEO, handle_incoming_media))

    # Job queue: runs every 60 seconds to check matching time slot
    job_queue = application.job_queue
    job_queue.run_repeating(check_and_broadcast, interval=60, first=10)

    # Start bot
    print("Bot polling started...")
    application.run_polling()

if __name__ == "__main__":
    main()
