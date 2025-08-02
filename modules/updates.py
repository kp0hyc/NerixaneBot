from .utils import *

from telegram.ext import (
    ContextTypes,
)

async def persist_stats(context: ContextTypes.DEFAULT_TYPE):
    save_stats()
    save_daily_stats()
    clear_and_save_cocks()
    print("Message stats saved.")

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

async def reset_daily(context: ContextTypes.DEFAULT_TYPE):
    yesterday = (datetime.now(TYUMEN) - timedelta(days=1)).date()
    ypath = daily_path_for_(yesterday)
    if daily_stats:
        ypath.write_text(json.dumps(daily_stats, ensure_ascii=False, indent=2))
    daily_stats.clear()
    save_daily_stats()
    print(f"Rotated daily stats: {yesterday} → {ypath.name}")

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