from .utils import *
from.config import MyBotState

from telegram.ext import (
    ContextTypes,
)

async def persist_stats(context: ContextTypes.DEFAULT_TYPE):
    MyBotState.save_stats()
    MyBotState.save_daily_stats()
    clear_and_save_cocks()
    clear_old_messages()
    print("Message stats saved.")

def check_init_user_table():
    row = db.execute("SELECT COUNT(*) AS cnt FROM user").fetchone()
    if row["cnt"] == 0:
        for uid, info in MyBotState.social_rating.items():
            total = count_total_rating(MyBotState.social_rating, uid)
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
        print(f"Updating coins for {uid}: {current} → {new_balance}; change: {coins}")
        db.execute(
            "INSERT INTO user (id, coins) VALUES (?, ?) "
            "ON CONFLICT(id) DO UPDATE SET coins = excluded.coins",
            (uid, new_balance)
        )

async def reset_daily(context: ContextTypes.DEFAULT_TYPE):
    yesterday = (datetime.now(TYUMEN) - timedelta(days=1)).date()
    ypath = daily_path_for_(yesterday)
    if MyBotState.daily_stats:
        ypath.write_text(json.dumps(MyBotState.daily_stats, ensure_ascii=False, indent=2))
    MyBotState.daily_stats.clear()
    MyBotState.save_daily_stats()
    print(f"Rotated daily stats: {yesterday} → {ypath.name}")

def clear_and_save_cocks():
    cutoff = datetime.now() - timedelta(hours=24)

    to_delete = []
    for key, info in MyBotState.last_sizes.items():
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
        del MyBotState.last_sizes[key]
    MyBotState.save_last_sizes()

async def reset_monthly_social_rating(context: ContextTypes.DEFAULT_TYPE):
    now = datetime.now(TYUMEN)

    if now.day != 1:
        return

    prev = now.replace(day=1) - timedelta(days=2)
    month_name = prev.strftime("%B").lower()  # например 'july'
    archive_file = f"social_rating_{month_name}.json"

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
        for uid, info in MyBotState.social_rating.items()
    }

    with open(archive_file, "w", encoding="utf-8") as f:
        json.dump(dump, f, ensure_ascii=False, indent=2)

    MyBotState.social_rating.clear()
    MyBotState.save_social_rating()
    MyBotState.load_old_social_rating()

    print(f"[Monthly reset] Archived to {archive_file} and cleared current social_rating.")