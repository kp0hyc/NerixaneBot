import asyncio
import hashlib
import html
import json
import logging
import os
import re

from dotenv import load_dotenv

import gspread
from oauth2client.service_account import ServiceAccountCredentials

from datetime import datetime, timedelta

from telethon import TelegramClient, events, types
from telethon.tl.types import ChannelParticipantsAdmins

from telegram import (
    constants,
    InlineKeyboardButton,
    InlineKeyboardMarkup,
    Update,
)
from telegram.error import BadRequest, Forbidden, TimedOut
from telegram.ext import (
    ApplicationBuilder,
    CallbackContext,
    CommandHandler,
    ContextTypes,
    MessageHandler,
    filters,
)

# ─── CONFIG ─────────────────────────────────────────────────────────────────────
load_dotenv()

BOT_TOKEN         = os.environ["BOT_TOKEN"]
API_ID            = int(os.environ["API_ID"])
API_HASH          = os.environ["API_HASH"]

SPREADSHEET_ID    = os.environ["SPREADSHEET_ID"]
GOOGLE_CREDS_PATH = os.environ["GOOGLE_CREDS_PATH"]

ORIG_CHANNEL_ID   = int(os.environ["ORIG_CHANNEL_ID"])
TARGET_USER       = int(os.environ["TARGET_USER"])

COCKBOT_USERNAME  = os.environ["COCKBOT_USERNAME"]
# ────────────────────────────────────────────────────────────────────────────────

scope = ["https://www.googleapis.com/auth/spreadsheets"]
creds = ServiceAccountCredentials.from_json_keyfile_name(
    "gservice-account.json", scope
)
gc = gspread.authorize(creds)
sheet = gc.open_by_key(SPREADSHEET_ID).worksheet("Sheet1")

SHEET_URL = f"https://docs.google.com/spreadsheets/d/{SPREADSHEET_ID}/edit?usp=sharing"

MODERATORS_FILE = "moderators.json"
SUBSCRIBERS_FILE = "subscribers.json"
FORWARD_MAP_FILE = "forward_map.json"
BANLIST_FILE = "banlist.json"

EDIT_TIMEOUT = timedelta(hours=48)

def load_banlist():
    global banlist
    with open(BANLIST_FILE, "r", encoding="utf-8") as f:
        banlist = json.load(f)

def save_banlist():
    with open(BANLIST_FILE, "w", encoding="utf-8") as f:
        json.dump(banlist, f, ensure_ascii=False, indent=2)

def save_forward_map():
    serializable = {}
    for (orig_id, msg_id), entry in forward_map.items():
        key = f"{orig_id}:{msg_id}"

        serializable[key] = {
            "text": entry.get("text", ""),
            "has_media": entry.get("has_media", False),
            "forwards": entry.get("forwards", []),
            "timestamp": entry.get("timestamp", "")
        }

    with open(FORWARD_MAP_FILE, "w", encoding="utf-8") as f:
        json.dump(serializable, f, ensure_ascii=False, indent=2)

def load_forward_map():
    global forward_map
    forward_map.clear()

    try:
        with open(FORWARD_MAP_FILE, "r", encoding="utf-8") as f:
            data = json.load(f)
    except (FileNotFoundError, json.JSONDecodeError):
        return

    for key, value in data.items():
        try:
            orig_str, msg_str = key.split(":", 1)
            orig_id = int(orig_str)
            msg_id = int(msg_str)
        except ValueError:
            continue

        if isinstance(value, list):
            forward_map[(orig_id, msg_id)] = {
                "text": "Это сообщение слишком старое..",
                "has_media": False,
                "timestamp": "",
                "forwards": [
                    (int(c), int(m), False) for c, m in value
                ]
            }

        elif isinstance(value, dict):
            forward_map[(orig_id, msg_id)] = {
                "text": value.get("text", ""),
                "has_media": value.get("has_media", False),
                "timestamp": value.get("timestamp", ""),
                "forwards": [
                    (int(c), int(m), bool(k)) for c, m, k in value.get("forwards", [])
                ]
            }

def load_moderators():
    try:
        with open(MODERATORS_FILE, "r") as f:
            return set(json.load(f))
    except (IOError, ValueError):
        return set()

def load_subscribers():
    try:
        with open(SUBSCRIBERS_FILE, "r") as f:
            return set(json.load(f))
    except (IOError, ValueError):
        return set()

def save_subscribers(subs: set):
    with open(SUBSCRIBERS_FILE, "w") as f:
        json.dump(list(subs), f)

# initialize in-memory set
SUBSCRIBERS = load_subscribers()
MODERATORS = load_moderators()
forward_map = {}
banlist: list[dict] = []
load_forward_map()
load_banlist()

