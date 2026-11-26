import os
import time
import threading
import requests
import subprocess
from datetime import datetime
from PIL import Image
import telebot
from flask import Flask

# ---------------- CONFIG ----------------
BOT_TOKEN = "9231852175:AAEmC4uuHjhxiSts_gyBUgsSoQWBHEr4e4c"  # your bot token
PERMANENT_ADMIN = 6403142441        # Telegram ID of admin
DUMP_CHANNEL_ID = -1002627919828    # dump channel ID
PLAYLIST_URL = "https://iptv-org.github.io/iptv/index.m3u"  # public playlist
SEGMENT_SECONDS = 180   # 3 minutes per segment
APP_ROOT = os.path.join(os.getcwd(), "recordings")
os.makedirs(APP_ROOT, exist_ok=True)

# Initialize Flask app
app = Flask(__name__)

bot = telebot.TeleBot(BOT_TOKEN, threaded=True)
PLAYLIST = []
APPROVED_USERS = set()  # users approved by admin

# ---------------- Flask Routes ----------------
@app.route('/')
def home():
    return "🤖 IPTV Recorder Bot is running!"

@app.route('/health')
def health():
    return {"status": "healthy", "channels_loaded": len(PLAYLIST)}

@app.route('/playlist')
def playlist_status():
    return {"total_channels": len(PLAYLIST), "playlist_url": PLAYLIST_URL}

# ---------------- Admin Decorator ----------------
def admin_only(func):
    def wrapper(message, *args, **kwargs):
        uid = message.from_user.id if message.from_user else None
        if uid != PERMANENT_ADMIN:
            bot.reply_to(message, "🚫 Access denied. Admin only.")
            return
        return func(message, *args, **kwargs)
    return wrapper

# ---------------- Approved User Decorator ----------------
def approved_only(func):
    def wrapper(message, *args, **kwargs):
        uid = message.from_user.id if message.from_user else None
        if uid != PERMANENT_ADMIN and uid not in APPROVED_USERS:
            bot.reply_to(message, "🚫 You are not approved. Ask admin to approve you.")
            return
        return func(message, *args, **kwargs)
    return wrapper

# ---------------- Playlist Functions ----------------
def fetch_playlist(url=PLAYLIST_URL):
    try:
        r = requests.get(url, timeout=20)
        r.raise_for_status()
        text = r.text
    except Exception as e:
        print("Playlist fetch failed:", e)
        return []

    lines = [l.strip() for l in text.splitlines() if l.strip() != ""]
    res = []
    cur_name = None
    idx = 1
    for line in lines:
        if line.startswith("#EXTINF"):
            parts = line.split(",", 1)
            cur_name = parts[1].strip() if len(parts) > 1 else "unknown"
        elif line.startswith("#"):
            continue
        else:
            url_line = line
            name = cur_name or url_line
            res.append({"id": idx, "name": name, "url": url_line})
            idx += 1
            cur_name = None
    return res

def load_playlist():
    global PLAYLIST
    PLAYLIST = fetch_playlist()
    print(f"Loaded playlist: {len(PLAYLIST)} channels")

load_playlist()

# ---------------- Utility ----------------
def human_duration(seconds: int) -> str:
    h = seconds // 3600
    m = (seconds % 3600) // 60
    s = seconds % 60
    if h:
        return f"{h} hr {m} min {s} sec"
    if m:
        return f"{m} min {s} sec"
    return f"{s} sec"

def safe_filename(s: str) -> str:
    return "".join(c if c.isalnum() or c in "-_." else "_" for c in s)[:200]

def generate_thumbnail(video_path, thumb_path):
    try:
        subprocess.run([
            "ffmpeg", "-y", "-i", video_path, "-ss", "00:00:01.000",
            "-vframes", "1", thumb_path
        ], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, check=True)
    except:
        pass

