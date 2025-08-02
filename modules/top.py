import html

from .config import *
from .utils import *

from telegram import (
    constants,
    Update,
)
from telegram.ext import (
    ContextTypes,
)

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

    await subscribe_flow_(
        user.id,
        send_dm=send_dm,
        reply_in_chat=reply_in_chat,
    )