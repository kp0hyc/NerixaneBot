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

# ─── CONFIG ─────────────────────────────────────────────────────────────────────
load_dotenv()

BOT_TOKEN         = os.environ["BOT_TOKEN"]
API_ID            = int(os.environ["API_ID"])
API_HASH          = os.environ["API_HASH"]

SPREADSHEET_ID    = os.environ["SPREADSHEET_ID"]
GOOGLE_CREDS_PATH = os.environ["GOOGLE_CREDS_PATH"]

ORIG_CHANNEL_ID   = int(os.environ["ORIG_CHANNEL_ID"])
GAMBLING_CHANNEL_ID   = int(os.environ["GAMBLING_CHANNEL_ID"])
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

BASE_DIR = Path(__file__).resolve().parent.parent

MODERATORS_FILE = BASE_DIR / "moderators.json"
SUBSCRIBERS_FILE = BASE_DIR / "subscribers.json"
FORWARD_MAP_FILE = BASE_DIR / "forward_map.json"
BANLIST_FILE = BASE_DIR / "banlist.json"
STATS_FILE = BASE_DIR / "message_stats.json"
DAILY_STATS_DIR = BASE_DIR / "daily_stats"
LAST_SIZES_FILE = BASE_DIR / "last_sizes.json"
SOCIAL_RATING_FILE = BASE_DIR / "social_rating.json"
WEIGHTS_FILE = BASE_DIR / "emoji_weights.json"
BANWORDS_FILE = BASE_DIR / "banwords.json"
META_FILE = BASE_DIR / "meta.json"
SOCIAL_ADD_RATING_IMAGE = BASE_DIR / "add_rating.png"
SOCIAL_SUB_RATING_IMAGE = BASE_DIR / "sub_rating.png"
CASINO_IMAGE = BASE_DIR / "casino.jpg"
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
    ord('o'): 'о', ord('O'): 'о', ord('0'): 'о', ord('օ'): 'о',
    ord('a'): 'а', ord('A'): 'а', ord('@'): 'а',
    ord('p'): 'р', ord('P'): 'р',
    ord('h'): 'н', ord('H'): 'н',
    ord('t'): 'т', ord('T'): 'т',
    ord('y'): 'у', ord('Y'): 'у',
    ord('k'): 'к', ord('K'): 'к',
    ord('c'): 'с', ord('C'): 'с',
    ord('m'): 'м', ord('M'): 'м',
    ord('e'): 'е', ord('E'): 'е',
    ord('b'): 'в', ord('B'): 'в',
    ord('n'): 'п', ord('N'): 'п',
    ord('u'): 'и', ord('U'): 'и',
    ord('r'): 'г', ord('R'): 'г',
    ord('3'): 'з',
    ord('6'): 'б',
    ord('9'): 'д',
    ord('x'): 'х',
    ord('X'): 'х',
    ord('×'): 'х',
    ord('✕'): 'х',
    ord('❌'): 'х',
    ord('⤫'): 'х',
    ord('ҳ'): 'х',
    ord('🞩'): 'х',
    ord('χ'): 'х',
    ord('𝔁'): 'х',
    ord('𝕏'): 'х',
    ord('ⅹ'): 'х',
    ord('Ⅹ'): 'х',
    ord('ꭓ'): 'х',
    ord('ⲭ'): 'х',
}

def daily_path_for_(date_obj: datetime.date) -> Path:
    return DAILY_STATS_DIR / f"daily_stats_{date_obj.isoformat()}.json"

logging.basicConfig(
    format="%(asctime)s - %(name)s - %(levelname)s - %(message)s",
    level=logging.INFO,
)

