import glob
import json
import logging
import os
import re
import sqlite3

from dotenv import load_dotenv

import gspread
from oauth2client.service_account import ServiceAccountCredentials

from datetime import datetime, timedelta
from zoneinfo import ZoneInfo

from pathlib import Path

from telethon import TelegramClient
from telethon.tl.functions.channels import GetParticipantRequest

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
CHAT_AFK_TIMEOUT = timedelta(minutes=30)
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

patterns_ = []

def _is_mod(user_id: int) -> bool:
    return user_id in MODERATORS

def daily_path_for_(date_obj: datetime.date) -> Path:
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
    today_path = daily_path_for_(today)
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
    path = daily_path_for_(today)
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
    global patterns_
    patterns_ = [
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