logging.basicConfig(
    format="%(asctime)s - %(name)s - %(levelname)s - %(message)s",
    level=logging.INFO,
)

async def make_link_keyboard(orig_chat_id, orig_msg_id, bot):
    chat = await bot.get_chat(orig_chat_id)
    if chat.username:
        link = f"https://t.me/{chat.username}/{orig_msg_id}"
    else:
        cid = str(orig_chat_id)
        link = f"https://t.me/c/{cid[4:]}/{orig_msg_id}"
    print(f"orig_chat_id={orig_chat_id!r}, chat.username={chat.username!r}")
    print(f"generated link: {link}")
    return InlineKeyboardMarkup([[
        InlineKeyboardButton("🕹 Перейти к сообщению", url=link)
    ]])


async def make_chat_invite_keyboard():
    return InlineKeyboardMarkup([[
        InlineKeyboardButton("🕹 Вступить в культ!", url="https://t.me/+pI3sHlc1ocY5ZTdi")
    ]])
    

def is_original_keyboard(kb):
    if not kb or not hasattr(kb, 'inline_keyboard'):
        return False

    for row in kb.inline_keyboard:
        for button in row:
            if not hasattr(button, 'url'):
                continue
            if "t.me/+pI3sHlc1ocY5ZTdi" not in button.url:
                return True

    return False

def add_ban_rule(sig: dict):
    rule = {k: v for k, v in sig.items() if v is not None}
    banlist.append(rule)
    save_banlist()

def extract_media_signature(msg):
    obj = None
    if msg.photo:
        obj = msg.photo[-1]
    elif msg.animation:
        obj = msg.animation
    elif msg.video:
        obj = msg.video
    elif msg.document:
        obj = msg.document

    if not obj:
        return None

    return {
        "file_unique_id": getattr(obj, "file_unique_id", None),
        "mime_type":      getattr(obj, "mime_type",      None),
        "duration":       getattr(obj, "duration",       None), 
        "width":          getattr(obj, "width",          None),
        "height":         getattr(obj, "height",         None),
        "file_size":      getattr(obj, "file_size",      None),

        "sha256":    None,
    }

async def compute_sha256(bot, file_id):
    bio = await bot.get_file(file_id)
    data = await bio.download_as_bytearray()
    return hashlib.sha256(data).hexdigest()

async def is_banned_media(sig: dict, file_id, bot) -> bool:
    uid = sig.get("file_unique_id")
    if uid:
        for rule in banlist:
            if rule.get("file_unique_id") == uid:
                return True

    meta_keys = ["mime_type", "duration", "width", "height", "file_size"]
    for rule in banlist:
        if all(
            rule.get(k) is None or rule[k] == sig.get(k)
            for k in meta_keys
        ):
            return True
            rule_hash = rule.get("sha256")
            if not rule_hash:
                return True
            
            try:
                sig["sha256"] = await compute_sha256(bot, file_id)
            except:
                return True

            return sig["sha256"] == rule_hash

    return False
    
async def broadcast(orig_chat_id, orig_msg_id, text, has_media, bot):
    kb = await make_link_keyboard(orig_chat_id, orig_msg_id, bot)
    join_chat = await make_chat_invite_keyboard()

    if (orig_chat_id, orig_msg_id) not in forward_map:
        forward_map[(orig_chat_id, orig_msg_id)] = {
            "text": text,
            "has_media": has_media,
            "forwards": [],
            "timestamp": datetime.utcnow().isoformat()
        }

    mention_list = []
    for subscriber_id in list(SUBSCRIBERS):
        print('Forwarding!')
        try:
            member = await bot.get_chat_member(orig_chat_id, subscriber_id)
            print(member)
        except BadRequest:
            print("Bad Requesst")
            return
        
        try:
            if member.status in ("left", "kicked"):
                fwd = await bot.send_message(
                    chat_id=subscriber_id,
                    text="Рыжопеч опубликовала новое сообщение в чате, но вы должны быть его участником, чтобы видеть содержимое!",
                    reply_markup=join_chat
                )
                forward_map[(orig_chat_id, orig_msg_id)]["forwards"].append((subscriber_id, fwd.message_id, False))
                continue

            fwd = await bot.copy_message(
                chat_id=subscriber_id,
                from_chat_id=orig_chat_id,
                message_id=orig_msg_id,
                reply_markup=kb
            )
            forward_map[(orig_chat_id, orig_msg_id)]["forwards"].append((subscriber_id, fwd.message_id, True))

        except Forbidden:
            SUBSCRIBERS.remove(subscriber_id)
            save_subscribers(SUBSCRIBERS)
            print(f"Removed {subscriber_id}: never initiated conversation")
            try:
                chat = await bot.get_chat(subscriber_id)
                if chat.username:
                    mention = f"@{chat.username}"
                else:
                    name = chat.first_name or "Чаттерс"
                    mention = f'<a href="tg://user?id={subscriber_id}">{name}</a>'
            except Exception:
                mention = str(subscriber_id)
            
            mention_list.append(mention)
            

        except TimedOut:
            print(f"Forward to {subscriber_id} timed out; skipping")

        except Exception:
            print(f"Unexpected error forwarding to {subscriber_id}")
    
    if mention_list:
        unique = list(dict.fromkeys(mention_list))
        mentions = ", ".join(unique)
        text = (
            f"{mentions}, вы так и не начали чат со мной, "
            "выписаны из сталкеров!"
        )
        try:
            await bot.send_message(
                chat_id=orig_chat_id,
                text=text,
                parse_mode=constants.ParseMode.HTML,
                disable_web_page_preview=True
            )
        except (Forbidden, TimedOut, asyncio.CancelledError):
            pass

