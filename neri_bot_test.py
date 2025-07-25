import asyncio
import glob
import hashlib
import html
import json
import io
import logging
import math
import matplotlib.pyplot as plt
import os
import random
import re
import sqlite3
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
    InputMediaPhoto,
    MessageEntity,
    Update,
    WebAppInfo,
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

BASE_URL          = os.environ["BASE_URL"]
MY_BOT_USERNAME   = os.environ["MY_BOT_USERNAME"]
WEB_APP_NAME      = os.environ["WEB_APP_NAME"] 

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
BANWORDS_FILE = "banwords.json"
META_FILE = "meta.json"
SOCIAL_ADD_RATING_IMAGE = "add_rating.png"
SOCIAL_SUB_RATING_IMAGE = "sub_rating.png"
CASINO_IMAGE = "casino.jpg"
DAILY_STATS_DIR.mkdir(exist_ok=True)

TYUMEN = ZoneInfo("Asia/Yekaterinburg")
EDIT_TIMEOUT = timedelta(hours=48)
CHAT_AFK_TIMEOUT = timedelta(minutes=15)
PAGE_SIZE = 10

db = sqlite3.connect("info.db", check_same_thread=False)
db.row_factory = sqlite3.Row

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

HOMOGLYPHS = {
    ord('x'): 'х', ord('X'): 'х',
    ord('o'): 'о', ord('O'): 'о', ord('0'): 'о',
    ord('a'): 'а', ord('A'): 'а',
    ord('p'): 'р', ord('P'): 'р',
    ord('h'): 'н', ord('H'): 'н',
    ord('t'): 'т', ord('T'): 'т',
    ord('y'): 'у', ord('Y'): 'у',
    ord('k'): 'к', ord('K'): 'к',
    ord('c'): 'с', ord('C'): 'с',
    ord('m'): 'м', ord('M'): 'м',
}

_patterns = []

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

def load_old_social_rating():
    global old_social_rating
    old_social_rating = {}
    
    root_dir = os.getcwd()
    pattern = os.path.join(root_dir, "social_rating_*.json")

    for path in glob.glob(pattern):
        if os.path.abspath(path) == os.path.abspath(SOCIAL_RATING_FILE):
            continue

        try:
            with open(path, encoding="utf-8") as f:
                raw = json.load(f)
        except (FileNotFoundError, json.JSONDecodeError):
            continue

        for uid_str, v in raw.items():
            uid = int(uid_str)
            hist = old_social_rating.setdefault(uid, {
                "additional_chat": 0,
                "additional_neri": 0,
                "additional_self": 0,
                "boosts":          0,
                "manual_rating":   0,
            })

            hist["additional_chat"] += int(v.get("additional_chat", 0))
            hist["additional_neri"] += int(v.get("additional_neri", 0))
            hist["additional_self"] += int(v.get("additional_self", 0))
            hist["boosts"]          += int(v.get("boosts", 0))
            hist["manual_rating"]   += int(v.get("manual_rating", 0))

def load_social_rating():
    global social_rating
    try:
        raw = json.loads(open(SOCIAL_RATING_FILE, encoding="utf-8").read())
        social_rating = {}
        for uid_str, v in raw.items():
            uid = int(uid_str)
            rc = {int(rid): int(cnt)
                  for rid, cnt in v.get("reactor_counts", {}).items()}
            total = int(v.get("total_reacts", sum(rc.values())))
            social_rating[uid] = {
                "reactor_counts": rc,
                "total_reacts":    total,
                "additional_chat": int(v.get("additional_chat", 0)),
                "additional_neri": int(v.get("additional_neri", 0)),
                "additional_self": int(v.get("additional_self", 0)),
                "boosts":          int(v.get("boosts", 0)),
                "manual_rating":   int(v.get("manual_rating", 0)),
            }
    except (FileNotFoundError, json.JSONDecodeError):
        social_rating = {}

