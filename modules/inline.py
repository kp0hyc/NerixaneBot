import uuid

from .config import MyBotState
from .utils import *

from telegram import InlineQueryResultArticle, InputTextMessageContent, Update
#from telegram.error import TelegramError
from telegram.ext import (
    CallbackContext,
)

RANKS: list[dict] = [
    {
        "title": "Опущенный",
        "min": float("-inf"),  # всё, что ниже 0
        "max": -1,
        "aliases": [
            "чмо",
            "клоун",
            "Какамал",
            "мочехлёб",
            "ЧЕРЕПАХА-ТЕРПИЛА",
        ],
    },
    {
        "title": "Латентный симп",
        "min": 0,
        "max": 99,
        "aliases": [
            "Данжен-Мастер",
            "МЧС",
            "Малютка",
            "СН0РЛАКС",
            "ЗВЕН0",
            "ЗЕРН0",
        ],
    },
    {
        "title": "Симп",
        "min": 100,
        "max": 299,
        "aliases": [
            "СИМП",
            "симпотяга",
            "половой психопат",
            "Программист-анальник",
            "Секстерминатор",
        ],
    },
    {
        "title": "Гига-симп",
        "min": 300,
        "max": 999,
        "aliases": [
            "гигасимп",
            "брат рыжепальди",
            "рыжепопик",
            "КВАС0ЁБ",
            "Анальный Верзила",
        ],
    },
    {
        "title": "Легенда чата",
        "min": 1000,
        "max": float("inf"),
        "aliases": [
            "брат Фиттипальди (живой)",
            "брат Фиттипальди (тот, что умер)",
            "Ефим Шефрим",
            "Анатолий Курпатов",
            "В0Д0ЛАЗ-ПУК0ВДЫХ",
        ],
    },
]

def pick_rank_alias(score: int) -> tuple[str, str]:
    for bucket in RANKS:
        if bucket["min"] <= score <= bucket["max"]:
            return bucket["title"], random.choice(bucket["aliases"])
    return "??", "Безымянный"

async def inline_query(update: Update, context: CallbackContext) -> None:
    q = update.inline_query.query.strip().lower()
    results: list[InlineQueryResultArticle] = []

    for uid, info in MyBotState.indexed_users.items():
        if q and q not in info["name"].lower():
            continue

        alias = info.get("alias", "") or "???"
        note  = info.get("note",  "")
        rank_title, _ = pick_rank_alias(info["soc_cur_tot"])

        dossier = (
            f"🗂 **Досье на агента {alias}**\n"
            f"👤 {info['name']}  |  _{rank_title}_\n"
            f"📨 **Сообщений:** {info['total_msgs']}  "
              f"(сегодня — {info['daily_msgs']})\n"
            f"❤️ **Текущий соц. рейтинг:** {info['soc_cur_tot']} "
              f"(из них нализал — {info['soc_cur_neri']})\n"
            f"🌍 **Весь рейтинг:** {info['soc_glob_tot']}\n"
            f"🪙 **Рыженки запрятал:** {info['coins']}\n\n"
            f"{note}"
        )

        results.append(
            InlineQueryResultArticle(
                id=str(uid),
                title=f"{info['name']} — {alias}",
                description=f"{rank_title} • 💬 {info['total_msgs']} • 🪙 {info['coins']}",
                input_message_content=InputTextMessageContent(
                    message_text=dossier,
                    parse_mode="Markdown",
                    disable_web_page_preview=True,
                ),
            )
        )

    await update.inline_query.answer(
        results,
        cache_time=1,
        is_personal=True,
    )

async def index_users(ctx: CallbackContext) -> None:
    with db:
        rows = db.execute(
            "SELECT id, coins, alias, note FROM user"
        ).fetchall()

    coins_by_id =  {r["id"]: r["coins"] for r in rows}
    alias_by_id =  {r["id"]: r["alias"]  for r in rows}
    note_by_id  =  {r["id"]: r["note"]   for r in rows}

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

            if not alias:
                _, alias = pick_rank_alias(cur_tot)
                note = f""

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
            }

        except Exception:
            continue

    print(f"✅ Indexed {len(MyBotState.indexed_users)} users.")