async def handle_cocksize(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user = update.effective_user
    msg = update.message
    
    if not msg:
        return
    
    sig = extract_media_signature(msg)
    
    if sig:
        file_id = (
            (msg.animation or msg.video or msg.document or (msg.photo[-1] if msg.photo else None)).file_id
        )
        
        if await is_banned_media(sig, file_id, context.bot):
            try:
                await context.bot.delete_message(
                    chat_id=update.effective_chat.id,
                    message_id=msg.message_id
                )
            except:
                print("failed to delete message")
                pass 
            
            try:
                await context.bot.ban_chat_member(update.effective_chat.id, user.id)
            except BadRequest as e:
                print(f"Failed to ban {user.id}: {e}")
            
            entry = {
                "timestamp":    datetime.utcnow().strftime("%Y-%m-%d %H:%M:%S"),
                "chat_id":      update.effective_chat.id,
                "user_id":      user.id,
                "username":     user.username,
                "first_name":   user.first_name,
                "last_name":    user.last_name,
                "file_unique_id": sig.get("file_unique_id"),
                "mime_type":      sig.get("mime_type"),
                "duration":       sig.get("duration"),
                "width":          sig.get("width"),
                "height":         sig.get("height"),
                "file_size":      sig.get("file_size")
            }
            
            print(entry)
            
            return
    
    if msg.new_chat_members:
        for member in msg.new_chat_members:
            user_id = member.id
            print(f"New user joined: {user_id} ({member.username})")
            if user_id in SUBSCRIBERS:
                await update_all_messages(context.bot, user_id)
    
    if not msg.from_user:
        return
    print(update)
    
    if not user or user.id != TARGET_USER:
        return
    
    print('we got message!')
    print(msg)
    
    orig_chat = update.effective_chat.id
    orig_msg  = msg.message_id
    text = msg.text or msg.caption or ""
    has_media = any([
        msg.photo,
        msg.video,
        msg.audio,
        msg.document,
        msg.voice,
        msg.animation,
        msg.sticker
    ])
    
    await broadcast(orig_chat, orig_msg, text, has_media, context.bot)

    save_forward_map()
    
    via = update.message.via_bot
    if not (via and via.username == COCKBOT_USERNAME):
        return

    text = update.message.text or ""
    m = re.search(r"(\d+(?:\.\d+)?)\s*cm", text, re.IGNORECASE)
    if not m:
        return

    size = m.group(1)
    ts = datetime.utcnow().strftime("%Y-%m-%d %H:%M:%S")

    sheet.append_row(
        [ ts, float(size) ],
        value_input_option="RAW",
        table_range="A:B"
    )

    await update.message.reply_text(
        f"✍\n{SHEET_URL}",
        reply_to_message_id=update.message.message_id
    )

async def subscribe(update: Update, context: CallbackContext):
    user = update.effective_user
    if not user:
        return
    uid = user.id
    if uid in SUBSCRIBERS:
        try:
            await context.bot.send_message(
                chat_id=uid,
                text="🎉 Всё настроено, сообщения будут приходить сюда!"
            )
            await update.message.reply_text("✅ Вы уже сталкерите Рыжопеча.")
        except Forbidden:
            await update.message.reply_text(
                "✅ Я тебя записал, но не смогу отправить сообщение, пока ты не откроешь чат со мной. "
                "Пожалуйста отправь /start мне в ЛС."
            )
    else:
        SUBSCRIBERS.add(uid)
        save_subscribers(SUBSCRIBERS)
        await update.message.reply_text("🎉 Поздравляю, теперь ты сталкеришь Рыжопеча!")
        
        try:
            await context.bot.send_message(
                chat_id=uid,
                text="🎉 Всё настроено, сообщения будут приходить сюда!"
            )
        except Forbidden:
            await update.message.reply_text(
                "✅ Я тебя записал, но не смогу отправить сообщение, пока ты не откроешь чат со мной. "
                "Пожалуйста отправь /start мне в ЛС."
            )

async def unsubscribe(update: Update, context: CallbackContext):
    user = update.effective_user
    if not user:
        return
    uid = user.id
    if uid in SUBSCRIBERS:
        SUBSCRIBERS.remove(uid)
        save_subscribers(SUBSCRIBERS)
        await update.message.reply_text("⚠️ Вы покинули клуб сталкеров Рыжопеча.")
    else:
        await update.message.reply_text("ℹ️ Так ты и не следил, чел...")

async def start(update: Update, context: CallbackContext):
    user = update.effective_user
    if not user:
        return
    await update.message.reply_text("ℹ️ Бот инициализирован!")

async def warn_use_dm(update: Update, context: CallbackContext):
    await update.message.reply_text(
        "🚫 Пожалуйста, отправь мне это в личные сообщения, а не сюда."
    )

async def delete_forwards(bot, orig_chat, orig_msg):
    key = (orig_chat, orig_msg)
    entry = forward_map.get(key)

    if not entry:
        return

    for sub_chat, sub_msg, _ in entry["forwards"]:
        print(f"Deleting message {sub_msg} in chat {sub_chat}")
        try:
            await bot.delete_message(
                chat_id=sub_chat,
                message_id=sub_msg
            )
        except BadRequest:
            print("Couldn't delete message")
        except Exception as e:
            print(f"Unexpected error deleting message: {e}")

    forward_map.pop(key, None)


async def edit_message(bot, sub_chat, sub_msg, orig_id, orig_msg, new_text, has_media):
    kb = await make_link_keyboard(orig_id, orig_msg, bot)
    try:
        if has_media:
            await bot.edit_message_caption(
                chat_id=sub_chat,
                message_id=sub_msg,
                caption=new_text,
                parse_mode=constants.ParseMode.HTML,
                reply_markup=kb
            )
        else:
            await bot.edit_message_text(
                chat_id=sub_chat,
                message_id=sub_msg,
                text=new_text,
                parse_mode=constants.ParseMode.HTML,
                reply_markup=kb
            )
    except BadRequest as e:
        print(f"Couldn't edit message {sub_msg} in chat {sub_chat}: {e}")
        pass

async def edit_forwards(bot, event, orig_id, orig_msg):
    msg = event.message
    new_text = msg.message or ""
    has_media = msg.media is not None

    key = (orig_id, orig_msg)
    entry = forward_map.get(key)
    if not entry:
        return

    for sub_chat, sub_msg, is_original in entry["forwards"]:
        print(f"Editing message {sub_msg} for user {sub_chat}")

        if not is_original:
            print(f"Skipping non-original message for {sub_chat}")
            continue

        try:
            await edit_message(bot, sub_chat, sub_msg, orig_id, orig_msg, new_text, has_media)
        except Exception as e:
            print(f"Failed to edit message {sub_msg} in {sub_chat}: {e}")

async def update_all_messages(bot, user_id):
    now = datetime.utcnow()

    for (orig_id, orig_msg), entry in forward_map.items():
        forwards = entry["forwards"]
        user_forward = next((fwd for fwd in entry["forwards"] if fwd[0] == user_id), None)
        if not user_forward:
            continue

        timestamp_str = entry.get("timestamp")
        if not timestamp_str:
            print(f"Skipping message {orig_msg}: no timestamp")
            continue

        try:
            message_time = datetime.fromisoformat(timestamp_str)
        except ValueError:
            print(f"Skipping message {orig_msg}: invalid timestamp format")
            continue

        if message_time < now - EDIT_TIMEOUT:
            print(f"Skipping message {orig_msg}: too old to edit")
            continue

        sub_chat, sub_msg, is_original = user_forward
        
        new_text = entry.get("text", "")
        if entry.get("has_media", False) and new_text == "":
            new_text = "Медиаконтент"
        has_media = entry.get("has_media", False) and is_original
        if not is_original:
            new_text = "Можете перейти к сообщению по ссылке"


        await edit_message(bot, sub_chat, sub_msg, orig_id, orig_msg, new_text, has_media)

    print(f"Update complete for user {user_id}")

async def ban_media(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user = update.effective_user
    msg  = update.effective_message

    if not user or user.id not in MODERATORS:
        return

    reply = msg.reply_to_message
    if not reply:
        await msg.reply_text("⚠️ Ответьте на сообщение с видео/GIF/файлом, чтобы добавить его в банлист.")
        return

    sig = extract_media_signature(reply)
    if not sig:
        await msg.reply_text("⚠️ В этом сообщении нет видео, анимации или документа.")
        return


    try:
        await context.bot.delete_message(chat_id=reply.chat.id, message_id=reply.message_id)
    except Exception:
        pass

    file_id = (
        (reply.animation or reply.video or reply.document or (reply.photo[-1] if reply.photo else None)).file_id
    )
    
    try:
        sig["sha256"] = await compute_sha256(context.bot, file_id)
    except Exception as e:
        print(f"Failed to hash file: {e}")

    meta_keys = ["mime_type", "duration", "width", "height", "file_size"]
    for rule in banlist:
        if rule.get("file_unique_id") == sig["file_unique_id"]:
            await msg.reply_text("ℹ️ Этот файл уже в банлисте (по уникальному ID).")
            return
        if all(
            rule.get(k) is None or rule[k] == sig.get(k)
            for k in meta_keys
        ):
            await msg.reply_text("ℹ️ Этот файл уже в банлисте (по метаданным).")
            return

    add_ban_rule(sig)

    lines = [f"{k}={v}" for k,v in sig.items() if v is not None and not k.startswith("_")]
    await msg.reply_text("✅ Добавил этот контент в банлист:\n" + "\n".join(lines))

async def unban_media(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user = update.effective_user
    msg  = update.effective_message

    if not user or user.id not in MODERATORS:
        return

    reply = msg.reply_to_message
    if not reply:
        await msg.reply_text(
            "⚠️ Ответьте на ранее заблокированное сообщение, чтобы убрать его из банлиста."
        )
        return

    sig = extract_media_signature(reply)
    if not sig:
        await msg.reply_text("⚠️ В этом сообщении нет видео, анимации или документа.")
        return
    
    file_id = (
        (reply.animation or reply.video or reply.document or (reply.photo[-1] if reply.photo else None)).file_id
    )

    try:
        sig["sha256"] = await compute_sha256(context.bot, file_id)
    except:
        pass

    meta_keys = ["mime_type", "duration", "width", "height", "file_size"]
    removed = 0
    new_rules = []
    for rule in banlist:
        if rule.get("file_unique_id") and rule["file_unique_id"] == sig.get("file_unique_id"):
            removed += 1
            continue

        if all(
            rule.get(k) is None or rule[k] == sig.get(k)
            for k in meta_keys
        ):
            removed += 1
            continue
        
        new_rules.append(rule)

    if removed:
        banlist[:] = new_rules
        save_banlist()
        await msg.reply_text(f"✅ Удалено {removed} правил из банлиста.")
    else:
        await msg.reply_text("ℹ️ Не нашёл совпадающих правил в банлисте.")

def main():
    mc = TelegramClient('anon', API_ID, API_HASH)
    
    mc.start(bot_token=BOT_TOKEN)

    app = ApplicationBuilder().token(BOT_TOKEN).get_updates_read_timeout(30).get_updates_write_timeout(30).build()
    app.add_handler(
        MessageHandler(filters.Chat(chat_id=ORIG_CHANNEL_ID) & ~filters.COMMAND, handle_cocksize)
    )

    app.add_handler(CommandHandler("notify",   subscribe))
    app.add_handler(CommandHandler("stop", unsubscribe))
    app.add_handler(CommandHandler("start", start, filters=filters.ChatType.PRIVATE))
    app.add_handler(CommandHandler("start", warn_use_dm, filters=~filters.ChatType.PRIVATE))
    app.add_handler(CommandHandler("ban", ban_media))
    app.add_handler(CommandHandler("unban", unban_media))

    @mc.on(events.MessageDeleted(chats=ORIG_CHANNEL_ID))
    async def on_deleted(event):
        print("chat id: ", event.chat_id)
        for msg_id in event.deleted_ids:
            await delete_forwards(app.bot, ORIG_CHANNEL_ID, msg_id)
        
        save_forward_map()
    
    @mc.on(events.MessageEdited(chats=ORIG_CHANNEL_ID))
    async def on_edited(event):
        print("chat id: ", event.chat_id)
        orig_id   = event.chat_id
        orig_msg  = event.message.id
        await edit_forwards(app.bot, event, orig_id, orig_msg)

    app.run_polling(timeout=30)

if __name__ == "__main__":
    main()
