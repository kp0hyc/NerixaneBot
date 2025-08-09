import html
import math

from .config import MyBotState
from .utils import *

from telegram import InlineQueryResultArticle, InputTextMessageContent, Update
#from telegram.error import TelegramError
from telegram.ext import (
    CallbackContext,
)

RANK_TRACKS = {
    "neri": {
        "field": "soc_cur_neri",
        "buckets": [
            {"title": "Опущенный",      "min": float("-inf"), "max": -1,   "weight": 9999},
            {"title": "Латентный симп", "min": 0,   "max": 99,  "weight": 500},
            {"title": "Симп",           "min": 100, "max": 299, "weight": 800},
            {"title": "Гига-симп",      "min": 300, "max": 999, "weight": 1100},
            {"title": "Архисимп",       "min": 1000,"max": float("inf"), "weight": 9999},
        ],
    },

    "social": {
        "field": "soc_cur_tot",
        "buckets": [
            {"title": "Изгой",        "min": float("-inf"), "max": -1,   "weight": 5000},
            {"title": "Новичок",  "min": 0,   "max": 99,  "weight": 450},
            {"title": "Свой парень",  "min": 100, "max": 499, "weight": 750},
            {"title": "Авторитет",    "min": 500, "max": 999, "weight": 1000},
            {"title": "Икона чата",   "min": 1000,"max": float("inf"), "weight": 5000},
        ],
    },

    "msgs": {
        "field": "total_msgs",
        "buckets": [
            {"title": "Скорлупа",        "min": 0,   "max": 49,   "weight": 1000},
            {"title": "Наблюдатель",     "min": 50,  "max": 199,  "weight": 400},
            {"title": "Труженик чата",   "min": 200, "max": 499,  "weight": 650},
            {"title": "Почётный спамер", "min": 500, "max": 4999,  "weight": 900},
            {"title": "Гигасрун",        "min": 5000,"max": float("inf"), "weight": 1000},
        ],
    },

    "coins": {
        "field": "coins",
        "buckets": [
            {"title": "Бомжара должник",      "min": float("-inf"),   "max": -1,    "weight": 8000},
            {"title": "Дрочер копеек",        "min": 0,   "max": 99,    "weight": 550},
            {"title": "Копатель сокровищ",    "min": 100,  "max": 999,   "weight": 700},
            {"title": "Коллекционер рыженки", "min": 1000,  "max": 9999,  "weight": 950},
            {"title": "Вор казино",           "min": 10000, "max": float("inf"), "weight": 8000},
        ],
    },
}

ALIASES = [
            "чмо",
            "клоун",
            "Какамал",
            "мочехлёб",
            "ЧЕРЕПАХА-ТЕРПИЛА",
            "Данжен-Мастер",
            "МЧС",
            "Малютка",
            "СН0РЛАКС",
            "ЗВЕН0",
            "ЗЕРН0",
            "СИМП",
            "симпотяга",
            "половой психопат",
            "Программист-анальник",
            "Секстерминатор",
            "гигасимп",
            "брат рыжепальди",
            "КВАС0ЁБ",
            "Анальный Верзила",
            "брат Фиттипальди (живой)",
            "брат Фиттипальди (тот, что умер)",
            "Ефим Шефрим",
            "Анатолий Курпатов",
            "В0Д0ЛАЗ-ПУК0ВДЫХ",
]

def _pick_bucket(value: int | float, buckets: list[dict]) -> tuple[int, dict]:
    for i, b in enumerate(buckets):
        if b["min"] <= value <= b["max"]:
            return i, b
    return None, None

def pick_rank(info: dict) -> str:
    best_title = "??"
    best_weight = float("-inf")

    for _, track in RANK_TRACKS.items():
        value = info.get(track["field"], 0)
        _, bucket = _pick_bucket(value, track["buckets"])
        if bucket is None:
            continue
        w = float(bucket.get("weight", 0.0))
        if math.isnan(w):
            w = float("-inf")

        if (w > best_weight):
            best_weight = w
            best_title = bucket["title"]

    return best_title

def pick_alias() -> str:
    return random.choice(ALIASES)

