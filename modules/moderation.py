from .utils import *

from telegram import (
    Update,
)
from telegram.ext import (
    ContextTypes,
)

def check_banwords(text):
    norm = normalize(text)
    #print("norm: ", norm)
    for pat in patterns_:
        if pat.search(norm):
            return True
    return False

def add_ban_rule(sig: dict):
    rule = {k: v for k, v in sig.items() if v is not None}
    banlist.append(rule)
    save_banlist()

async def is_banned_media(sig: dict, file_id, bot):
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