import asyncio
import re

from .moderation import *
from .config import MyBotState

from telegram.error import BadRequest, Forbidden, TimedOut
from telegram import (
    constants,
    ChatPermissions,
)

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
        
        block, ban_type = await is_banned_media(sig, file_id, context.bot)
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
            
            try:
                member = await context.bot.get_chat_member(update.effective_chat.id, user.id)
                join_date = member.joined_date
                print(f"User joined: {join_date}")
            except Exception as e:
                print(f"Couldn't fetch join date for user {user.id}: {e}")
                join_date = None

            now = datetime.now(TYUMEN)
            force_soft = False

            if join_date:
                one_month_ago = now - timedelta(days=30)
                if join_date < one_month_ago:
                    print(f"User {user.id} joined over a month ago — downgrading to soft.")
                    force_soft = True

            if ban_type == "block":
                try:
                    await context.bot.restrict_chat_member(
                        chat_id=update.effective_chat.id,
                        user_id=user.id,
                        permissions=ChatPermissions(
                            can_send_messages=True,
                            can_send_other_messages=False,
                            can_add_web_page_previews=False,
                            can_send_documents=False,
                            can_send_photos=False,
                            can_send_videos=False,
                            can_send_video_notes=False,
                        )
                    )
                except BadRequest as e:
                    print(f"Failed to restrict media for {user.id}: {e}")
            elif ban_type == "ban" and not force_soft:
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
            if user_id in MyBotState.SUBSCRIBERS:
                await update_all_messages(context.bot, user_id)
    
    print(update)

    MyBotState.message_stats[user.id] = MyBotState.message_stats.get(user.id, 0) + 1
    MyBotState.daily_stats[user.id] = MyBotState.daily_stats.get(user.id, 0) + 1
    
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
                MyBotState.last_sizes[user.id] = {"size": size, "ts": ts}
                MyBotState.save_last_sizes()

    if not user.id in MyBotState.social_rating:
        MyBotState.social_rating[user.id] = {
            "reactor_counts": {},
            "total_reacts":    0,
            "additional_chat": 0,
            "additional_neri": 0,
            "additional_self": 0,
            "boosts":          0,
            "manual_rating":   0,
        }
        MyBotState.save_social_rating()

    bc = getattr(msg, "sender_boost_count", None)
    if bc is None and hasattr(msg, "api_kwargs"):
        bc = msg.api_kwargs.get("sender_boost_count", 0)
    boost_count = int(bc or 0)
    if MyBotState.social_rating[user.id]["boosts"] != boost_count:
        MyBotState.social_rating[user.id]["boosts"] = boost_count
        MyBotState.save_social_rating()
    
    await check_afk_time(context.bot, user, update.effective_chat.id)

    #check if message is a reply
    if msg.reply_to_message:
        reply = msg.reply_to_message
        #check if reply is via bot
        if reply.via_bot:
            print("Whitelist msg that was replied")
            with db:
                db.execute(
                    "INSERT OR IGNORE INTO white_msg (msg_id, ts) VALUES (?, ?)",
                    (reply.id, datetime.now(TYUMEN))
                )
    
    print(f"Before dice and via bot check")
    if (msg.dice or msg.via_bot) and user.id != TARGET_USER:
        print("Dice or via bot detected")
        skip = False
        if msg.dice and msg.dice.emoji == "🎰":
            res = msg.dice.value - 1
            v0 = (res >> 4) & 0b11
            v1 = (res >> 2) & 0b11
            v2 = res         & 0b11

            if v0 == v1 == v2:
                print("Dice triple detected, skip deletion")
                skip = True

        if msg.via_bot:
            skip = is_white_bot(msg.via_bot.username)

        print(f"Skip deletion: {skip}")

        if not skip:
            asyncio.create_task(
                delete_message_later_and_check(msg, 300)
            )

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

    MyBotState.save_forward_map()
    
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

async def broadcast(orig_chat_id, orig_msg_id, text, has_media, bot):
    kb = await make_link_keyboard(orig_chat_id, orig_msg_id, bot)
    join_chat = await make_chat_invite_keyboard()

    if (orig_chat_id, orig_msg_id) not in MyBotState.forward_map:
        MyBotState.forward_map[(orig_chat_id, orig_msg_id)] = {
            "text": text,
            "has_media": has_media,
            "forwards": [],
            "timestamp": datetime.utcnow().isoformat()
        }

    mention_list = []
    for subscriber_id in list(MyBotState.SUBSCRIBERS):
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
                MyBotState.forward_map[(orig_chat_id, orig_msg_id)]["forwards"].append((subscriber_id, fwd.message_id, False))
                continue

            fwd = await bot.copy_message(
                chat_id=subscriber_id,
                from_chat_id=orig_chat_id,
                message_id=orig_msg_id,
                reply_markup=kb
            )
            MyBotState.forward_map[(orig_chat_id, orig_msg_id)]["forwards"].append((subscriber_id, fwd.message_id, True))

        except Forbidden:
            MyBotState.SUBSCRIBERS.remove(subscriber_id)
            BotState.save_subscribers(MyBotState.SUBSCRIBERS)
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

async def update_all_messages(bot, user_id):
    now = datetime.utcnow()

    for (orig_id, orig_msg), entry in MyBotState.forward_map.items():
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
    entry = MyBotState.forward_map.get(key)
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

async def delete_forwards(bot, orig_chat, orig_msg):
    key = (orig_chat, orig_msg)
    entry = MyBotState.forward_map.get(key)

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

    MyBotState.forward_map.pop(key, None)

async def handle_gambling(update: Update, context: ContextTypes.DEFAULT_TYPE):
    print("handle_gambling")
    user = update.effective_user
    msg = update.message
    if not msg:
        return
    
    if not user:
        return
    
    #check if message via bot or a dice
    if not msg.dice and not msg.via_bot:
        await msg.delete()


async def on_chat_member(update, context):
    print(f"Chat member update: {update.chat_member}")

    if update.chat_member.chat.id != ORIG_CHANNEL_ID:
        return

    old = update.chat_member.old_chat_member
    new = update.chat_member.new_chat_member

    left = old.status in ("member","administrator","creator") and new.status in ("left","kicked")
    joined = old.status in ("left","kicked") and new.status in ("member","administrator","creator")

    if joined:
        print(f"User {new.user.id} joined the chat")
    elif left:
        print(f"User {new.user.id} left the chat")

        with db:
            db.execute("""
                UPDATE user
                SET left_cnt = COALESCE(left_cnt, 0) + 1
                WHERE id = ?
            """, (new.user.id,))