def save_social_rating():
    dump = {
        str(uid): {
            "reactor_counts": {str(rid): cnt
                               for rid, cnt in info["reactor_counts"].items()},
            "total_reacts":    info["total_reacts"],
            "additional_chat": info["additional_chat"],
            "additional_neri": info["additional_neri"],
            "additional_self": info["additional_self"],
            "boosts":          info["boosts"],
            "manual_rating":   info["manual_rating"],
        }
        for uid, info in social_rating.items()
    }
    with open(SOCIAL_RATING_FILE, "w", encoding="utf-8") as f:
        json.dump(dump, f, ensure_ascii=False, indent=2)

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

def load_banwords():
    try:
        with open(BANWORDS_FILE, "r") as f:
            return set(json.load(f))
    except (IOError, ValueError):
        return set()

def save_banwords():
    with open(BANWORDS_FILE, "w") as f:
        json.dump(list(BANWORDS), f)

def load_subscribers():
    try:
        with open(SUBSCRIBERS_FILE, "r") as f:
            return set(json.load(f))
    except (IOError, ValueError):
        return set()

def save_subscribers(subs: set):
    with open(SUBSCRIBERS_FILE, "w") as f:
        json.dump(list(subs), f)

def load_meta_info():
    global META_INFO
    try:
        with open(META_FILE, "r", encoding="utf-8") as f:
            data = json.load(f)
    except (FileNotFoundError, json.JSONDecodeError):
        data = {}

    META_INFO["afk_time"] = data.get("afk_time", 0)
    META_INFO["alive_time"] = data.get("alive_time", 0)

    first_ts = data.get("first_message_time")
    if isinstance(first_ts, (int, float)):
        META_INFO["first_message_time"] = datetime.fromtimestamp(first_ts)
    else:
        META_INFO["first_message_time"] = datetime.now()

    last_ts = data.get("last_message_time")
    if isinstance(last_ts, (int, float)):
        META_INFO["last_message_time"] = datetime.fromtimestamp(last_ts)
    else:
        META_INFO["last_message_time"] = datetime.now()

    join_ts = data.get("join_bot_time")
    if isinstance(join_ts, (int, float)):
        META_INFO["join_bot_time"] = datetime.fromtimestamp(join_ts)
    else:
        META_INFO["join_bot_time"] = datetime.now()

def save_meta_info():
    first_dt = META_INFO.get("first_message_time", datetime.now())
    last_dt  = META_INFO.get("last_message_time",  datetime.now())
    join_dt  = META_INFO.get("join_bot_time",  datetime.now())

    serializable = {
        "afk_time": META_INFO.get("afk_time", 0),
        "alive_time": META_INFO.get("alive_time", 0),
        "first_message_time": first_dt.timestamp(),
        "last_message_time":  last_dt.timestamp(),
        "join_bot_time":  join_dt.timestamp(),
    }

    with open(META_FILE, "w", encoding="utf-8") as f:
        json.dump(serializable, f, ensure_ascii=False, indent=2)

# initialize in-memory set
SUBSCRIBERS = load_subscribers()
BANWORDS = load_banwords()
MODERATORS = load_moderators()
META_INFO = {}
forward_map = {}
banlist: list[dict] = []
message_stats: dict[int, int] = {}
daily_stats:  dict[int,int] = {}
stats_sessions: dict[int, int] = {}
last_sizes: dict[int, dict] = {}
social_rating: dict[int, dict] = {}
old_social_rating: dict[int, dict] = {}
emoji_weights: dict[str,int] = {}
load_forward_map()
load_banlist()
load_stats()
load_last_sizes()
load_old_social_rating()
load_social_rating()
load_emoji_weights()
load_meta_info()

def compile_patterns():
    global _patterns
    _patterns = [
        re.compile(
            ''.join(
                rf'(?:{re.escape(ch)}\W*)+' 
                for ch in word
            ),
            re.IGNORECASE
        )
        for word in BANWORDS
    ]
    #print("_patterns: ", _patterns)

compile_patterns()

logging.basicConfig(
    format="%(asctime)s - %(name)s - %(levelname)s - %(message)s",
    level=logging.INFO,
)

