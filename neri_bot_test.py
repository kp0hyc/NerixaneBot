import asyncio
import hashlib
import html
import json
import logging
import math
import os
import random
import re
import sys
import unicodedata

from dotenv import load_dotenv

import gspread
from oauth2client.service_account import ServiceAccountCredentials

from datetime import datetime, timedelta, time, timezone
from zoneinfo import ZoneInfo

from pathlib import Path

from typing import Callable, Awaitable

from html import escape

from telethon import TelegramClient, events, types
from telethon.utils import get_peer_id
from telethon.tl.functions.channels import GetParticipantRequest
from telethon.tl.types import (
    ChannelParticipant,
    ChannelParticipantsAdmins, 
    ChannelParticipantsRecent,
    UpdateBotMessageReaction, 
    PeerChat, 
    PeerChannel, 
    PeerUser,
    ReactionEmoji, 
    ReactionCustomEmoji,
)

from telegram import (
    constants,
    InlineKeyboardButton,
    InlineKeyboardMarkup,
    MessageEntity,
    Update,
)
from telegram.error import BadRequest, Forbidden, TimedOut
from telegram.ext import (
    ApplicationBuilder,
    CallbackContext,
    CallbackQueryHandler,
    CommandHandler,
    ContextTypes,
    MessageHandler,
    MessageReactionHandler,
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

mc: TelegramClient

SHEET_URL = f"https://docs.google.com/spreadsheets/d/{SPREADSHEET_ID}/edit?usp=sharing"

MODERATORS_FILE = "moderators.json"
SUBSCRIBERS_FILE = "subscribers.json"
FORWARD_MAP_FILE = "forward_map.json"
BANLIST_FILE = "banlist.json"
STATS_FILE = "message_stats.json"
DAILY_STATS_DIR = Path("daily_stats")
LAST_SIZES_FILE = "last_sizes.json"
SOCIAL_RATING_FILE = "social_rating.json"
WEIGHTS_FILE = Path("emoji_weights.json")
SOCIAL_ADD_RATING_IMAGE = "add_rating.png"
SOCIAL_SUB_RATING_IMAGE = "sub_rating.png"
DAILY_STATS_DIR.mkdir(exist_ok=True)

TYUMEN = ZoneInfo("Asia/Yekaterinburg")
EDIT_TIMEOUT = timedelta(hours=48)
PAGE_SIZE = 10

TARGET_NICKS = [
    "Рыжая голова",
    "Рыжопеч",
    "Принцесса рунета",
    "Яся",
    "Рыжейшество",
    "Яр!%#",
    "Рыжая жёппа",
    "Рыжая рептилия"
]

def _is_mod(user_id: int) -> bool:
    return user_id in MODERATORS

def _daily_path_for(date_obj: datetime.date) -> Path:
    return DAILY_STATS_DIR / f"daily_stats_{date_obj.isoformat()}.json"

async def get_join_date(client: TelegramClient, chat_id: int, user_id: int):
    try:
        res = await client(GetParticipantRequest(
            channel=chat_id,
            participant=user_id
        ))
        part = res.participant  # this is a ChannelParticipant subclass
        print("part: ", part)
        return part.date      # datetime when they joined
    except Exception as e:
        print("exception in get_join_date: ", e)
        return None

def load_emoji_weights():
    global emoji_weights
    if WEIGHTS_FILE.exists():
        try:
            raw = json.loads(WEIGHTS_FILE.read_text(encoding="utf-8"))
            # coerce keys to str and values to int
            emoji_weights = {str(k): int(v) for k, v in raw.items()}
        except Exception:
            # keep defaults on error
            pass

def save_emoji_weights():
    WEIGHTS_FILE.write_text(
        json.dumps(emoji_weights, ensure_ascii=False, indent=2),
        encoding="utf-8"
    )

def load_social_rating():
    global social_rating
    DEFAULTS = {
        "additional_chat": 0,
        "additional_neri": 0,
        "additional_self": 0,
        "boosts": 0,
        "manual_rating": 0,
    }

    try:
        with open(SOCIAL_RATING_FILE, "r", encoding="utf-8") as f:
            data = json.load(f)

        social_rating = {
            int(uid): {
                k: int(v.get(k, DEFAULTS[k]))
                for k in DEFAULTS
            }
            for uid, v in data.items()
        }

    except (FileNotFoundError, json.JSONDecodeError):
        print("Couldn't read social rating")
        social_rating = {}

def save_social_rating():
    to_dump = {
        str(uid): {"additional_chat": info["additional_chat"], "additional_neri": info["additional_neri"], "additional_self": info["additional_self"], "boosts": info["boosts"], "manual_rating": info["manual_rating"]}
        for uid, info in social_rating.items()
    }
    with open(SOCIAL_RATING_FILE, "w", encoding="utf-8") as f:
        json.dump(to_dump, f, ensure_ascii=False, indent=2)

def load_stats():
    global message_stats, daily_stats

    try:
        with open(STATS_FILE, "r", encoding="utf-8") as f:
            raw = json.load(f)
            message_stats = {int(k): int(v) for k, v in raw.items()}
    except (FileNotFoundError, json.JSONDecodeError):
        message_stats = {}

    today = datetime.now(TYUMEN).date()
    today_path = _daily_path_for(today)
    if today_path.exists():
        try:
            with open(today_path, "r", encoding="utf-8") as f:
                raw = json.load(f)
                daily_stats = {int(k): int(v) for k, v in raw.items()}
        except (json.JSONDecodeError, IOError):
            daily_stats = {}
    else:
        daily_stats = {}

def save_stats():
    with open(STATS_FILE, "w", encoding="utf-8") as f:
        json.dump(message_stats, f, ensure_ascii=False, indent=2)

def save_daily_stats():
    today = datetime.now(TYUMEN).date()
    path = _daily_path_for(today)
    path.write_text(
        json.dumps(daily_stats, ensure_ascii=False, indent=2)
    )

def load_last_sizes():
    global last_sizes
    try:
        with open(LAST_SIZES_FILE, "r", encoding="utf-8") as f:
            data = json.load(f)
            last_sizes = {
                int(uid): {"size": float(v["size"]), "ts": v["ts"]}
                for uid, v in data.items()
            }
    except (FileNotFoundError, json.JSONDecodeError):
        last_sizes = {}

def save_last_sizes():
    to_dump = {
        str(uid): {"size": info["size"], "ts": info["ts"]}
        for uid, info in last_sizes.items()
    }
    with open(LAST_SIZES_FILE, "w", encoding="utf-8") as f:
        json.dump(to_dump, f, ensure_ascii=False, indent=2)

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
message_stats: dict[int, int] = {}
daily_stats:  dict[int,int] = {}
stats_sessions: dict[int, int] = {}
last_sizes: dict[int, dict] = {}
social_rating: dict[int, dict] = {}
emoji_weights: dict[str,int] = {}
load_forward_map()
load_banlist()
load_stats()
load_last_sizes()
load_social_rating()
load_emoji_weights()

logging.basicConfig(
    format="%(asctime)s - %(name)s - %(levelname)s - %(message)s",
    level=logging.INFO,
)

def count_total_rating(uid):
    if uid not in social_rating:
        return 0
    info = social_rating[uid]
    return info.get("additional_chat", 0) + info.get("additional_neri", 0) * 15 + info.get("boosts", 0) * 5 + info.get("manual_rating", 0)

def count_neri_rating(uid):
    if uid not in social_rating:
        return 0
    info = social_rating[uid]
    return info.get("additional_neri", 0) * 15 + info.get("boosts", 0) * 5 + info.get("manual_rating", 0)

async def reset_daily(context: ContextTypes.DEFAULT_TYPE):
    yesterday = (datetime.now(TYUMEN) - timedelta(days=1)).date()
    ypath = _daily_path_for(yesterday)
    if daily_stats:
        ypath.write_text(json.dumps(daily_stats, ensure_ascii=False, indent=2))
    daily_stats.clear()
    save_daily_stats()
    print(f"Rotated daily stats: {yesterday} → {ypath.name}")

def parse_name(uc):
    if uc.id == TARGET_USER:
        nick = random.choice(TARGET_NICKS)
        return f"👑<b>{escape(nick)}</b>"
    if uc.first_name or uc.last_name:
        return escape(" ".join(filter(None, [uc.first_name, uc.last_name])))
    if uc.username:
        return escape(f"@{uc.username}")
    return escape(str(uid))

async def build_stats_page_async(mode: str, page: int, bot) -> tuple[str, InlineKeyboardMarkup]:
    if mode == "global":
        items   = list(message_stats.items())
        mode_ru = "глобально"

    elif mode == "daily":
        items   = list(daily_stats.items())
        mode_ru = "сегодня"

    elif mode == "cock":
        items   = [(uid, info["size"]) for uid, info in last_sizes.items()]
        mode_ru = "по размеру"

    else:  # "social"
        items = []
        for uid, info in social_rating.items():
            if uid == TARGET_USER:
                continue
            total = count_total_rating(uid)
            neri  = count_neri_rating(uid)
            items.append((uid, total, neri))
        mode_ru = "соц. рейтинг"

    sorted_stats = sorted(items, key=lambda kv: kv[1], reverse=True)
    total        = len(sorted_stats)
    start, end = page * PAGE_SIZE, (page + 1) * PAGE_SIZE
    last_page = max(math.floor((total - 1) / PAGE_SIZE), 0)
    print("last_page: ", last_page)
    chunk = sorted_stats[start:end]

    header = f"📊 Топ ({mode_ru.capitalize()}) #{start+1}–{min(end, total)} из {total}:\n"
    lines = [header]
    for rank, entry in enumerate(chunk, start=start+1):
        if mode == "social":
            uid, full, neri = entry
        else:
            uid, full = entry
            neri = None

        try:
            uc   = await bot.get_chat(uid)
            name = parse_name(uc)
        except:
            name = escape(str(uid))

        if mode == "cock":
            lines.append(f"{rank}. {name}: {float(full):.1f} см")
        elif mode == "social":
            lines.append(f"{rank}. {name}: {full}({neri}) рейтинга")
        else:
            lines.append(f"{rank}. {name}: {full} сообщений")

    text = "\n".join(lines)

    modes = [
        ("global",  "🌐 Всё"),
        ("daily",   "📅 Сегодня"),
        ("social",  "⚡ Соц. рейтинг"),
        ("cock",    "🍆 Размер"),
    ]
    mode_buttons = [
        InlineKeyboardButton(
            label,
            callback_data=f"stats:{m}:0"
        )
        for m, label in modes
    ]

    nav_buttons = []
    if page > 0:
        nav_buttons.append(
            InlineKeyboardButton("◀️ Предыдущая", callback_data=f"stats:{mode}:{page-1}")
        )
    if end < total:
        nav_buttons.append(
            InlineKeyboardButton("Следующая ▶️", callback_data=f"stats:{mode}:{page+1}")
        )
        if mode == "social" or mode == "cock":
            nav_buttons.append(
                InlineKeyboardButton("Последняя", callback_data=f"stats:{mode}:{last_page}")
            )
    action_buttons = [
        InlineKeyboardButton("ℹ️ Отслеживать рыжопеча", callback_data=f"follow")
    ]
    kb = InlineKeyboardMarkup([mode_buttons, nav_buttons, action_buttons])
    return text, kb

async def follow_callback(update: Update, context: ContextTypes.DEFAULT_TYPE):
    q = update.callback_query
    await q.answer()

    user = update.effective_user
    if not user:
        return

    async def send_dm(text):
        await context.bot.send_message(chat_id=user.id, text=text)

    mention = f'<a href="tg://user?id={user.id}">{html.escape(user.full_name)}</a>: '
    async def reply_in_chat(text):
        await context.bot.send_message(
            chat_id=q.message.chat_id,
            text=mention + text,
            parse_mode=constants.ParseMode.HTML
        )

    await _subscribe_flow(
        user.id,
        send_dm=send_dm,
        reply_in_chat=reply_in_chat,
    )

async def stats_page_callback(update: Update, context: ContextTypes.DEFAULT_TYPE):
    q = update.callback_query
    await q.answer()

    msg = q.message
    #owner_id = stats_sessions.get(msg.message_id)
    #if owner_id is None or q.from_user.id != owner_id:
    #    return await q.answer("⛔ Только автор сообщения может навигировать.", show_alert=True)

    _, mode, page_str = q.data.split(":")
    page = int(page_str)
    text, kb = await build_stats_page_async(mode, page, context.bot)
    await q.edit_message_text(text=text, parse_mode="HTML", reply_markup=kb)


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
    sticker_pack = None

    if msg.photo:
        obj = msg.photo[-1]
    elif msg.animation:
        obj = msg.animation
    elif msg.video:
        obj = msg.video
    elif msg.document:
        obj = msg.document
    elif msg.sticker:
        obj = msg.sticker

    if not obj:
        return None

    sig = {
        "file_unique_id":   getattr(obj, "file_unique_id", None),
        "mime_type":        getattr(obj, "mime_type",      None),
        "duration":         getattr(obj, "duration",       None),
        "width":            getattr(obj, "width",          None),
        "height":           getattr(obj, "height",         None),
        "file_size":        getattr(obj, "file_size",      None),
        "sticker_set_name": getattr(obj, "set_name",       None),
        # we'll fill this in later if you need to compare hashes
        "sha256":         None,
    }

    return sig

async def compute_sha256(bot, file_id):
    bio = await bot.get_file(file_id)
    data = await bio.download_as_bytearray()
    return hashlib.sha256(data).hexdigest()

async def is_banned_media(sig: dict, file_id, bot) -> (bool, bool):
    pack = sig.get("sticker_set_name")
    print("pack: ", pack)
    if pack:
        for rule in banlist:
            if rule.get("sticker_set_name") == pack:
                return True, rule.get("soft", False)
        return False, False

    uid = sig.get("file_unique_id")
    if uid:
        for rule in banlist:
            if rule.get("file_unique_id") == uid:
                return True, rule.get("soft", False)

    meta_keys = ["mime_type", "duration", "width", "height", "file_size"]
    for rule in banlist:
        if all(
            rule.get(k) is None or rule[k] == sig.get(k)
            for k in meta_keys
        ):
            return True, rule.get("soft")
            rule_hash = rule.get("sha256")
            if not rule_hash:
                return True, rule.get("soft")
            
            try:
                sig["sha256"] = await compute_sha256(bot, file_id)
            except:
                return True, rule.get("soft")

            return sig["sha256"] == rule_hash, rule.get("soft")

    return False, False
    
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
    print("handle_cocksize")
    user = update.effective_user
    msg = update.message
    
    if not msg:
        return
    
    sig = extract_media_signature(msg)
    
    if sig:
        file_id = (
            (msg.sticker or msg.animation or msg.video or msg.document or (msg.photo[-1] if msg.photo else None)).file_id
        )
        
        block, soft = await is_banned_media(sig, file_id, context.bot)
        if block:
            print("We got blocked media!")
            
            try:
                await context.bot.delete_message(
                    chat_id=update.effective_chat.id,
                    message_id=msg.message_id
                )
            except:
                print("failed to delete message")
                pass 
            
            if not soft:
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
    
    if not user:
        return

    message_stats[user.id] = message_stats.get(user.id, 0) + 1
    daily_stats[user.id] = daily_stats.get(user.id, 0) + 1
    
    fd = getattr(msg, "forward_date", None)
    if fd is None and hasattr(msg, "api_kwargs"):
        fd = msg.api_kwargs.get("forward_date", None)
    via = update.message.via_bot
    if (via and via.username == COCKBOT_USERNAME and fd is None):
        if update.message and update.message.text:
            cock_text = update.message.text or ""
            m = re.search(r"(\d+(?:\.\d+)?)\s*cm", cock_text, re.IGNORECASE)
            if m:
                size = float(m.group(1))
                ts = datetime.utcnow().strftime("%Y-%m-%d %H:%M:%S")
                last_sizes[user.id] = {"size": size, "ts": ts}
                save_last_sizes()

    if not user.id in social_rating:
        social_rating[user.id] = {"additional_chat": 0, "additional_neri": 0, "additional_self": 0, "boosts": 0, "manual_rating": 0}
        save_social_rating()

    bc = getattr(msg, "sender_boost_count", None)
    if bc is None and hasattr(msg, "api_kwargs"):
        bc = msg.api_kwargs.get("sender_boost_count", 0)
    boost_count = int(bc or 0)
    if social_rating[user.id]["boosts"] != boost_count:
        social_rating[user.id]["boosts"] = boost_count
        save_social_rating()
    
    if user.id != TARGET_USER:
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

    cock_text = update.message.text or ""
    m = re.search(r"(\d+(?:\.\d+)?)\s*cm", cock_text, re.IGNORECASE)
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

def extract_emojis(lst):
    out = []
    for r in lst:
        if isinstance(r, ReactionEmoji):
            out.append(r.emoticon)
        elif isinstance(r, ReactionCustomEmoji):
            out.append(f"<custom:{r.document_id}>")
    return out

def get_emoji_weight(e: str) -> int:
    """
    Look up e in emoji_weights, trying to normalize both
    with and without the VARIATION SELECTOR-16 (U+FE0F).
    """
    # First try exactly as‐is
    if e in emoji_weights:
        return emoji_weights[e]

    # Normalize to NFC (just in case)
    e_nfc = unicodedata.normalize("NFC", e)
    if e_nfc != e and e_nfc in emoji_weights:
        return emoji_weights[e_nfc]

    # Try adding VS16 if it’s missing
    VS16 = "\uFE0F"
    if not e_nfc.endswith(VS16):
        cand = e_nfc + VS16
        if cand in emoji_weights:
            return emoji_weights[cand]
    else:
        # Or stripping it
        cand = e_nfc.rstrip(VS16)
        if cand in emoji_weights:
            return emoji_weights[cand]

    return 0

async def on_message_reaction(mc, event):
    print("got reaction")
    print(event)
    peer = event.peer
    if isinstance(peer, PeerChat):
        chat_id = peer.chat_id
    elif isinstance(peer, PeerChannel):
        chat_id = peer.channel_id
    else:
        return
    
    chat_id = get_peer_id(peer)
    print("chat_id: ", chat_id)
    if chat_id != ORIG_CHANNEL_ID:
        return

    msg_id = event.msg_id

    msg = await mc.get_messages(chat_id, ids=msg_id)
    
    if not msg:
        return
    
    print("orig_msg: ", msg)

    author_id = None
    if hasattr(msg, "from_id"):
        if msg.from_id != None:
            author_id = get_peer_id(msg.from_id)
        else:
            author_id = TARGET_USER
    else:
        return

    if not hasattr(event, "actor"):
        return
    reactor_id = get_peer_id(event.actor)

    if reactor_id == author_id:
        return

    print("author_id: ", author_id)
    print("reactor_id: ", reactor_id)
    
    if reactor_id != ORIG_CHANNEL_ID and count_total_rating(reactor_id) < -100:
        print("too low social rating")
        return

    if reactor_id != ORIG_CHANNEL_ID and reactor_id != TARGET_USER:
        join_date = await get_join_date(mc, ORIG_CHANNEL_ID, reactor_id)
        if not join_date:
            print("user not in the chat to count rating")
            return
        now = datetime.now(timezone.utc)
        if (now - join_date).days <= 3:
            print("member is too new")
            return

    old = getattr(event, 'old_reactions', []) or []
    new = getattr(event, 'new_reactions', []) or getattr(event, 'new_reaction', [])

    old_set = set(extract_emojis(old))
    new_set = set(extract_emojis(new))
    
    added   = new_set - old_set
    removed = old_set - new_set

    print(f"added reactions: {added}")
    print(f"removed reactions: {removed}")

    delta = 0
    for e in added:
        delta += get_emoji_weight(e)
    for e in removed:
        delta -= get_emoji_weight(e)
    
    print("delta: ", delta)
    if delta == 0:
        return  # nothing to change

    receiver = author_id
    entry_name = "additional_chat"
    if reactor_id == TARGET_USER or reactor_id == ORIG_CHANNEL_ID:
        entry_name = "additional_neri"
    elif author_id == TARGET_USER:
        entry_name = "additional_self"
        
    entry = social_rating.setdefault(receiver, {"additional_chat": 0, "additional_neri": 0, "additional_self": 0, "boosts": 0, "manual_rating": 0})
    entry[entry_name] = entry[entry_name] + delta
    save_social_rating()

    print(
        f"[Reactions] msg#{msg_id} for user {author_id} by user {reactor_id}: "
        f"+{len(added)} added, -{len(removed)} removed → delta={delta}, "
        f"new score={entry[entry_name]}"
    )

async def _subscribe_flow(
    user_id: int,
    *,
    send_dm: Callable[[str], Awaitable],
    reply_in_chat: Callable[[str], Awaitable],
):
    if user_id in SUBSCRIBERS:
        try:
            await send_dm("🎉 Всё настроено, сообщения будут приходить сюда!")
            await reply_in_chat("✅ Вы уже сталкерите Рыжопеча.")
        except Forbidden:
            await reply_in_chat(
                "✅ Я тебя записал, но не смогу отправить сообщение, пока ты не откроешь чат со мной. "
                "Пожалуйста отправь /start мне в ЛС."
            )
    else:
        SUBSCRIBERS.add(user_id)
        save_subscribers(SUBSCRIBERS)

        await reply_in_chat("🎉 Поздравляю, теперь ты сталкеришь Рыжопеча!")
        try:
            await send_dm("🎉 Всё настроено, сообщения будут приходить сюда!")
        except Forbidden:
            await reply_in_chat(
                "✅ Я тебя записал, но не смогу отправить сообщение, пока ты не откроешь чат со мной. "
                "Пожалуйста отправь /start мне в ЛС."
            )

async def subscribe(update: Update, context: CallbackContext):
    user = update.effective_user
    if not user:
        return

    async def send_dm(text):
        await context.bot.send_message(chat_id=user.id, text=text)

    async def reply_in_chat(text):
        await update.message.reply_text(text)

    await _subscribe_flow(
        user.id,
        send_dm=send_dm,
        reply_in_chat=reply_in_chat,
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
    await add_media_to_block(update, context, False)

async def block_media(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await add_media_to_block(update, context, True)

async def add_media_to_block(update: Update, context: ContextTypes.DEFAULT_TYPE, soft):
    user = update.effective_user
    msg  = update.effective_message

    if not user or user.id not in MODERATORS:
        return

    reply = msg.reply_to_message
    if not reply:
        await msg.reply_text(
            "⚠️ Ответьте на сообщение с видео/GIF/файлом/стикером, чтобы добавить его в банлист."
        )
        return

    sig = extract_media_signature(reply)
    if not sig:
        await msg.reply_text(
            "⚠️ В этом сообщении нет видео, анимации, документа или стикера."
        )
        return

    try:
        await context.bot.delete_message(chat_id=reply.chat.id, message_id=reply.message_id)
    except Exception:
        pass

    file_obj = (
        reply.sticker
        or reply.animation
        or reply.video
        or reply.document
        or (reply.photo[-1] if reply.photo else None)
    )
    file_id = getattr(file_obj, "file_id", None)

    if file_id:
        try:
            sig["sha256"] = await compute_sha256(context.bot, file_id)
        except Exception as e:
            print(f"Failed to hash file: {e}")

    # Проверяем, нет ли уже в банлисте
    meta_keys = ["mime_type", "duration", "width", "height", "file_size"]
    for rule in banlist:
        # 1) по названию набора стикеров
        if sig.get("sticker_set_name") and rule.get("sticker_set_name") == sig["sticker_set_name"]:
            await msg.reply_text("ℹ️ Этот стикер/пак уже в банлисте (по названию набора).")
            return
        # 2) по уникальному ID файла
        if rule.get("file_unique_id") == sig["file_unique_id"]:
            await msg.reply_text("ℹ️ Этот файл уже в банлисте (по уникальному ID).")
            return
        # 3) по остальным метаданным
        if all(
            rule.get(k) is None or rule[k] == sig.get(k)
            for k in meta_keys
        ):
            await msg.reply_text("ℹ️ Этот файл уже в банлисте (по метаданным).")
            return

    if soft:
        sig["soft"] = True
    add_ban_rule(sig)

    lines = [
        f"{k}={v}"
        for k, v in sig.items()
        if v is not None and not k.startswith("_")
    ]
    await msg.reply_text("✅ Добавил этот контент в банлист:\n" + "\n".join(lines))

async def unban_media(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user = update.effective_user
    msg  = update.effective_message

    if not user or user.id not in MODERATORS:
        return

    reply = msg.reply_to_message
    if not reply:
        await msg.reply_text(
            "⚠️ Ответьте на ранее заблокированное видео/GIF/документ/стикер, чтобы убрать его из банлиста."
        )
        return

    sig = extract_media_signature(reply)
    if not sig:
        await msg.reply_text(
            "⚠️ В этом сообщении нет видео, анимации, документа или стикера."
        )
        return

    file_obj = (
        reply.sticker
        or reply.animation
        or reply.video
        or reply.document
        or (reply.photo[-1] if reply.photo else None)
    )
    file_id = getattr(file_obj, "file_id", None)

    if file_id:
        try:
            sig["sha256"] = await compute_sha256(context.bot, file_id)
        except Exception:
            pass

    meta_keys = ["mime_type", "duration", "width", "height", "file_size"]
    removed = 0
    new_rules = []

    for rule in banlist:
        if sig.get("sticker_set_name") and rule.get("sticker_set_name") == sig["sticker_set_name"]:
            removed += 1
            continue

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
        await msg.reply_text("ℹ️ Не найдено совпадающих правил в банлисте.")


async def top_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    text, kb = await build_stats_page_async("global", 0, context.bot)
    sent = await update.message.reply_text(
        text,
        parse_mode="HTML",
        reply_markup=kb
    )
    stats_sessions[sent.message_id] = update.effective_user.id


async def show_rating(update: Update, context: CallbackContext):
    return

async def shutdown_bot(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user = update.effective_user
    if not user or user.id not in MODERATORS:
        return

    await update.message.reply_text("🔌 Shutting down, saving stats…")

    save_stats()
    save_daily_stats()
    clear_and_save_cocks()

    sys.exit(0)

def clear_and_save_cocks():
    cutoff = datetime.now() - timedelta(hours=24)

    to_delete = []
    for key, info in last_sizes.items():
        ts_str = info.get("ts")
        if not ts_str:
            print("WARNING NO TIMESTAMP")
            continue
        try:
            ts = datetime.strptime(ts_str, "%Y-%m-%d %H:%M:%S")
        except ValueError:
            print("WARNING WRONG TIMESTAMP")
            continue

        if ts < cutoff:
            to_delete.append(key)

    for key in to_delete:
        del last_sizes[key]
    save_last_sizes()

async def persist_stats(context: ContextTypes.DEFAULT_TYPE):
    save_stats()
    save_daily_stats()
    clear_and_save_cocks()
    print("Message stats saved.")

async def edit_weights_cmd(update, context: ContextTypes.DEFAULT_TYPE):
    user = update.effective_user
    if not user or user.id not in MODERATORS:
        return

    dump = json.dumps(emoji_weights, ensure_ascii=False, indent=2)
    
    sent = await update.message.reply_text(dump)
    context.user_data["weights_msg_id"] = sent.message_id

async def edit_weights_reply(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user = update.effective_user
    msg  = update.effective_message

    if not user or user.id not in MODERATORS:
        return
    parent = msg.reply_to_message
    if not parent or parent.message_id != context.user_data.get("weights_msg_id"):
        return

    text = msg.text or ""
    m = re.match(r"\s*(.+?)\s*:\s*(\-?\d+)\s*$", text)
    if not m:
        return await msg.reply_text("ℹ️ Неверный формат. Напишите `emoji: число`.")

    key_raw, val_raw = m.group(1), m.group(2)
    ce = next(
        (e for e in (msg.entities or [])
         if e.type == MessageEntity.CUSTOM_EMOJI),
        None
    )
    if ce:
        key = f"<custom:{ce.custom_emoji_id}>"
    else:
        key = key_raw  

    try:
        weight = int(val_raw)
    except ValueError:
        return await msg.reply_text("ℹ️ Вторая часть должна быть числом.")

    # update & save
    emoji_weights[key] = weight
    save_emoji_weights()

    await msg.reply_text(f"✅ Обновлено: {key} → {weight}")

async def change_social_rating(update: Update, context: CallbackContext):
    caller = update.effective_user
    if not caller or caller.id != TARGET_USER:
        return

    reply = update.message.reply_to_message
    if not reply:
        await update.message.reply_text(
            "Ты должна ответить на сообщение нужного чатера\n"
            "/sc <+N или -N> [причина]\n"
            "Например (ответом): /sc +3 Спасибки за вклад!"
        )
        return

    target = reply.from_user
    target_id = target.id
    display_name = target.username and f"@{target.username}" or target.full_name or str(target_id)

    if not context.args:
        await update.message.reply_text("❌ Ты должна указать +N или -N, например /sc +2")
        return

    diff_str = context.args[0]
    try:
        diff = int(diff_str)
    except ValueError:
        await update.message.reply_text("❌ Вторым аргументом должно быть число, например +5 или -2.")
        return

    if target_id not in social_rating:
        social_rating[target_id] = {
            "additional_chat": 0,
            "additional_neri": 0,
            "additional_self": 0,
            "boosts": 0,
            "manual_rating": 0,
        }

    old = social_rating[target_id]["manual_rating"]
    social_rating[target_id]["manual_rating"] = old + diff

    save_social_rating()
    
    word = "получил"
    social_rating_image = SOCIAL_ADD_RATING_IMAGE
    if diff < 0:
        word = "потерял"
        social_rating_image = SOCIAL_SUB_RATING_IMAGE

    caption = f"✅ {display_name} {word} {abs(diff)} социальных кредитов"
    if len(context.args) > 1:
        reason = " ".join(context.args[1:])
        caption += f"\nПричина: {reason}"

    try:
        with open(social_rating_image, "rb") as photo:
            await context.bot.send_photo(
                chat_id=update.effective_chat.id,
                photo=photo,
                caption=caption
            )
    except FileNotFoundError:
        await context.bot.send_message(
            chat_id=update.effective_chat.id,
            text=caption
        )


def main():
    mc = TelegramClient('anon', API_ID, API_HASH)
    
    mc.start(bot_token=BOT_TOKEN)

    app = (
        ApplicationBuilder()
        .token(BOT_TOKEN)
        .get_updates_read_timeout(30)
        .get_updates_write_timeout(30)
        .build()
    )
    app.add_handler(
        MessageHandler(filters.Chat(chat_id=ORIG_CHANNEL_ID) & ~filters.COMMAND, handle_cocksize)
    )
    
    app.add_handler(CommandHandler("edit_weights", edit_weights_cmd))
    app.add_handler(
        MessageHandler(
            filters.TEXT & filters.REPLY,
            edit_weights_reply
        ),
        group=1
    )
    app.add_handler(CommandHandler("notify",   subscribe))
    app.add_handler(CommandHandler("stop", unsubscribe))
    app.add_handler(CommandHandler("start", start, filters=filters.ChatType.PRIVATE))
    app.add_handler(CommandHandler("start", warn_use_dm, filters=~filters.ChatType.PRIVATE))
    app.add_handler(CommandHandler("rating", show_rating))
    app.add_handler(CommandHandler("ban", ban_media))
    app.add_handler(CommandHandler("block", block_media))
    app.add_handler(CommandHandler("unban", unban_media))
    app.add_handler(CommandHandler("shutdown", shutdown_bot))
    app.add_handler(CommandHandler("top", top_command))
    app.add_handler(CommandHandler("sc", change_social_rating))
    
    app.add_handler(CallbackQueryHandler(stats_page_callback, pattern=r"^stats:(?:global|daily|social|cock):\d+$"))
    app.add_handler(CallbackQueryHandler(follow_callback, pattern=r"^follow$"))

    @mc.on(events.MessageDeleted(chats=ORIG_CHANNEL_ID))
    async def on_deleted(event):
        print("delted in chat id: ", event.chat_id)
        for msg_id in event.deleted_ids:
            await delete_forwards(app.bot, ORIG_CHANNEL_ID, msg_id)
        
        save_forward_map()
    
    @mc.on(events.MessageEdited(chats=ORIG_CHANNEL_ID))
    async def on_edited(event):
        print("edited in chat id: ", event.chat_id)
        orig_id   = event.chat_id
        orig_msg  = event.message.id
        await edit_forwards(app.bot, event, orig_id, orig_msg)

    @mc.on(events.Raw)
    async def handler(event):
        if not isinstance(event, UpdateBotMessageReaction):
            return
        await on_message_reaction(mc, event)

    app.job_queue.run_repeating(
        persist_stats,
        interval=300,
        first=300 
    )
    
    app.job_queue.run_daily(
        reset_daily,
        time=time(hour=0, minute=0, tzinfo=TYUMEN)
    )

    app.run_polling(
        timeout=30,
    )
    print("exiting")

if __name__ == "__main__":
    main()