class BotState:
    @classmethod
    def init(cls):
        cls.SUBSCRIBERS = cls.load_subscribers()
        cls.BANWORDS = cls.load_banwords()
        cls.MODERATORS = cls.load_moderators()
        print("Moderators: ", cls.MODERATORS)
        cls.META_INFO = {}
        cls.forward_map = {}
        cls.banlist = []
        cls.message_stats = {}
        cls.daily_stats = {}
        cls.stats_sessions = {}
        cls.last_sizes = {}
        cls.social_rating = {}
        cls.old_social_rating = {}
        cls.emoji_weights = {}
        cls.slot = True
        cls.indexed_users = {}
        cls.rd_users = set()
        
        cls.load_forward_map()
        cls.load_banlist()
        cls.load_stats()
        cls.load_last_sizes()
        cls.load_old_social_rating()
        cls.load_social_rating()
        cls.load_emoji_weights()
        cls.load_meta_info()
        cls.compile_patterns()
        cls.ensure_helpers_table()
        cls.ensure_slot_rolls_table()
        cls.ensure_white_bot_table()
        cls.ensure_white_msg_table()
        cls.ensure_random_deposit_table()

        cls.upgrade_users_table()

    @classmethod
    def compile_patterns(cls):
        cls.patterns_ = [
            re.compile(
                r'(?<![^\W\d_])' +  # not preceded by a letter
                ''.join(
                    rf'(?:{re.escape(ch)}\W*)+'
                    for ch in word
                ) +
                r'(?![^\W\d_])',     # not followed by a letter
                re.IGNORECASE
            )
            for word in cls.BANWORDS
        ]
        print("len: ", len(cls.patterns_))
        #print("_patterns: ", _patterns)

    @classmethod
    def load_meta_info(cls):
        try:
            with open(META_FILE, "r", encoding="utf-8") as f:
                data = json.load(f)
        except (FileNotFoundError, json.JSONDecodeError):
            data = {}

        cls.META_INFO["afk_time"] = data.get("afk_time", 0)
        cls.META_INFO["alive_time"] = data.get("alive_time", 0)

        first_ts = data.get("first_message_time")
        if isinstance(first_ts, (int, float)):
            cls.META_INFO["first_message_time"] = datetime.fromtimestamp(first_ts)
        else:
            cls.META_INFO["first_message_time"] = datetime.now()

        last_ts = data.get("last_message_time")
        if isinstance(last_ts, (int, float)):
            cls.META_INFO["last_message_time"] = datetime.fromtimestamp(last_ts)
        else:
            cls.META_INFO["last_message_time"] = datetime.now()

        join_ts = data.get("join_bot_time")
        if isinstance(join_ts, (int, float)):
            cls.META_INFO["join_bot_time"] = datetime.fromtimestamp(join_ts)
        else:
            cls.META_INFO["join_bot_time"] = datetime.now()

    @classmethod
    def load_emoji_weights(cls):
        if WEIGHTS_FILE.exists():
            try:
                raw = json.loads(WEIGHTS_FILE.read_text(encoding="utf-8"))
                # coerce keys to str and values to int
                cls.emoji_weights = {str(k): int(v) for k, v in raw.items()}
            except Exception:
                # keep defaults on error
                pass

    @classmethod
    def save_emoji_weights(cls):
        WEIGHTS_FILE.write_text(
            json.dumps(cls.emoji_weights, ensure_ascii=False, indent=2),
            encoding="utf-8"
        )

    @classmethod
    def load_old_social_rating(cls):
        cls.old_social_rating = {}
        
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
                hist = cls.old_social_rating.setdefault(uid, {
                    "reactor_counts": {},
                    "additional_chat":  0,
                    "additional_neri":  0,
                    "additional_self":  0,
                    "boosts":           0,
                    "manual_rating":    0,
                })

                hist["additional_chat"] += int(v.get("additional_chat", 0))
                hist["additional_neri"] += int(v.get("additional_neri", 0))
                hist["additional_self"] += int(v.get("additional_self", 0))
                hist["boosts"]          += int(v.get("boosts", 0))
                hist["manual_rating"]   += int(v.get("manual_rating", 0))

                for rid_str, entry in v.get("reactor_counts", {}).items():
                    rid = int(rid_str)
                    if isinstance(entry, dict):
                        count = int(entry.get("count", 0))
                        value = int(entry.get("value", 0))
                    else:
                        count = int(entry)
                        value = 0

                    rc_hist = hist["reactor_counts"].setdefault(rid, {"count": 0, "value": 0})
                    rc_hist["count"] += count
                    rc_hist["value"] += value

    @classmethod
    def load_social_rating(cls):
        try:
            raw = json.loads(open(SOCIAL_RATING_FILE, encoding="utf-8").read())
            cls.social_rating = {}

            for uid_str, v in raw.items():
                uid = int(uid_str)
                rc_new = {}
                for rid_str, item in v.get("reactor_counts", {}).items():
                    rid = int(rid_str)

                    if isinstance(item, dict):
                        count = int(item.get("count", 0))
                        value = int(item.get("value", 0))
                    else:
                        count = int(item)
                        value = 0

                    rc_new[rid] = {
                        "count":       count,
                        "value":       value,
                    }

                cls.social_rating[uid] = {
                    "reactor_counts": rc_new,
                    "banned":          bool(v.get("banned", False)),
                    "additional_chat": int(v.get("additional_chat", 0)),
                    "additional_neri": int(v.get("additional_neri", 0)),
                    "additional_self": int(v.get("additional_self", 0)),
                    "boosts":          int(v.get("boosts", 0)),
                    "manual_rating":   int(v.get("manual_rating", 0)),
                    "reactor_dates":   v.get("reactor_dates", []),
                }

        except (FileNotFoundError, json.JSONDecodeError):
            cls.social_rating = {}

    @classmethod
    def save_social_rating(cls):
        dump = {
            str(uid): {
                "reactor_counts": {
                    str(rid): {
                        "count":         info_rc["count"],
                        "value":         info_rc["value"],
                    }
                    for rid, info_rc in info["reactor_counts"].items()
                },
                "banned":           info.get("banned", False),
                "total_reacts":     info.get("total_reacts", 0),
                "additional_chat":  info.get("additional_chat", 0),
                "additional_neri":  info.get("additional_neri", 0),
                "additional_self":  info.get("additional_self", 0),
                "boosts":           info.get("boosts", 0),
                "manual_rating":    info.get("manual_rating", 0),
                "reactor_dates":    info.get("reactor_dates", []),
            }
            for uid, info in cls.social_rating.items()
        }
        with open(SOCIAL_RATING_FILE, "w", encoding="utf-8") as f:
            json.dump(dump, f, ensure_ascii=False, indent=2)

    @classmethod
    def load_stats(cls):
        try:
            with open(STATS_FILE, "r", encoding="utf-8") as f:
                raw = json.load(f)
                cls.message_stats = {int(k): int(v) for k, v in raw.items()}
        except (FileNotFoundError, json.JSONDecodeError):
            cls.message_stats = {}

        today = datetime.now(TYUMEN).date()
        today_path = daily_path_for_(today)
        if today_path.exists():
            try:
                with open(today_path, "r", encoding="utf-8") as f:
                    raw = json.load(f)
                    cls.daily_stats = {int(k): int(v) for k, v in raw.items()}
            except (json.JSONDecodeError, IOError):
                cls.daily_stats = {}
        else:
            cls.daily_stats = {}

    @classmethod
    def save_stats(cls):
        with open(STATS_FILE, "w", encoding="utf-8") as f:
            json.dump(cls.message_stats, f, ensure_ascii=False, indent=2)

    @classmethod
    def save_daily_stats(cls):
        today = datetime.now(TYUMEN).date()
        path = daily_path_for_(today)
        path.write_text(
            json.dumps(cls.daily_stats, ensure_ascii=False, indent=2)
        )

    @classmethod
    def load_last_sizes(cls):
        try:
            with open(LAST_SIZES_FILE, "r", encoding="utf-8") as f:
                data = json.load(f)
                cls.last_sizes = {
                    int(uid): {"size": float(v["size"]), "ts": v["ts"]}
                    for uid, v in data.items()
                }
        except (FileNotFoundError, json.JSONDecodeError):
            cls.last_sizes = {}

    @classmethod
    def save_last_sizes(cls):
        to_dump = {
            str(uid): {"size": info["size"], "ts": info["ts"]}
            for uid, info in cls.last_sizes.items()
        }
        with open(LAST_SIZES_FILE, "w", encoding="utf-8") as f:
            json.dump(to_dump, f, ensure_ascii=False, indent=2)

    @classmethod
    def load_banlist(cls):
        with open(BANLIST_FILE, "r", encoding="utf-8") as f:
            cls.banlist = json.load(f)

    @classmethod
    def save_banlist(cls):
        with open(BANLIST_FILE, "w", encoding="utf-8") as f:
            json.dump(cls.banlist, f, ensure_ascii=False, indent=2)

    @classmethod
    def save_forward_map(cls):
        serializable = {}
        for (orig_id, msg_id), entry in cls.forward_map.items():
            key = f"{orig_id}:{msg_id}"

            serializable[key] = {
                "text": entry.get("text", ""),
                "has_media": entry.get("has_media", False),
                "forwards": entry.get("forwards", []),
                "timestamp": entry.get("timestamp", "")
            }

        with open(FORWARD_MAP_FILE, "w", encoding="utf-8") as f:
            json.dump(serializable, f, ensure_ascii=False, indent=2)

    @classmethod
    def load_forward_map(cls):
        cls.forward_map.clear()

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
                cls.forward_map[(orig_id, msg_id)] = {
                    "text": "Это сообщение слишком старое..",
                    "has_media": False,
                    "timestamp": "",
                    "forwards": [
                        (int(c), int(m), False) for c, m in value
                    ]
                }

            elif isinstance(value, dict):
                cls.forward_map[(orig_id, msg_id)] = {
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

    @classmethod
    def save_banwords(cls):
        with open(BANWORDS_FILE, "w") as f:
            json.dump(list(cls.BANWORDS), f)

    def load_subscribers():
        try:
            with open(SUBSCRIBERS_FILE, "r") as f:
                return set(json.load(f))
        except (IOError, ValueError):
            return set()

    def save_subscribers(subs: set):
        with open(SUBSCRIBERS_FILE, "w") as f:
            json.dump(list(subs), f)

    @classmethod
    def save_meta_info(cls):
        first_dt = cls.META_INFO.get("first_message_time", datetime.now())
        last_dt  = cls.META_INFO.get("last_message_time",  datetime.now())
        join_dt  = cls.META_INFO.get("join_bot_time",  datetime.now())

        serializable = {
            "afk_time": cls.META_INFO.get("afk_time", 0),
            "alive_time": cls.META_INFO.get("alive_time", 0),
            "first_message_time": first_dt.timestamp(),
            "last_message_time":  last_dt.timestamp(),
            "join_bot_time":  join_dt.timestamp(),
        }

        with open(META_FILE, "w", encoding="utf-8") as f:
            json.dump(serializable, f, ensure_ascii=False, indent=2)
    
    def upgrade_users_table():
        cur = db.cursor()

        cur.execute("PRAGMA table_info('user');")
        existing = {row[1] for row in cur.fetchall()}

        if "alias" not in existing:
            cur.execute("ALTER TABLE user ADD COLUMN alias TEXT;")

        if "note" not in existing:
            cur.execute("ALTER TABLE user ADD COLUMN note TEXT;")

        db.commit()

    def ensure_helpers_table():
        with db:
            db.execute("""
                CREATE TABLE IF NOT EXISTS helper (
                    id INTEGER PRIMARY KEY
                )
            """)
    
    def ensure_slot_rolls_table():
        with db:
            db.execute("""
                CREATE TABLE IF NOT EXISTS slot_rolls (
                    user_id INTEGER NOT NULL,
                    ts       INTEGER NOT NULL
                )
            """)
            db.execute("""
                CREATE INDEX IF NOT EXISTS idx_slot_rolls_user
                ON slot_rolls(user_id)
            """)
    
    def ensure_white_bot_table():
        with db:
            db.execute("""
                CREATE TABLE IF NOT EXISTS white_bot (
                    name TEXT PRIMARY KEY
                )
            """)
    
    def ensure_white_msg_table():
        with db:
            db.execute("""
                CREATE TABLE IF NOT EXISTS white_msg (
                    msg_id INTEGER NOT NULL,
                    ts     INTEGER NOT NULL
                )
            """)
            db.execute("""
                CREATE INDEX IF NOT EXISTS idx_white_msg_id
                ON white_msg(msg_id)
            """)
    
    def ensure_random_deposit_table():
        with db:
            db.execute("""
                CREATE TABLE IF NOT EXISTS random_deposit (
                    ts       INTEGER NOT NULL
                )
            """)