def check_init_user_table():
    row = db.execute("SELECT COUNT(*) AS cnt FROM user").fetchone()
    if row["cnt"] == 0:
        for uid, info in social_rating.items():
            total = count_total_rating(social_rating, uid)
            db.execute(
                "INSERT INTO user (id, coins) VALUES (?, ?)",
                (uid, total)
            )

def update_coins(uid, coins):
    with db:
        row = db.execute(
            "SELECT coins FROM user WHERE id = ?",
            (uid,)
        ).fetchone()
        current = row["coins"] if row else 0
        new_balance = current + coins
        db.execute(
            "INSERT INTO user (id, coins) VALUES (?, ?) "
            "ON CONFLICT(id) DO UPDATE SET coins = excluded.coins",
            (uid, new_balance)
        )

def count_total_rating(sr, uid):
    if uid not in sr:
        return 0
    info = sr[uid]
    return info.get("additional_chat", 0) + info.get("additional_neri", 0) * 15 + info.get("boosts", 0) * 5 + info.get("manual_rating", 0)

def count_neri_rating(sr, uid):
    if uid not in sr:
        return 0
    info = sr[uid]
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
    # выбираем, откуда брать данные и как подписать режим
    if mode == "global":
        items   = list(message_stats.items())
        mode_ru = "глобально по сообщениям"

    elif mode == "daily":
        items   = list(daily_stats.items())
        mode_ru = "сегодня"

    elif mode == "cock":
        items   = [(uid, info["size"]) for uid, info in last_sizes.items()]
        mode_ru = "по размеру"

    elif mode == "social":
        items = []
        for uid, info in social_rating.items():
            if uid == TARGET_USER:
                continue
            total = count_total_rating(social_rating, uid)
            neri  = count_neri_rating(social_rating, uid)
            items.append((uid, total, neri))
        mode_ru = "соц. рейтинг (текущий)"

    elif mode == "social_global":
        items = []
        for uid, info in old_social_rating.items():
            if uid == TARGET_USER:
                continue
            total = count_total_rating(old_social_rating, uid) + count_total_rating(social_rating, uid)
            neri  = count_neri_rating(old_social_rating, uid) + count_neri_rating(social_rating, uid)
            items.append((uid, total, neri))
        mode_ru = "соц. рейтинг (глобальный)"

    elif mode == "casino":
        # Загружаем монетки из БД: таблица users (или замените на вашу)
        with db:
            rows = db.execute(
                "SELECT id, coins FROM user"
            ).fetchall()
        items   = [(r["id"], r["coins"]) for r in rows]
        mode_ru = "по рыженке"

    else:
        items   = []
        mode_ru = mode

    # сортируем, разбиваем на страницы
    sorted_stats = sorted(items, key=lambda kv: kv[1], reverse=True)
    total        = len(sorted_stats)
    start, end   = page * PAGE_SIZE, (page + 1) * PAGE_SIZE
    last_page    = max((total - 1) // PAGE_SIZE, 0)
    chunk        = sorted_stats[start:end]

    # формируем текст
    header = f"📊 Топ ({mode_ru.capitalize()}) #{start+1}–{min(end, total)} из {total}:\n"
    lines = [header]
    for rank, entry in enumerate(chunk, start=start+1):
        # social-режимы имеют тройку (uid, total, neri)
        if mode.startswith("social"):
            uid, full, neri = entry
        else:
            uid, full = entry
            neri = None

        # пытаемся получить имя пользователя
        try:
            uc   = await bot.get_chat(uid)
            name = parse_name(uc)
        except:
            name = escape(str(uid))

        # формируем строку в зависимости от режима
        if mode == "cock":
            lines.append(f"{rank}. {name}: {float(full):.1f} см")
        elif mode == "casino":
            lines.append(f"{rank}. {name}: {full} рыженки")
        elif mode.startswith("social"):
            lines.append(f"{rank}. {name}: {full}({neri}) рейтинга")
        else:
            lines.append(f"{rank}. {name}: {full} сообщений")

    text = "\n".join(lines)

    modes1 = [
        ("global",        "🌐 Всё"),
        ("daily",         "📅 Сегодня"),
        ("cock",          "🍆 Размер"),
    ]
    modes2 = [
        ("social",       "⚡ Соц. рейтинг"),
        ("social_global","🌍 Соц. рейтинг (всего)"),
        ("casino",       "🎰 Казино"),
    ]
    mode1_buttons = [
        InlineKeyboardButton(label, callback_data=f"stats:{m}:0")
        for m, label in modes1
    ]
    mode2_buttons = [
        InlineKeyboardButton(label, callback_data=f"stats:{m}:0")
        for m, label in modes2
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
    if mode in ("social", "social_global", "cock", "casino"):
        nav_buttons.append(
            InlineKeyboardButton("Последняя", callback_data=f"stats:{mode}:{last_page}")
        )

    kb = InlineKeyboardMarkup([mode1_buttons, mode2_buttons, nav_buttons])
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

def normalize(text):
    return text.translate(HOMOGLYPHS).lower()

def check_banwords(text):
    norm = normalize(text)
    #print("norm: ", norm)
    for pat in _patterns:
        if pat.search(norm):
            return True
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

def format_duration(delta: timedelta) -> str:
    total = int(delta.total_seconds())
    hrs, rem = divmod(total, 3600)
    mins, secs = divmod(rem, 60)
    parts = []
    if hrs:  parts.append(f"{hrs} ч")
    if mins: parts.append(f"{mins} мин")
    if secs or not parts: parts.append(f"{secs} сек")
    return " ".join(parts)

async def check_afk_time(bot, user, chat_id):
    now = datetime.now()

    last_time = META_INFO.get("last_message_time", now)
    first_time = META_INFO.get("first_message_time", last_time)

    delta_dead = now - last_time
    delta_alive = last_time - first_time

    prev_dead_secs = META_INFO.get("afk_time", 0)
    prev_dead_td = timedelta(seconds=prev_dead_secs)
    prev_alive_secs = META_INFO.get("alive_time", 0)
    prev_alive_td = timedelta(seconds=prev_alive_secs)

    if delta_dead > CHAT_AFK_TIMEOUT or delta_dead > prev_dead_td:
        reanimator = parse_name(user) if user else "<i>неизвестный герой</i>"

        new_dead_record = delta_dead > prev_dead_td
        new_alive_record = delta_alive > prev_alive_td

        if new_dead_record:
            dead_info = (
                f"🔥 Новый антирекорд простоя! "
                f"Чат был мёртв {format_duration(delta_dead)} "
                f"(предыдущий рекорд — {format_duration(prev_dead_td)})."
            )
            META_INFO["afk_time"] = int(delta_dead.total_seconds())
        else:
            dead_info = f"⏱ Чат был мёртв {format_duration(delta_dead)}."

        if new_alive_record:
            alive_info = (
                f"🚀 Новый рекорд «жизни» чата! "
                f"Вы срали непрерывно {format_duration(delta_alive)} "
                f"(предыдущий рекорд — {format_duration(prev_alive_td)})."
            )
            META_INFO["alive_time"] = int(delta_alive.total_seconds())
        else:
            alive_info = f"Чат был жив {format_duration(delta_alive)}."

        reanim_info  = f"💉 Реанимационные мероприятия успешно провёл {reanimator}."
        text = "\n\n".join([dead_info, alive_info, reanim_info])
        await bot.send_message(chat_id, text, parse_mode="HTML")

        META_INFO["first_message_time"] = now

    META_INFO["last_message_time"] = now
    save_meta_info()



async def handle_cocksize(update: Update, context: ContextTypes.DEFAULT_TYPE):
    print("handle_cocksize")
    user = update.effective_user
    msg = update.message
    
    if not msg:
        return
    
    if not user:
        return

    text = msg.text or msg.caption or ""
    if check_banwords(text) and user.id != TARGET_USER:
        print("We got blocked text!")
        try:
            await context.bot.delete_message(
                chat_id=update.effective_chat.id,
                message_id=msg.message_id
            )
        except:
            print("failed to delete message")
            pass 
        entry = {
            "timestamp":    datetime.utcnow().strftime("%Y-%m-%d %H:%M:%S"),
            "chat_id":      update.effective_chat.id,
            "user_id":      user.id,
            "username":     user.username,
            "first_name":   user.first_name,
            "last_name":    user.last_name,
            "text":         text
        }
        
        print(entry)
        return
    
    sig = extract_media_signature(msg)
    
    if sig and user.id != TARGET_USER:
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
    
    print(update)

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
        social_rating[user.id] = {
            "reactor_counts": {},
            "total_reacts":    0,
            "additional_chat": 0,
            "additional_neri": 0,
            "additional_self": 0,
            "boosts":          0,
            "manual_rating":   0,
        }
        save_social_rating()

    bc = getattr(msg, "sender_boost_count", None)
    if bc is None and hasattr(msg, "api_kwargs"):
        bc = msg.api_kwargs.get("sender_boost_count", 0)
    boost_count = int(bc or 0)
    if social_rating[user.id]["boosts"] != boost_count:
        social_rating[user.id]["boosts"] = boost_count
        save_social_rating()
    
    await check_afk_time(context.bot, user, update.effective_chat.id)
    
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
    
    if reactor_id != ORIG_CHANNEL_ID and count_total_rating(social_rating, reactor_id) < -100:
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
        
    if delta == 0:
        print("delta is zero, we quit")
        return

    entry = social_rating.setdefault(author_id, {
        "reactor_counts": {},
        "total_reacts":    0,
        "additional_chat": 0,
        "additional_neri": 0,
        "additional_self": 0,
        "boosts":          0,
        "manual_rating":   0,
    })
    
    if reactor_id != TARGET_USER and reactor_id != ORIG_CHANNEL_ID and author_id != TARGET_USER:
        rc = entry["reactor_counts"]
        prev = rc.get(reactor_id, 0)

        total = sum(rc.values())

        if total >= 50:
            cap = math.floor(0.2 * total)
            excess = 0
            if prev > cap:
                print("Too many reacts counted!")
                return

        rc[reactor_id] = prev + 1
        entry["total_reacts"] = total + 1
    
    print("delta: ", delta)
    if delta == 0:
        return  # nothing to change

    receiver = author_id
    entry_name = "additional_chat"
    multiplier = 1
    if reactor_id == TARGET_USER or reactor_id == ORIG_CHANNEL_ID:
        entry_name = "additional_neri"
        multiplier = 15
    elif author_id == TARGET_USER:
        entry_name = "additional_self"
        
    entry[entry_name] = entry[entry_name] + delta
    update_coins(author_id, multiplier * delta)
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
    save_meta_info()

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
    update_coins(target_id, diff)

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

async def reset_monthly_social_rating(context: ContextTypes.DEFAULT_TYPE):
    now = datetime.now(TYUMEN)

    if now.day != 1:
        return

    prev = now.replace(day=1) - timedelta(days=2)
    month_name = prev.strftime("%B").lower()  # например 'july'
    archive_file = f"social_rating_{month_name}.json"

    dump = {
        str(uid): {
            "reactor_counts": {str(rid): cnt
                               for rid, cnt in info["reactor_counts"].items()},
            "total_reacts":    info["total_reacts"],
            "additional_chat": info["additional_chat"],
            "additional_neri": info["additional_neri"],
            "additional_self": info["additional_self"],
            "boosts":          info["boosts"],
            "manual_rating":   info["manual_rating"],
        }
        for uid, info in social_rating.items()
    }

    with open(archive_file, "w", encoding="utf-8") as f:
        json.dump(dump, f, ensure_ascii=False, indent=2)

    social_rating.clear()
    save_social_rating()
    load_old_social_rating()

    print(f"[Monthly reset] Archived to {archive_file} and cleared current social_rating.")

async def add_banword(update: Update, context: CallbackContext):
    user = update.effective_user
    msg  = update.effective_message

    if not user or user.id not in MODERATORS:
        return

    if not context.args:
        await msg.reply_text("Использование: /bw слово1 слово2 …")
        return

    new_words = {w.strip().lower() for w in context.args if w.strip()}

    added = new_words - BANWORDS
    BANWORDS.update(added)

    if added:
        await msg.reply_text(
            f"✅ Добавлено {len(added)} новые слова: " +
            ", ".join(sorted(added))
        )
    else:
        await msg.reply_text("Все эти слова уже в списке.")
    save_banwords()
    compile_patterns()

async def remove_banword(update: Update, context: CallbackContext):
    user = update.effective_user
    msg  = update.effective_message

    if not user or user.id not in MODERATORS:
        return

    if not context.args:
        await msg.reply_text("Использование: /remove_bw слово1 слово2 …")
        return

    requested = {w.strip().lower() for w in context.args if w.strip()}

    present    = requested & BANWORDS
    not_found  = requested - BANWORDS

    BANWORDS.difference_update(present)

    if present:
        await msg.reply_text(
            f"✅ Убрано {len(present)} слов(а): " + ", ".join(sorted(present)) +
            (f"\n⚠️ Не найдено: {', '.join(sorted(not_found))}"
             if not_found else "")
        )
    else:
        await msg.reply_text("Ни одно из указанных слов не было в списке бан-слов.")
    save_banwords()
    compile_patterns()


def create_bet_image_and_text(pid):
    # 1) Gather stakes
    opts = db.execute(
        """
        SELECT po.option, COALESCE(SUM(b.amount), 0) AS total
        FROM poll_option po
        LEFT JOIN bets b
          ON po.poll_id = b.poll_id
         AND po.idx     = b.option_idx
        WHERE po.poll_id = ?
        GROUP BY po.idx, po.option
        """,
        (pid,)
    ).fetchall()

    # 2) Fetch question
    row = db.execute(
        "SELECT question FROM poll WHERE id = ?",
        (pid,)
    ).fetchone()
    if row is None:
        raise ValueError(f"Poll {pid} not found")
    question = row["question"]

    # 3) Compute total votes across all options
    total_votes = sum(r["total"] for r in opts)

    # 4) Build the markdown list: "1. Option — X votes, coef: Y.YY"
    lines = []
    for i, r in enumerate(opts):
        votes = r["total"]
        if votes > 0:
            coef = total_votes / votes
            coef_str = f", коэф.: {coef:.2f}"
        else:
            coef_str = ""
        lines.append(f"{i+1}. {r['option']} — {votes} рыженки{coef_str}")
    options_md = "\n".join(lines)

    # 5) Build the caption text
    text = (
        f"🎲 *{question}* (#{pid})\n\n"
        f"{options_md}\n\n"
        "Сделай свою ставку, нажав на кнопку ниже:"
    )
    
    link = f"https://t.me/{MY_BOT_USERNAME}/{WEB_APP_NAME}?startapp=poll_{pid}"
    kb = InlineKeyboardMarkup([[
        InlineKeyboardButton("Сделать ставку 🔗", url=link)
    ]])

    # 6) Prepare for pie-chart: only nonzero slices
    labels = [r["option"] for r in opts]
    sizes  = [r["total"]  for r in opts]
    nonzero = [(lbl, sz) for lbl, sz in zip(labels, sizes) if sz > 0]

    # 7) If no votes at all, return the default image
    if not nonzero or True:
        with open(CASINO_IMAGE, 'rb') as f:
            buf = io.BytesIO(f.read())
        buf.seek(0)
        return text, kb, buf

    # 8) Otherwise draw the pie
    labels, sizes = zip(*nonzero)
    fig, ax = plt.subplots()
    ax.pie(sizes, labels=labels, autopct='%1.1f%%', startangle=90)
    ax.axis('equal')

    buf = io.BytesIO()
    plt.savefig(buf, format='PNG', bbox_inches='tight')
    plt.close(fig)
    buf.seek(0)
    return text, kb, buf


async def start_bet(update: Update, context: CallbackContext):
    print("starting bet")
    user = update.effective_user
    msg  = update.effective_message

    if not user or user.id not in MODERATORS:
        return

    text = msg.text or ""
    parts = text.split(" ", 1)
    if len(parts) < 2 or not parts[1].strip():
        return await msg.reply_text(
            "❌ Использование: /start_bet Вопрос? Опция A;Опция B;Опция C"
        )
    payload = parts[1].strip()

    try:
        question_part, opts_part = payload.split("?", 1)
        question = question_part.strip() + "?"
        options  = [o.strip() for o in opts_part.split(";") if o.strip()]
        if len(options) < 2:
            raise ValueError()
    except ValueError:
        return await msg.reply_text(
            "❌ Неверный формат. Использование:\n"
            "/start_bet Вопрос? Опция A;Опция B;Опция C"
        )

    # insert into your SQLite
    with db:
        cur = db.execute(
            "INSERT INTO poll (question) VALUES (?)",
            (question,)
        )
        poll_id = cur.lastrowid

        for idx, text in enumerate(options):
            db.execute(
                "INSERT INTO poll_option (poll_id, idx, option) VALUES (?,?,?)",
                (poll_id, idx, text)
            )
    
    text, kb, buf = create_bet_image_and_text(poll_id)
    
    sent = await context.bot.send_photo(
        chat_id=ORIG_CHANNEL_ID,
        photo=buf,
        caption=text,
        reply_markup=kb,
        parse_mode="Markdown"
    )
    
    with db:
        db.execute(
            "UPDATE poll SET chat_id=?, message_id=? WHERE id=?",
            (sent.chat_id, sent.message_id, poll_id)
        )

    # confirm to the moderator
    await msg.reply_text(f"✅ Опрос #{poll_id} создан.")

async def close_bet(update: Update, context: CallbackContext):
    user = update.effective_user
    msg  = update.effective_message

    # only moderators
    if not user or user.id not in MODERATORS:
        return

    args = context.args or []
    if len(args) != 1 or not args[0].isdigit():
        return await msg.reply_text("❌ Использование: /close_bet <poll_id>")
    poll_id = int(args[0])

    # 1) Check status
    with db:
        poll_row = db.execute(
            "SELECT status FROM poll WHERE id = ?", (poll_id,)
        ).fetchone()
        if not poll_row:
            return await msg.reply_text(f"ℹ️ Опрос #{poll_id} не найден.")
        if poll_row["status"] != 0:
            return await msg.reply_text(f"ℹ️ Опрос #{poll_id} уже закрыт ранее.")

        # mark closed (no more new stakes)
        db.execute("UPDATE poll SET status = 1 WHERE id = ?", (poll_id,))
    await msg.reply_text(f"⏸ Опрос #{poll_id} закрыт. Новые ставки не принимаются.")

async def finish_bet(update: Update, context: CallbackContext):
    user = update.effective_user
    msg  = update.effective_message

    if not user or user.id not in MODERATORS:
        return

    args = context.args or []
    if len(args) != 2 or not all(arg.isdigit() for arg in args):
        return await msg.reply_text(
            "❌ Использование: /finish_bet <poll_id> <winning_option_idx>"
        )
    poll_id, win_idx = map(int, args)

    with db:
        poll_row = db.execute(
            "SELECT status, question FROM poll WHERE id = ?",
            (poll_id,)
        ).fetchone()
        if not poll_row:
            return await msg.reply_text(f"ℹ️ Опрос #{poll_id} не найден.")
        question = poll_row["question"]
        if poll_row["status"] == 2:
            return await msg.reply_text(f"ℹ️ Опрос #{poll_id} уже завершён ранее.")

        opt_row = db.execute(
            "SELECT option FROM poll_option WHERE poll_id = ? AND idx = ?",
            (poll_id, win_idx)
        ).fetchone()
        if not opt_row:
            return await msg.reply_text(
                f"❌ Вариант #{win_idx} не найден в опросе #{poll_id}."
            )
        winning_option = opt_row["option"]

        # 3) Загружаем все ставки
        bets = db.execute(
            "SELECT user_id, option_idx, amount FROM bets WHERE poll_id = ?",
            (poll_id,)
        ).fetchall()
        # Если ставок не было — сразу отмечаем опрос завершённым
        if not bets:
            db.execute(
                "UPDATE poll SET status = 2, winner_idx = ? WHERE id = ?",
                (win_idx, poll_id)
            )
            return await msg.reply_text(
                f"ℹ️ Опрос #{poll_id} завершён.\n"
                f"Вопрос: {question}\n"
                f"Выиграл вариант: «{winning_option}»\n"
                "Ставок не было."
            )

    # 4) Разбиваем на победителей и проигравших
    winners = [(r["user_id"], r["amount"]) for r in bets if r["option_idx"] == win_idx]
    losers  = [(r["user_id"], r["amount"]) for r in bets if r["option_idx"] != win_idx]
    total_win  = sum(a for _, a in winners)
    total_lose = sum(a for _, a in losers)

    # 5) Отмечаем опрос как завершённый и сохраняем индекс победителя
    with db:
        db.execute(
            "UPDATE poll SET status = 2, winner_idx = ? WHERE id = ?",
            (win_idx, poll_id)
        )

    # 6) Если никто не ставил на победивший вариант
    if total_win == 0:
        return await context.bot.send_message(
            chat_id=ORIG_CHANNEL_ID,
            text=(
                f"⚠️ Опрос #{poll_id} завершён.\n"
                f"Вопрос: {question}\n"
                f"Выиграл вариант: «{winning_option}», "
                "но на него никто не ставил.\n"
                f"Общие потери: {total_lose} рыженки."
            )
        )

    # 7) Начисляем выплаты
    payouts = {}
    for uid, stake in winners:
        share  = (stake / total_win) * total_lose
        payout = int(stake + share)
        update_coins(uid, payout)
        payouts[uid] = payout

    # 8) Собираем итоговое сообщение
    lines = [
        f"🏁 Опрос #{poll_id} завершён!",
        f"Вопрос: {question}",
        f"Выиграл вариант: «{winning_option}»",
        f"Всего ставок: {total_win + total_lose} рыженки "
        f"(на победу — {total_win}, на поражение — {total_lose})",
        "",
        "💰 Выплаты:"
    ]
    for uid, amt in payouts.items():
        try:
            # fetch the User/Chat object and format via parse_name
            user_chat = await context.bot.get_chat(uid)
            name = parse_name(user_chat)
        except Exception:
            # fallback in case of error
            name = f"[ID {uid}]"
        lines.append(f"• {name}: +{amt} рыженки")

    await context.bot.send_message(
        chat_id=ORIG_CHANNEL_ID,
        text="\n".join(lines),
        parse_mode=constants.ParseMode.HTML,
    )

async def refresh_polls(context: CallbackContext):
    rows = db.execute(
        "SELECT id, chat_id, message_id FROM poll WHERE status=0"
    ).fetchall()

    for row in rows:
        pid, chat, msg_id = row["id"], row["chat_id"], row["message_id"]
        
        if not chat or not msg_id:
            continue

        text, kb, buf = create_bet_image_and_text(pid)
        media = InputMediaPhoto(
            media=buf,
            caption=text,
            parse_mode="Markdown"
        )

        try:
            await context.bot.edit_message_media(
                chat_id=chat,
                message_id=msg_id,
                media=media,
                reply_markup=kb
            )
        except Exception as e:
            print(f"Failed to refresh poll {pid}: {e}")

def main():
    check_init_user_table()

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
    app.add_handler(CommandHandler("bw", add_banword))
    app.add_handler(CommandHandler("remove_bw", remove_banword))
    app.add_handler(CommandHandler("start_bet", start_bet))
    app.add_handler(CommandHandler("close_bet", close_bet))
    app.add_handler(CommandHandler("finish_bet", finish_bet))
    
    app.add_handler(CallbackQueryHandler(stats_page_callback, pattern=r"^stats:(?:global|daily|social|social_global|cock|casino):\d+$"))
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
    
    app.job_queue.run_repeating(
        refresh_polls,
        interval=300,
        first=240
    )
    
    app.job_queue.run_daily(
        reset_daily,
        time=time(hour=0, minute=0, tzinfo=TYUMEN)
    )
    
    app.job_queue.run_daily(
        reset_monthly_social_rating,
        time=time(hour=0, minute=1, tzinfo=TYUMEN)
    )

    app.run_polling(
        timeout=30,
    )
    print("exiting")

if __name__ == "__main__":
    main()
