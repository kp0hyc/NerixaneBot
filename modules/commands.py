import sys

from .top import *
from .updates import *
from .casino import *
from .config import MyBotState

from telegram import (
    MessageEntity,
)

async def subscribe(update: Update, context: CallbackContext):
    user = update.effective_user
    if not user:
        return

    async def send_dm(text):
        await context.bot.send_message(chat_id=user.id, text=text)

    async def reply_in_chat(text):
        await update.message.reply_text(text)

    await subscribe_flow_(
        user.id,
        send_dm=send_dm,
        reply_in_chat=reply_in_chat,
    )

async def unsubscribe(update: Update, context: CallbackContext):
    user = update.effective_user
    if not user:
        return
    uid = user.id
    if uid in MyBotState.SUBSCRIBERS:
        MyBotState.SUBSCRIBERS.remove(uid)
        MyBotState.save_subscribers(MyBotState.SUBSCRIBERS)
        await update.message.reply_text("⚠️ Вы покинули клуб сталкеров Рыжопеча.")
    else:
        await update.message.reply_text("ℹ️ Так ты и не следил, чел...")

async def start(update: Update, context: CallbackContext):
    user = update.effective_user
    if not user:
        return
    await update.message.reply_text("ℹ️ Бот инициализирован!")

async def warn_use_dm(update: Update, context: CallbackContext):
    await update.message.reply_text(
        "🚫 Пожалуйста, отправь мне это в личные сообщения, а не сюда."
    )

async def top_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    text, kb = await build_stats_page_async("global", 0, context.bot)
    sent = await update.message.reply_text(
        text,
        parse_mode="HTML",
        reply_markup=kb
    )
    MyBotState.stats_sessions[sent.message_id] = update.effective_user.id


async def show_rating(update: Update, context: CallbackContext):
    return