async def inline_query(update: Update, context: CallbackContext) -> None:
    q = update.inline_query.query.strip().lower()
    results: list[InlineQueryResultArticle] = []

    for uid, info in MyBotState.indexed_users.items():
        if q and q not in info["name"].lower():
            continue

        alias = info.get("alias", "") or "???"
        note  = info.get("note",  "")
        rank_title = pick_rank(info)

        alias_esc       = html.escape(alias)
        note_esc        = html.escape(note)
        name_esc        = html.escape(info["name"])
        rank_title_esc  = html.escape(rank_title)

        dossier = (
            f"🗂 <b>Досье на {alias_esc}</b>\n"
            f"👤 {name_esc}  |  <i>{rank_title_esc}</i>\n"
            f"📨 <b>Сообщений:</b> {info['total_msgs']}  "
            f"(сегодня — {info['daily_msgs']})\n"
            f"❤️ <b>Текущий соц. рейтинг:</b> {info['soc_cur_tot']} "
            f"(из них нализал — {info['soc_cur_neri']})\n"
            f"🌍 <b>Весь рейтинг:</b> {info['soc_glob_tot']}\n"
            f"🪙 <b>Рыженки запрятал:</b> {info['coins']}\n"
            f"🚪 <b>Позорных выходов из беседы:</b> {info['leave_count']}\n"
            f"📅 <b>В чате уже:</b> {info['days_in_chat']} дн.\n"
            f"⏳ <b>С последнего входа прошло:</b> {info['days_since_join']} дн.\n\n"
            f"{note_esc}"
        )

        results.append(
            InlineQueryResultArticle(
                id=str(uid),
                title=f"{info['name']} — {alias}",
                description=f"{rank_title} • 💬 {info['total_msgs']} • 🪙 {info['coins']}",
                input_message_content=InputTextMessageContent(
                    message_text=dossier,
                    parse_mode="HTML",
                    disable_web_page_preview=True,
                ),
            )
        )
    
    results = results[:50]

    await update.inline_query.answer(
        results,
        cache_time=1,
        is_personal=True,
    )

async def index_users(ctx: CallbackContext) -> None:
    with db:
        rows = db.execute(
            "SELECT id, coins, alias, note, left_cnt, chat_joined FROM user"
        ).fetchall()

    coins_by_id =       {r["id"]: r["coins"]       for r in rows}
    alias_by_id =       {r["id"]: r["alias"]       for r in rows}
    note_by_id  =       {r["id"]: r["note"]        for r in rows}
    leave_count_by_id =       {r["id"]: r["left_cnt"]    for r in rows}
    first_chat_joined_by_id = {r["id"]: r["chat_joined"] for r in rows}

    uids = (
        set(MyBotState.message_stats)      |
        set(MyBotState.daily_stats)        |
        set(MyBotState.social_rating)      |
        set(MyBotState.old_social_rating)  |
        set(coins_by_id)
    ) - {TARGET_USER}

    MyBotState.indexed_users.clear()

    for uid in uids:
        try:
            first_join_dt = to_dt(first_chat_joined_by_id.get(uid))
            last_join_dt  = to_dt(await get_join_date(ORIG_CHANNEL_ID, uid))

            if last_join_dt is None:
                continue

            if first_join_dt is None:
                first_join_dt = last_join_dt
                with db:
                    db.execute("UPDATE user SET chat_joined = ? WHERE id = ?", (int(first_join_dt.timestamp()), uid))

            member = await ctx.bot.get_chat_member(ORIG_CHANNEL_ID, uid)
            name   = parse_name(member.user)

            total_msgs = MyBotState.message_stats.get(uid, 0)
            daily_msgs = MyBotState.daily_stats.get(uid, 0)

            cur_tot  = count_total_rating(MyBotState.social_rating,     uid)
            cur_neri = count_neri_rating (MyBotState.social_rating,     uid)
            old_tot  = count_total_rating(MyBotState.old_social_rating, uid)
            old_neri = count_neri_rating (MyBotState.old_social_rating, uid)

            alias = alias_by_id.get(uid) or ""
            note  = note_by_id.get(uid)  or ""
            leave_count = leave_count_by_id.get(uid, 0)

            if not alias:
                alias = pick_alias()
                note = ""

                with db:
                    db.execute(
                        "UPDATE user SET alias = ? WHERE id = ?",
                        (alias, uid)
                    )

            MyBotState.indexed_users[uid] = {
                "name"          : name,
                "total_msgs"    : total_msgs,
                "daily_msgs"    : daily_msgs,
                "soc_cur_tot"   : cur_tot,
                "soc_cur_neri"  : cur_neri,
                "soc_glob_tot"  : cur_tot  + old_tot,
                "soc_glob_neri" : cur_neri + old_neri,
                "coins"         : coins_by_id.get(uid, 0),
                "alias"         : alias,
                "note"          : note,
                "leave_count"   : leave_count,
                "days_in_chat"  : (datetime.now(TYUMEN) - first_join_dt).days,
                "days_since_join": (datetime.now(TYUMEN) - last_join_dt).days,
            }

        except Exception:
            continue

    print(f"✅ Indexed {len(MyBotState.indexed_users)} users.")