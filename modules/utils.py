import asyncio
import hashlib
import random

from .bot_state import *
from .config import MyBotState

from telethon.tl.functions.channels import GetParticipantRequest
from typing import Callable, Awaitable

from html import escape

from telegram import (
    InlineKeyboardButton,
    InlineKeyboardMarkup,
)
from telegram.error import Forbidden

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

async def compute_sha256(bot, file_id):
    bio = await bot.get_file(file_id)
    data = await bio.download_as_bytearray()
    return hashlib.sha256(data).hexdigest()

def normalize(text):
    return text.translate(HOMOGLYPHS).lower()

def parse_name(uc):
    if uc.id == TARGET_USER:
        nick = random.choice(TARGET_NICKS)
        return f"👑<b>{escape(nick)}</b>"
    if uc.first_name or uc.last_name:
        return escape(" ".join(filter(None, [uc.first_name, uc.last_name])))
    if uc.username:
        return escape(f"@{uc.username}")
    return escape(str(uc.id))

def parse_mention(user):
    full_name = parse_name(user)
    return f'<a href="tg://user?id={user.id}">{full_name}</a>'

def count_total_rating(sr, uid):
    if uid not in sr:
        return 0
    info = sr[uid]

    total = (
        info.get("additional_chat", 0)
        + info.get("additional_neri", 0) * 15
        + info.get("boosts", 0) * 5
        + info.get("manual_rating", 0)
    )

    total += sum(
        entry.get("value", 0)
        for reactor_id, entry in info.get("reactor_counts", {}).items()
        if not sr.get(reactor_id, {}).get("banned", False)
    )

    return total

def count_neri_rating(sr, uid):
    if uid not in sr:
        return 0
    info = sr[uid]
    return info.get("additional_neri", 0) * 15 + info.get("boosts", 0) * 5 + info.get("manual_rating", 0)

def format_duration(delta: timedelta) -> str:
    total = int(delta.total_seconds())
    hrs, rem = divmod(total, 3600)
    mins, secs = divmod(rem, 60)
    parts = []
    if hrs:  parts.append(f"{hrs} ч")
    if mins: parts.append(f"{mins} мин")
    if secs or not parts: parts.append(f"{secs} сек")
    return " ".join(parts)

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

async def check_afk_time(bot, user, chat_id):
    now = datetime.now()

    last_time = MyBotState.META_INFO.get("last_message_time", now)
    first_time = MyBotState.META_INFO.get("first_message_time", last_time)

    delta_dead = now - last_time
    delta_alive = last_time - first_time

    prev_dead_secs = MyBotState.META_INFO.get("afk_time", 0)
    prev_dead_td = timedelta(seconds=prev_dead_secs)
    prev_alive_secs = MyBotState.META_INFO.get("alive_time", 0)
    prev_alive_td = timedelta(seconds=prev_alive_secs)

    print(f"AFK check: now={now}, last_time={last_time}, first_time={first_time}")
    print(f"AFK check: delta_dead={delta_dead}, delta_alive={delta_alive}")
    print(f"AFK check: prev_dead_td={prev_dead_td}, prev_alive_td={prev_alive_td}")

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
            MyBotState.META_INFO["afk_time"] = int(delta_dead.total_seconds())
        else:
            dead_info = f"⏱ Чат был мёртв {format_duration(delta_dead)}."

        if new_alive_record:
            alive_info = (
                f"🚀 Новый рекорд «жизни» чата! "
                f"Вы срали непрерывно {format_duration(delta_alive)} "
                f"(предыдущий рекорд — {format_duration(prev_alive_td)})."
            )
            MyBotState.META_INFO["alive_time"] = int(delta_alive.total_seconds())
        else:
            alive_info = f"Чат был жив {format_duration(delta_alive)}."

        reanim_info  = f"💉 Реанимационные мероприятия успешно провёл {reanimator}."
        text = "\n\n".join([dead_info, alive_info, reanim_info])
        await bot.send_message(chat_id, text, parse_mode="HTML")

        MyBotState.META_INFO["first_message_time"] = now

    MyBotState.META_INFO["last_message_time"] = now
    MyBotState.save_meta_info()

async def subscribe_flow_(
    user_id: int,
    *,
    send_dm: Callable[[str], Awaitable],
    reply_in_chat: Callable[[str], Awaitable],
):
    if user_id in MyBotState.SUBSCRIBERS:
        try:
            await send_dm("🎉 Всё настроено, сообщения будут приходить сюда!")
            await reply_in_chat("✅ Вы уже сталкерите Рыжопеча.")
        except Forbidden:
            await reply_in_chat(
                "✅ Я тебя записал, но не смогу отправить сообщение, пока ты не откроешь чат со мной. "
                "Пожалуйста отправь /start мне в ЛС."
            )
    else:
        MyBotState.SUBSCRIBERS.add(user_id)
        MyBotState.save_subscribers(MyBotState.SUBSCRIBERS)

        await reply_in_chat("🎉 Поздравляю, теперь ты сталкеришь Рыжопеча!")
        try:
            await send_dm("🎉 Всё настроено, сообщения будут приходить сюда!")
        except Forbidden:
            await reply_in_chat(
                "✅ Я тебя записал, но не смогу отправить сообщение, пока ты не откроешь чат со мной. "
                "Пожалуйста отправь /start мне в ЛС."
            )

def is_helper(user_id: int) -> bool:
    row = db.execute(
        "SELECT 1 FROM helper WHERE id = ?",
        (user_id,)
    ).fetchone()
    return row is not None

async def delete_messages_later(messages, delay: int):
    await asyncio.sleep(delay)
    for m in messages:
        try:
            await m.delete()
        except:
            pass

def check_group_owner(update):
    user = update.effective_user
    chat = update.effective_chat

    return user and chat and user.id == TARGET_USER and chat.id == ORIG_CHANNEL_ID