async def shutdown_bot(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user = update.effective_user
    if not user or user.id not in MyBotState.MODERATORS:
        return

    await update.message.reply_text("🔌 Shutting down, saving stats…")

    MyBotState.save_stats()
    MyBotState.save_daily_stats()
    clear_and_save_cocks()
    MyBotState.save_meta_info()

    sys.exit(0)

async def edit_weights_cmd(update, context: ContextTypes.DEFAULT_TYPE):
    user = update.effective_user
    if not user or user.id not in MyBotState.MODERATORS:
        return

    dump = json.dumps(MyBotState.emoji_weights, ensure_ascii=False, indent=2)
    
    sent = await update.message.reply_text(dump)
    context.user_data["weights_msg_id"] = sent.message_id

async def edit_weights_reply(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user = update.effective_user
    msg  = update.effective_message

    if not user or user.id not in MyBotState.MODERATORS:
        return
    parent = msg.reply_to_message
    if not parent or parent.message_id != context.user_data.get("weights_msg_id"):
        return

    text = msg.text or ""
    m = re.match(r"\s*(.+?)\s*:\s*(\-?\d+)\s*$", text)
    if not m:
        return await msg.reply_text("ℹ️ Неверный формат. Напишите `emoji: число`.")

    key_raw, val_raw = m.group(1), m.group(2)
    ce = next(
        (e for e in (msg.entities or [])
         if e.type == MessageEntity.CUSTOM_EMOJI),
        None
    )
    if ce:
        key = f"<custom:{ce.custom_emoji_id}>"
    else:
        key = key_raw  

    try:
        weight = int(val_raw)
    except ValueError:
        return await msg.reply_text("ℹ️ Вторая часть должна быть числом.")

    # update & save
    MyBotState.emoji_weights[key] = weight
    MyBotState.save_emoji_weights()

    await msg.reply_text(f"✅ Обновлено: {key} → {weight}")

async def ban_sc_user(update: Update, context: CallbackContext):
    user = update.effective_user
    msg  = update.effective_message

    if not user or user.id not in MyBotState.MODERATORS:
        return
    
    if not context.args:
        await msg.reply_text("Использование: /ban_sc_user uid")
        return
    
    try:
        target_id = int(context.args[0])
    except ValueError:
        await msg.reply_text("❌ Вторым аргументом должно быть число, например /ban_sc_user 123456789")
        return

    try:
        user_chat = await context.bot.get_chat(target_id)
        name = parse_name(user_chat)
    except Exception:
        name = f"[ID {target_id}]"
    if target_id not in MyBotState.social_rating:
        await msg.reply_text(f"ℹ️ Пользователь {name} не найден в соц. рейтинге.")
        return
    
    MyBotState.social_rating[target_id]["banned"] = True
    MyBotState.save_social_rating()

    await msg.reply_text(f"✅ Пользователь {name} заблокирован в соц. рейтинге.")

async def unban_sc_user(update: Update, context: CallbackContext):
    user = update.effective_user
    msg  = update.effective_message

    if not user or user.id not in MyBotState.MODERATORS:
        return
    
    if not context.args:
        await msg.reply_text("Использование: /unban_sc_user uid")
        return
    
    try:
        target_id = int(context.args[0])
    except ValueError:
        await msg.reply_text("❌ Вторым аргументом должно быть число, например /unban_sc_user 123456789")
        return

    try:
        user_chat = await context.bot.get_chat(target_id)
        name = parse_name(user_chat)
    except Exception:
        name = f"[ID {target_id}]"
    if target_id not in MyBotState.social_rating:
        await msg.reply_text(f"ℹ️ Пользователь {name} не найден в соц. рейтинге.")
        return
    
    MyBotState.social_rating[target_id]["banned"] = False
    MyBotState.save_social_rating()

    await msg.reply_text(f"✅ Пользователь {name} разблокирован в соц. рейтинге.")

async def change_social_rating(update: Update, context: CallbackContext):
    if not check_group_owner(update):
        return

    reply = update.message.reply_to_message
    if not reply:
        await update.message.reply_text(
            "Ты должна ответить на сообщение нужного чатера\n"
            "/sc <+N или -N> [причина]\n"
            "Например (ответом): /sc +3 Спасибки за вклад!"
        )
        return

    target = reply.from_user
    target_id = target.id
    display_name = target.username and f"@{target.username}" or target.full_name or str(target_id)

    if not context.args:
        await update.message.reply_text("❌ Ты должна указать +N или -N, например /sc +2")
        return

    diff_str = context.args[0]
    try:
        diff = int(diff_str)
    except ValueError:
        await update.message.reply_text("❌ Вторым аргументом должно быть число, например +5 или -2.")
        return

    if target_id not in MyBotState.social_rating:
        MyBotState.social_rating[target_id] = {
            "additional_chat": 0,
            "additional_neri": 0,
            "additional_self": 0,
            "boosts": 0,
            "manual_rating": 0,
        }

    old = MyBotState.social_rating[target_id]["manual_rating"]
    MyBotState.social_rating[target_id]["manual_rating"] = old + diff
    update_coins(target_id, diff)

    MyBotState.save_social_rating()
    
    word = "получил"
    social_rating_image = SOCIAL_ADD_RATING_IMAGE
    if diff < 0:
        word = "потерял"
        social_rating_image = SOCIAL_SUB_RATING_IMAGE

    caption = f"✅ {display_name} {word} {abs(diff)} социальных кредитов"
    if len(context.args) > 1:
        reason = " ".join(context.args[1:])
        caption += f"\nПричина: {reason}"

    try:
        with open(social_rating_image, "rb") as photo:
            await context.bot.send_photo(
                chat_id=update.effective_chat.id,
                photo=photo,
                caption=caption
            )
    except FileNotFoundError:
        await context.bot.send_message(
            chat_id=update.effective_chat.id,
            text=caption
        )

async def add_banword(update: Update, context: CallbackContext):
    user = update.effective_user
    msg  = update.effective_message

    if not user or user.id not in MyBotState.MODERATORS:
        return

    if not context.args:
        await msg.reply_text("Использование: /bw слово1 слово2 …")
        return

    new_words = {w.strip().lower() for w in context.args if w.strip()}

    added = new_words - MyBotState.BANWORDS
    MyBotState.BANWORDS.update(added)

    if added:
        await msg.reply_text(
            f"✅ Добавлено {len(added)} новые слова: " +
            ", ".join(sorted(added))
        )
    else:
        await msg.reply_text("Все эти слова уже в списке.")
    MyBotState.save_banwords()
    MyBotState.compile_patterns()

async def remove_banword(update: Update, context: CallbackContext):
    user = update.effective_user
    msg  = update.effective_message

    if not user or user.id not in MyBotState.MODERATORS:
        return

    if not context.args:
        await msg.reply_text("Использование: /remove_bw слово1 слово2 …")
        return

    requested = {w.strip().lower() for w in context.args if w.strip()}

    present    = requested & MyBotState.BANWORDS
    not_found  = requested - MyBotState.BANWORDS

    MyBotState.BANWORDS.difference_update(present)

    if present:
        await msg.reply_text(
            f"✅ Убрано {len(present)} слов(а): " + ", ".join(sorted(present)) +
            (f"\n⚠️ Не найдено: {', '.join(sorted(not_found))}"
             if not_found else "")
        )
    else:
        await msg.reply_text("Ни одно из указанных слов не было в списке бан-слов.")
    MyBotState.save_banwords()
    MyBotState.compile_patterns()

async def start_bet(update: Update, context: CallbackContext):
    print("starting bet")
    user = update.effective_user
    msg  = update.effective_message

    if not user or (user.id not in MyBotState.MODERATORS and not is_helper(user.id)):
        return

    text = msg.text or ""
    parts = text.split(" ", 1)
    if len(parts) < 2 or not parts[1].strip():
        return await msg.reply_text(
            "❌ Использование: /start_bet Вопрос? Опция A;Опция B;Опция C"
        )
    payload = parts[1].strip()

    try:
        question_part, opts_part = payload.split("?", 1)
        question = question_part.strip() + "?"
        options  = [o.strip() for o in opts_part.split(";") if o.strip()]
        if len(options) < 2:
            raise ValueError()
    except ValueError:
        return await msg.reply_text(
            "❌ Неверный формат. Использование:\n"
            "/start_bet Вопрос? Опция A;Опция B;Опция C"
        )

    # insert into your SQLite
    with db:
        cur = db.execute(
            "INSERT INTO poll (question) VALUES (?)",
            (question,)
        )
        poll_id = cur.lastrowid

        for idx, text in enumerate(options):
            db.execute(
                "INSERT INTO poll_option (poll_id, idx, option) VALUES (?,?,?)",
                (poll_id, idx, text)
            )
    
    text, kb, buf = create_bet_image_and_text(poll_id)
    
    sent = await context.bot.send_photo(
        chat_id=ORIG_CHANNEL_ID,
        photo=buf,
        caption=text,
        reply_markup=kb,
        parse_mode="Markdown"
    )
    
    with db:
        db.execute(
            "UPDATE poll SET chat_id=?, message_id=? WHERE id=?",
            (sent.chat_id, sent.message_id, poll_id)
        )

    # confirm to the moderator
    await msg.reply_text(f"✅ Опрос #{poll_id} создан.")

async def close_bet(update: Update, context: CallbackContext):
    user = update.effective_user
    msg  = update.effective_message

    if not user or (user.id not in MyBotState.MODERATORS and not is_helper(user.id)):
        return

    args = context.args or []
    if len(args) != 1 or not args[0].isdigit():
        return await msg.reply_text("❌ Использование: /close_bet <poll_id>")
    poll_id = int(args[0])

    # 1) Check status
    with db:
        poll_row = db.execute(
            "SELECT status FROM poll WHERE id = ?", (poll_id,)
        ).fetchone()
        if not poll_row:
            return await msg.reply_text(f"ℹ️ Опрос #{poll_id} не найден.")
        if poll_row["status"] != 0:
            return await msg.reply_text(f"ℹ️ Опрос #{poll_id} уже закрыт ранее.")

        # mark closed (no more new stakes)
        db.execute("UPDATE poll SET status = 1 WHERE id = ?", (poll_id,))
    await msg.reply_text(f"⏸ Опрос #{poll_id} закрыт. Новые ставки не принимаются.")

async def finish_bet(update: Update, context: CallbackContext):
    user = update.effective_user
    msg  = update.effective_message

    if not user or (user.id not in MyBotState.MODERATORS and not is_helper(user.id)):
        return

    args = context.args or []
    if len(args) != 2 or not all(arg.isdigit() for arg in args):
        return await msg.reply_text(
            "❌ Использование: /finish_bet <poll_id> <winning_option_idx>"
        )
    poll_id, win_idx = map(int, args)

    with db:
        poll_row = db.execute(
            "SELECT status, question FROM poll WHERE id = ?",
            (poll_id,)
        ).fetchone()
        if not poll_row:
            return await msg.reply_text(f"ℹ️ Опрос #{poll_id} не найден.")
        question = poll_row["question"]
        if poll_row["status"] == 2:
            return await msg.reply_text(f"ℹ️ Опрос #{poll_id} уже завершён ранее.")

        opt_row = db.execute(
            "SELECT option FROM poll_option WHERE poll_id = ? AND idx = ?",
            (poll_id, win_idx)
        ).fetchone()
        if not opt_row:
            return await msg.reply_text(
                f"❌ Вариант #{win_idx} не найден в опросе #{poll_id}."
            )
        winning_option = opt_row["option"]

        # 3) Загружаем все ставки
        bets = db.execute(
            "SELECT user_id, option_idx, amount FROM bets WHERE poll_id = ?",
            (poll_id,)
        ).fetchall()
        # Если ставок не было — сразу отмечаем опрос завершённым
        if not bets:
            db.execute(
                "UPDATE poll SET status = 2, winner_idx = ? WHERE id = ?",
                (win_idx, poll_id)
            )
            return await msg.reply_text(
                f"ℹ️ Опрос #{poll_id} завершён.\n"
                f"Вопрос: {question}\n"
                f"Выиграл вариант: «{winning_option}»\n"
                "Ставок не было."
            )

    # 4) Разбиваем на победителей и проигравших
    winners = [(r["user_id"], r["amount"]) for r in bets if r["option_idx"] == win_idx]
    losers  = [(r["user_id"], r["amount"]) for r in bets if r["option_idx"] != win_idx]
    total_win  = sum(a for _, a in winners)
    total_lose = sum(a for _, a in losers)

    # 5) Отмечаем опрос как завершённый и сохраняем индекс победителя
    with db:
        db.execute(
            "UPDATE poll SET status = 2, winner_idx = ? WHERE id = ?",
            (win_idx, poll_id)
        )

    # 6) Если никто не ставил на победивший вариант
    if total_win == 0:
        return await context.bot.send_message(
            chat_id=ORIG_CHANNEL_ID,
            text=(
                f"⚠️ Опрос #{poll_id} завершён.\n"
                f"Вопрос: {question}\n"
                f"Выиграл вариант: «{winning_option}», "
                "но на него никто не ставил.\n"
                f"Общие потери: {total_lose} рыженки."
            )
        )

    # 7) Начисляем выплаты
    payouts = {}
    for uid, stake in winners:
        share  = (stake / total_win) * total_lose
        payout = int(stake + share)
        update_coins(uid, payout)
        payouts[uid] = payout

    # 8) Собираем итоговое сообщение
    lines = [
        f"🏁 Опрос #{poll_id} завершён!",
        f"Вопрос: {question}",
        f"Выиграл вариант: «{winning_option}»",
        f"Всего ставок: {total_win + total_lose} рыженки "
        f"(на победу — {total_win}, на поражение — {total_lose})",
        "",
        "💰 Выплаты:"
    ]
    for uid, amt in payouts.items():
        try:
            # fetch the User/Chat object and format via parse_name
            user_chat = await context.bot.get_chat(uid)
            name = parse_name(user_chat)
        except Exception:
            # fallback in case of error
            name = f"[ID {uid}]"
        lines.append(f"• {name}: +{amt} рыженки")

    await context.bot.send_message(
        chat_id=ORIG_CHANNEL_ID,
        text="\n".join(lines),
        parse_mode=constants.ParseMode.HTML,
    )

async def slot_command(update: Update, context: CallbackContext):
    user = update.effective_user
    msg  = update.effective_message
    if not user:
        return

    if user.id == TARGET_USER and update.effective_chat.id != ORIG_CHANNEL_ID:
        await msg.delete()
        return

    #check chat id
    if update.effective_chat.id == ORIG_CHANNEL_ID and not user.id in MyBotState.MODERATORS:
        await msg.delete()
        return

    if not MyBotState.slot and not user.id in MyBotState.MODERATORS:
        now_ts = int(datetime.now(TYUMEN).timestamp())
        recent = db.execute(
            "SELECT ts FROM slot_rolls WHERE user_id = ? ORDER BY ts DESC LIMIT 5",
            (user.id,)
        ).fetchall()
        timestamps = [r["ts"] for r in recent]
        if len(timestamps) == 5:
            oldest = timestamps[-1]
            if now_ts - oldest < 30 * 60:
                next_avail_ts = oldest + 30 * 60
                next_dt = datetime.fromtimestamp(
                    next_avail_ts, tz=TYUMEN
                )
                time_str = next_dt.strftime("%Y-%m-%d %H:%M:%S")
                warning = await msg.reply_text(
                    f"❌ Лимит: 5 прокруток за 30 минут достигнут. "
                    f"Следующая попытка будет доступна {time_str}"
                )
                #asyncio.create_task(
                #    delete_messages_later([msg, warning], delay=5)
                #)
                return
        with db:
            db.execute(
                "INSERT INTO slot_rolls (user_id, ts) VALUES (?, ?)",
                (user.id, now_ts)
            )
            db.execute(
                """
                DELETE FROM slot_rolls
                WHERE user_id = ?
                AND ts NOT IN (
                    SELECT ts
                    FROM slot_rolls
                    WHERE user_id = ?
                    ORDER BY ts DESC
                    LIMIT 5
                )
                """,
                (user.id, user.id)
            )
    args = context.args or []
    action_text = None
    stake = 0
    if args:
        if args[0].isdigit():
            stake = int(args[0])
        else:
            action_text = " ".join(args)
    else:
        stake = 10

    if not action_text: #and stake > 0:
        row = db.execute(
            "SELECT coins FROM user WHERE id = ?",
            (user.id,)
        ).fetchone()
        balance = row["coins"] if row else 0
        if balance < stake:
            warning = await msg.reply_text(
                f"❌ Недостаточно рыженки: на счету {balance}, требуется {stake}."
            )
            #asyncio.create_task(
            #    delete_messages_later([msg, warning], delay=5)
            #)
            return
        update_coins(user.id, -stake)

    #if not action_text and stake == 0:
    #    warning = await msg.reply_text("❌ Ставка не может быть нулевой.")
        #asyncio.create_task(
        #    delete_messages_later([msg, warning], delay=5)
        #)
    #    return

    slot_msg = await msg.reply_dice(emoji="🎰")
    res = slot_msg.dice.value - 1
    v0 = (res >> 4) & 0b11
    v1 = (res >> 2) & 0b11
    v2 = res         & 0b11

    word = "ничего"
    if v0 == v1 == v2:
        mult = {1:5, 2:10, 0:15, 3:30}[v0]
        word = {1:"виноград", 2:"лимоны", 0:"слитки", 3:"топоры"}[v0]
    else:
        mult = 0

    name = parse_mention(user)
    result_msg = None
    if not action_text:
        new_balance = balance - stake + mult * stake

        # build and send result text (coins)
        if mult:
            payout = stake * mult
            update_coins(user.id, payout)
            result_text = (
                f"🎉 Ура! {name} налетел на «{word}» и помножил свою ставку в "
                f"{mult} раз — всего +{payout} рыженки! "
                f"Баланс: {new_balance} рыженки"
            )
        else:
            result_text = (
                f"💔 Увы, {name} проиграл ставку в {stake} рыженки."
                f" Баланс: {new_balance} рыженки"
            )

        result_msg = await msg.reply_text(
            text=result_text,
            parse_mode=constants.ParseMode.HTML
        )

        #if not mult:
            #asyncio.create_task(
            #    delete_messages_later(
            #        [msg, slot_msg, result_msg],
            #        delay=30
            #    )
            #)

    if action_text:
        if mult:
            await msg.reply_text(
                f"{name} налетел на «{word}» и теперь должен {action_text}",
                parse_mode=constants.ParseMode.HTML
            )
        #else:
            #asyncio.create_task(
            #    delete_messages_later(
            #        [msg, slot_msg],
            #        delay=30
            #    )
            #)

async def stop_slot_command(update: Update, context: CallbackContext):
    user = update.effective_user
    msg = update.effective_message
    
    if not user or (user.id not in MyBotState.MODERATORS and not is_helper(user.id)):
        return
    
    MyBotState.slot = False
    res = await msg.reply_text("✅ Слот машина остановлена.")

async def resume_slot_command(update: Update, context: CallbackContext):
    user = update.effective_user
    msg = update.effective_message
    
    if not user or (user.id not in MyBotState.MODERATORS and not is_helper(user.id)):
        return
    
    MyBotState.slot = True
    res = await msg.reply_text("✅ Слот машина возобновлена.")

async def add_helper(update: Update, context: CallbackContext):
    user = update.effective_user
    msg  = update.effective_message

    if not user or user.id not in MyBotState.MODERATORS:
        return

    args = context.args or []
    if len(args) != 1 or not args[0].isdigit():
        return await msg.reply_text("❌ Использование: /add_helper <user_id>")

    helper_id = int(args[0])

    with db:
        db.execute(
            "INSERT OR IGNORE INTO helper (id) VALUES (?)",
            (helper_id,)
        )
    
    helper_user = await context.bot.get_chat(helper_id)
    mention = parse_mention(helper_user)

    await msg.reply_text(f"✅ Пользователь {mention} стал помощником в Nerixane Cock Project.",
                         parse_mode="HTML")

async def remove_helper(update: Update, context: CallbackContext):
    user = update.effective_user
    msg  = update.effective_message

    if not user or user.id not in MyBotState.MODERATORS:
        return

    args = context.args or []
    if len(args) != 1 or not args[0].isdigit():
        return await msg.reply_text("❌ Использование: /remove_helper <user_id>")

    helper_id = int(args[0])
    with db:
        cur = db.execute(
            "DELETE FROM helper WHERE id = ?",
            (helper_id,)
        )
    
    helper_user = await context.bot.get_chat(helper_id)
    mention = parse_mention(helper_user)

    if cur.rowcount:
        await msg.reply_text(f"✅ Пользователь {mention} больше не помощник в Nerixane Cock Project.",
                             parse_mode="HTML")
    else:
        await msg.reply_text(f"ℹ️ Пользователь {mention} не был помощником.",
                             parse_mode="HTML")