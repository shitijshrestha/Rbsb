
import os, json, time, threading, subprocess, requests, telebot
from flask import Flask

bot = telebot.TeleBot(os.getenv("6403142441"), parse_mode="HTML")
app = Flask(__name__)
ADMIN = int(os.getenv("PERMANENT_ADMIN", "6403142441")
DUMP = int(os.getenv("-1002627919828")
DEFAULT = os.getenv("PLAYLIST_URL", "https://iptv-org.github.io/iptv/index.m3u")

def load(f, d):
    try:
        with open(f) as x: return json.load(x)
    except: return d

def save(f, d):
    with open(f, "w") as x: json.dump(d, x)

urls = load("playlists.json", [DEFAULT])
users = set(load("approved_users.json", []))
channels, jobs = [], {}
lock = threading.RLock()

def allowed(m):
    return m.from_user.id == ADMIN or m.from_user.id in users

def fetch(url):
    r = requests.get(url, timeout=30)
    r.raise_for_status()
    if "#EXTM3U" not in r.text[:500]: raise ValueError("Invalid M3U")
    result, name = [], "Unknown"
    for line in r.text.splitlines():
        line = line.strip()
        if line.startswith("#EXTINF"):
            name = line.split(",", 1)[-1].strip()
        elif line and not line.startswith("#") and line.startswith(("http://", "https://")):
            result.append({"name": name, "url": line})
            name = "Unknown"
    if not result: raise ValueError("No channels found")
    return result

def reload_all():
    global channels
    data, errors = [], []
    for url in urls:
        try: data.extend(fetch(url))
        except Exception as e: errors.append(str(e))
    if data:
        channels = [{"id": i, **c} for i, c in enumerate(data, 1)]
    return len(channels), errors

reload_all()

def send(chat, text):
    for i in range(0, len(text), 3500):
        bot.send_message(chat, text[i:i+3500])

def upload(chat, file, title):
    try:
        thumb = file + ".jpg"
        subprocess.run(["ffmpeg", "-y", "-ss", "3", "-i", file,
                        "-frames:v", "1", "-vf", "scale=320:-1", thumb],
                       stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        with open(file, "rb") as v:
            if os.path.exists(thumb):
                with open(thumb, "rb") as t:
                    bot.send_video(chat, v, caption=title[:1024],
                                   thumb=t, supports_streaming=True, timeout=300)
            else:
                bot.send_video(chat, v, caption=title[:1024],
                               supports_streaming=True, timeout=300)
        if os.path.exists(thumb): os.remove(thumb)
        return True
    except Exception as e:
        print("Upload:", e)
        return False

def worker(key, chat, url, mins, title):
    folder = f"recordings/{key}"
    os.makedirs(folder, exist_ok=True)
    listing = folder + "/list.csv"
    pattern = folder + "/part_%04d.mp4"
    cmd = ["ffmpeg", "-y", "-reconnect", "1", "-reconnect_streamed", "1",
           "-reconnect_delay_max", "10", "-i", url, "-t", str(mins*60),
           "-map", "0:v:0?", "-map", "0:a:0?", "-c:v", "libx264",
           "-preset", "veryfast", "-crf", "23", "-c:a", "aac",
           "-f", "segment", "-segment_time", "180", "-reset_timestamps", "1",
           "-segment_list", listing, "-segment_list_type", "csv", pattern]
    p = None
    done = set()
    try:
        bot.send_message(chat, f"🔴 Recording: <b>{title}</b>")
        p = subprocess.Popen(cmd, stdout=subprocess.DEVNULL, stderr=subprocess.PIPE)
        jobs[key]["process"] = p
        while p.poll() is None:
            if jobs.get(key, {}).get("stop"):
                p.terminate()
                break
            if os.path.exists(listing):
                with open(listing) as f:
                    for row in __import__("csv").reader(f):
                        if row and row[0] not in done and os.path.exists(row[0]):
                            done.add(row[0])
                            a = upload(chat, row[0], title)
                            b = upload(DUMP, row[0], title) if DUMP else True
                            if a and b: os.remove(row[0])
            time.sleep(3)
        if p.poll() is None: p.wait()
        if os.path.exists(listing):
            with open(listing) as f:
                for row in __import__("csv").reader(f):
                    if row and row[0] not in done and os.path.exists(row[0]):
                        a = upload(chat, row[0], title)
                        b = upload(DUMP, row[0], title) if DUMP else True
                        if a and b: os.remove(row[0])
        bot.send_message(chat, "✅ Recording finished!")
    except Exception as e:
        bot.send_message(chat, f"❌ {str(e)[:700]}")
    finally:
        if p and p.poll() is None: p.terminate()
        jobs.pop(key, None)

def record(chat, url, mins, title):
    if not url.startswith(("http://", "https://")) or not 1 <= mins <= 1440:
        return bot.send_message(chat, "❌ Invalid URL/minutes")
    if any(j["chat"] == chat for j in jobs.values()):
        return bot.send_message(chat, "⚠️ Recording already running")
    key = f"{chat}_{int(time.time())}"
    jobs[key] = {"chat": chat, "stop": False, "start": time.time(), "title": title}
    threading.Thread(target=worker, args=(key, chat, url, mins, title), daemon=True).start()

@bot.message_handler(commands=["start", "help"])
def help_cmd(m):
    bot.reply_to(m, "/playlist - All channels\n/p NAME - Search ID\n/find NAME - Search\n"
                    "/rec ID MINUTES - Record\n/record URL MINUTES\n/status\n/stop\n"
                    "/addplaylist URL (Admin)\n/reloadplaylist (Admin)\n"
                    "/approve ID /revoke ID /users /stopall")

@bot.message_handler(commands=["addplaylist"])
def add(m):
    if m.from_user.id != ADMIN: return
    a = m.text.split(maxsplit=1)
    if len(a) < 2: return bot.reply_to(m, "Usage: /addplaylist URL")
    try:
        new = fetch(a[1])
        if a[1] not in urls:
            urls.append(a[1])
            save("playlists.json", urls)
        n, err = reload_all()
        bot.reply_to(m, f"✅ Added {len(new)} channels. Total: {n}")
        if err: send(m.chat.id, "\n".join(err))
    except Exception as e: bot.reply_to(m, f"❌ {e}")

@bot.message_handler(commands=["reloadplaylist"])
def reload_cmd(m):
    if m.from_user.id != ADMIN: return
    n, err = reload_all()
    bot.reply_to(m, f"✅ Reloaded. Channels: {n}")
    if err: send(m.chat.id, "\n".join(err))

@bot.message_handler(commands=["playlist", "p", "find"])
def search(m):
    if not allowed(m): return
    a = m.text.split(maxsplit=1)
    key = a[1].lower() if len(a) > 1 else ""
    if m.text.startswith(("/p", "/find")) and not key:
        return bot.reply_to(m, "Usage: /p Channel Name")
    result = [c for c in channels if key in c["name"].lower()]
    if not result: return bot.reply_to(m, "❌ No channels found")
    send(m.chat.id, "\n".join(
        f"🆔 <code>{c['id']}</code> — {c['name']}" for c in result
    ) + "\n\n/rec ID MINUTES")

@bot.message_handler(commands=["rec"])
def rec(m):
    if not allowed(m): return
    a = m.text.split(maxsplit=3)
    try:
        c = next(x for x in channels if x["id"] == int(a[1]))
        record(m.chat.id, c["url"], int(a[2]), a[3] if len(a)>3 else c["name"])
    except: bot.reply_to(m, "Usage: /rec ID MINUTES")

@bot.message_handler(commands=["record"])
def direct(m):
    if not allowed(m): return
    a = m.text.split(maxsplit=3)
    try: record(m.chat.id, a[1], int(a[2]), a[3] if len(a)>3 else "Recording")
    except: bot.reply_to(m, "Usage: /record URL MINUTES")

@bot.message_handler(commands=["status"])
def status(m):
    if not jobs: return bot.reply_to(m, "No active recordings")
    send(m.chat.id, "\n".join(f"{j['title']} - {int(time.time()-j['start'])} sec" for j in jobs.values()))

@bot.message_handler(commands=["stop", "stopall"])
def stop(m):
    if m.text.startswith("/stopall") and m.from_user.id != ADMIN: return
    n = 0
    for j in jobs.values():
        if m.from_user.id == ADMIN or j["chat"] == m.chat.id:
            j["stop"] = True
            if j.get("process"): j["process"].terminate()
            n += 1
    bot.reply_to(m, f"🛑 Stop request: {n}")

@bot.message_handler(commands=["approve", "revoke"])
def access(m):
    if m.from_user.id != ADMIN: return
    try:
        uid = int(m.text.split()[1])
        if m.text.startswith("/approve"): users.add(uid)
        else: users.discard(uid)
        save("approved_users.json", list(users))
        bot.reply_to(m, "✅ Updated")
    except: bot.reply_to(m, "Usage: /approve USER_ID")

@bot.message_handler(commands=["users"])
def users_cmd(m):
    if m.from_user.id == ADMIN:
        send(m.chat.id, "\n".join(map(str, sorted(users))) or "No users")

@app.route("/")
@app.route("/health")
def health():
    return {"status": "running", "channels": len(channels), "jobs": len(jobs)}

if __name__ == "__main__":
    threading.Thread(target=lambda: app.run(host="0.0.0.0", port=int(os.getenv("PORT", "10000"))), daemon=True).start()
    bot.infinity_polling(skip_pending=True)
    
