import asyncio
import datetime
import html
import json
import logging
import os
import re

from dotenv import load_dotenv

import gspread
from oauth2client.service_account import ServiceAccountCredentials

from telethon import TelegramClient, events, types

from telegram import (
    constants,
    InlineKeyboardButton,
    InlineKeyboardMarkup,
    Update,
)
from telegram.error import Forbidden, TimedOut
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

SUBSCRIBERS_FILE = "subscribers.json"
FORWARD_MAP_FILE = "forward_map.json"

def save_forward_map():
    serializable = {
        f"{orig}:{msg}": subs
        for (orig, msg), subs in forward_map.items()
    }
    with open(FORWARD_MAP_FILE, "w", encoding="utf-8") as f:
        json.dump(serializable, f, ensure_ascii=False, indent=2)

def load_forward_map():
    global forward_map
    forward_map.clear()

    with open(FORWARD_MAP_FILE, "r", encoding="utf-8") as f:
        data = json.load(f)

    if data == None:
        return
    for key, subs in data.items():
        orig_str, msg_str = key.split(":", 1)
        orig_id = int(orig_str)
        msg_id  = int(msg_str)
        forward_map[(orig_id, msg_id)] = [(int(c), int(m)) for c, m in subs]

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
forward_map = {}
load_forward_map()

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

async def broadcast(orig_chat_id, orig_msg_id, bot):
    kb = await make_link_keyboard(orig_chat_id, orig_msg_id, bot)

    mention_list = []
    for subscriber_id in list(SUBSCRIBERS):
        print('Forwarding!')
        try:
            fwd = await bot.copy_message(
                chat_id=subscriber_id,
                from_chat_id=orig_chat_id,
                message_id=orig_msg_id,
                reply_markup=kb
            )
            forward_map.setdefault((orig_chat_id, orig_msg_id), []).append(
                (subscriber_id, fwd.message_id)
            )

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
    if not msg or not msg.from_user:
        return
    print(update)
    
    if not user or user.id != TARGET_USER:
        return

    
    print('we got message!')
    print(msg)
    
    orig_chat = update.effective_chat.id
    orig_msg  = msg.message_id
    
    await broadcast(orig_chat, orig_msg, context.bot)

    save_forward_map()
    
    via = update.message.via_bot
    if not (via and via.username == COCKBOT_USERNAME):
        return

    text = update.message.text or ""
    m = re.search(r"(\d+(?:\.\d+)?)\s*cm", text, re.IGNORECASE)
    if not m:
        return

    size = m.group(1)
    ts   = datetime.datetime.utcnow().isoformat()

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
            # test message
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
    for sub_chat, sub_msg in forward_map.get(key, []):
        print("delete message for ", sub_chat)
        try:
            await bot.delete_message(
                chat_id=sub_chat,
                message_id=sub_msg
            )
        except BadRequest:
            print("Couldn't delete message")
            pass
    forward_map.pop(key, None)


async def edit_forwards(bot, event, orig_id, orig_msg):    
    msg = event.message
    new_text = msg.message or ""     
    has_media = msg.media is not None

    key = (orig_id, orig_msg)
    for sub_chat, sub_msg in forward_map.get(key, []):
        print("edit message for ", sub_chat)
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
        except BadRequest:
            print("couldn't edit message")
            pass
    

async def get_chat_owner_id(chat_id: int, context):
    print('hello world')
    admins = await context.bot.get_chat_administrators(chat_id)
    print(len(admins))
    for member in admins:
        print('member: ', member.user.id)
        print(member.status)
        print(member.user.first_name)
        print(member.user.last_name)
        print(member.user.username)
        if member.status == 'creator':
            print('creator: ', member.user.id)

def main():
    mc = TelegramClient('anon', API_ID, API_HASH)
    
    mc.start(bot_token=BOT_TOKEN)

    app = ApplicationBuilder().token(BOT_TOKEN).get_updates_read_timeout(30).get_updates_write_timeout(30).build()
    app.add_handler(
        MessageHandler(filters.ALL & ~filters.COMMAND, handle_cocksize)
    )

    app.add_handler(CommandHandler("notify",   subscribe))
    app.add_handler(CommandHandler("stop", unsubscribe))
    app.add_handler(CommandHandler("start", start, filters=filters.ChatType.PRIVATE))
    app.add_handler(CommandHandler("start", warn_use_dm, filters=~filters.ChatType.PRIVATE))
    
    @mc.on(events.MessageDeleted())
    async def on_deleted(event):
        print("chat id: ", event.chat_id)
        for msg_id in event.deleted_ids:
            await delete_forwards(app.bot, ORIG_CHANNEL_ID, msg_id)
        
        save_forward_map()
    
    @mc.on(events.MessageEdited())
    async def on_edited(event):
        print("chat id: ", event.chat_id)
        orig_id   = event.chat_id
        orig_msg  = event.message.id
        await edit_forwards(app.bot, event, orig_id, orig_msg)
    
    app.run_polling(timeout=30)

if __name__ == "__main__":
    main()