# ---------------- Recording ----------------
def record_by_url(chat_id, url, duration_minutes, title=None):
    title = title or "Recording"
    total_seconds = int(duration_minutes) * 60
    if total_seconds <= 0:
        bot.send_message(chat_id, "❌ Duration must be > 0")
        return

    segs = (total_seconds + SEGMENT_SECONDS - 1) // SEGMENT_SECONDS
    bot.send_message(chat_id, f"🎬 Recording: {title}\n🔗 {url}\n⏱ {human_duration(total_seconds)}\n✂️ Splitting into {segs} parts ({SEGMENT_SECONDS}s)")

    job_dir = os.path.join(APP_ROOT, safe_filename(f"{title}_{datetime.now().strftime('%Y%m%d_%H%M%S')}"))
    os.makedirs(job_dir, exist_ok=True)
    out_pattern = os.path.join(job_dir, "seg_%05d.mp4")
    ffmpeg_cmd = [
        "ffmpeg", "-y", "-nostdin", "-i", url,
        "-c", "copy", "-f", "segment",
        "-segment_time", str(SEGMENT_SECONDS),
        "-reset_timestamps", "1",
        "-t", str(total_seconds),
        out_pattern
    ]

    proc = subprocess.Popen(ffmpeg_cmd, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    uploaded_paths = set()
    try:
        while True:
            files = sorted([f for f in os.listdir(job_dir) if f.startswith("seg_") and f.endswith(".mp4")])
            for fname in files:
                path = os.path.join(job_dir, fname)
                if path in uploaded_paths:
                    continue
                if os.path.getsize(path) == 0:
                    continue

                part_index = len(uploaded_paths) + 1
                thumb_path = os.path.join(job_dir, f"thumb_{part_index}.jpg")
                generate_thumbnail(path, thumb_path)
                caption = f"🎬 {title}\n📦 Part {part_index}/{segs}\n⏱ {SEGMENT_SECONDS} sec"

                for target_id in [chat_id, DUMP_CHANNEL_ID]:
                    try:
                        with open(path, "rb") as f:
                            if os.path.exists(thumb_path):
                                with open(thumb_path, "rb") as t:
                                    bot.send_video(target_id, f, caption=caption, supports_streaming=True, thumb=t)
                            else:
                                bot.send_video(target_id, f, caption=caption, supports_streaming=True)
                    except Exception as e:
                        bot.send_message(chat_id, f"❌ Upload failed for part {part_index} to {target_id}: {e}")
                        continue

                uploaded_paths.add(path)
                os.remove(path)
                if os.path.exists(thumb_path):
                    os.remove(thumb_path)

                bot.send_message(chat_id, f"✅ Uploaded part {part_index}/{segs}")

            if proc.poll() is not None:
                remaining = [f for f in os.listdir(job_dir) if f.startswith("seg_") and f.endswith(".mp4") and os.path.join(job_dir, f) not in uploaded_paths]
                if not remaining:
                    break
            time.sleep(1)

        bot.send_message(chat_id, f"✅ Recording finished. Uploaded {len(uploaded_paths)} part(s).")
    finally:
        try:
            if proc.poll() is None:
                proc.terminate()
        except:
            pass
        try:
            for f in os.listdir(job_dir):
                os.remove(os.path.join(job_dir, f))
            os.rmdir(job_dir)
        except:
            pass

# ---------------- Commands ----------------
@bot.message_handler(commands=['start'])
def cmd_start(m):
    bot.reply_to(m, "🤖 IPTV Recorder Bot\nUse /help for commands. Admin/approved users only.")

@bot.message_handler(commands=['help'])
def cmd_help(m):
    text = (
        "/find <keyword> — search playlist\n"
        "/rec <channel_id> <minutes> [title] — record channel by id\n"
        "/record <url> <minutes> [title] — record direct URL\n"
        "/playlist — show first 20 channels\n"
        "/reloadplaylist — reload playlist (admin only)\n"
        "/approve <user_id> — admin approve user\n"
    )
    bot.reply_to(m, text)

@bot.message_handler(commands=['reloadplaylist'])
@admin_only
def cmd_reload(m):
    bot.reply_to(m, "🔄 Reloading playlist...")
    load_playlist()
    bot.send_message(m.chat.id, f"✅ Playlist reloaded: {len(PLAYLIST)} channels")

@bot.message_handler(commands=['playlist'])
@approved_only
def cmd_playlist(m):
    if not PLAYLIST:
        bot.reply_to(m, "Playlist empty.")
        return
    lines = [f"{e['id']:03d}: {e['name']}" for e in PLAYLIST[:20]]
    bot.reply_to(m, "Playlist (first 20):\n" + "\n".join(lines))

@bot.message_handler(commands=['find'])
@approved_only
def cmd_find(m):
    try:
        q = m.text.split(maxsplit=1)[1].strip().lower()
    except:
        bot.reply_to(m, "Usage: /find <keyword>")
        return
    found = [e for e in PLAYLIST if q in e['name'].lower()]
   
    if not found:
        bot.reply_to(m, "No channels found for: " + q)
        return
    lines = [f"{e['id']:03d} • {e['name']}\n    {e['url']}" for e in found[:30]]
    bot.send_message(m.chat.id, "🔎 Search results:\n\n" + "\n\n".join(lines))

@bot.message_handler(commands=['rec'])
@approved_only
def cmd_rec(m):
    parts = m.text.split()
    if len(parts) < 3:
        bot.reply_to(m, "Usage: /rec <channel_id> <minutes> [title]")
        return
    try:
        cid = int(parts[1])
        dur = int(parts[2])
    except:
        bot.reply_to(m, "Invalid input.")
        return
    title = " ".join(parts[3:]) if len(parts) > 3 else None
    entry = next((e for e in PLAYLIST if e['id'] == cid), None)
    if not entry:
        bot.reply_to(m, f"Channel id {cid} not found.")
        return
    bot.reply_to(m, f"Scheduled record: {entry['name']} for {dur} min. Starting now...")
    threading.Thread(target=record_by_url, args=(m.chat.id, entry['url'], dur, title or entry['name']), daemon=True).start()

@bot.message_handler(commands=['record'])
@approved_only
def cmd_record(m):
    parts = m.text.split()
    if len(parts) < 3:
        bot.reply_to(m, "Usage: /record <url> <minutes> [title]")
        return
    url = parts[1]
    try:
        dur = int(parts[2])
    except:
        bot.reply_to(m, "Invalid duration.")
        return
    title = " ".join(parts[3:]) if len(parts) > 3 else None
    bot.reply_to(m, f"Scheduled record for {url} for {dur} min. Starting now...")
    threading.Thread(target=record_by_url, args=(m.chat.id, url, dur, title), daemon=True).start()

@bot.message_handler(commands=['approve'])
@admin_only
def cmd_approve(m):
    parts = m.text.split()
    if len(parts) != 2:
        bot.reply_to(m, "Usage: /approve <user_id>")
        return
    try:
        uid = int(parts[1])
    except:
        bot.reply_to(m, "Invalid user_id.")
        return
    APPROVED_USERS.add(uid)
    bot.send_message(uid, "✅ You are approved to use the bot now!")
    bot.reply_to(m, f"User {uid} approved.")

def run_bot():
    """Run the Telegram bot"""
    print("🤖 Bot started. Listening for commands...")
    bot.infinity_polling()

def run_flask():
    """Run the Flask web server"""
    port = int(os.environ.get('PORT', 5000))
    app.run(host='0.0.0.0', port=port, debug=False)

# ---------------- Startup ----------------
if __name__ == "__main__":
    print("🚀 Starting IPTV Recorder Bot...")
    
    flask_thread = threading.Thread(target=run_flask, daemon=True)
    flask_thread.start()
    
    print(f"🌐 Flask server running on port {os.environ.get('PORT', 5000)}")
    print("🤖 Starting Telegram bot...")
    
    # Run bot in main thread
    run_bot()
