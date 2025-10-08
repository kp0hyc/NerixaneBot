import io
import matplotlib.pyplot as plt

from .utils import *
from .updates import *

from telegram.helpers import escape_markdown

from telegram import (
    InputMediaPhoto,
    Update,
)
from telegram.ext import (
    CallbackContext,
    ContextTypes,
)

def _ensure_rd_bucket(context: ContextTypes.DEFAULT_TYPE):
    return context.bot_data.setdefault("rd", {})

def _random_integer_partition_allow_zero(total: int, n: int) -> list[int]:
    if n <= 0:
        return []
    if n == 1:
        return [total]
    ws = [random.random() for _ in range(n)]
    s = sum(ws) or 1.0
    raw = [int(total * (w / s)) for w in ws]
    rem = total - sum(raw)
    idxs = list(range(n))
    random.shuffle(idxs)
    for i in idxs[:rem]:
        raw[i] += 1
    return raw

def create_bet_image_and_text(pid):
    # 1) Gather stakes
    opts = db.execute(
        """
        SELECT po.option, COALESCE(SUM(b.amount), 0) AS total
        FROM poll_option po
        LEFT JOIN bets b
          ON po.poll_id = b.poll_id
         AND po.idx     = b.option_idx
        WHERE po.poll_id = ?
        GROUP BY po.idx, po.option
        """,
        (pid,)
    ).fetchall()

    # 2) Fetch question
    row = db.execute(
        "SELECT question FROM poll WHERE id = ?",
        (pid,)
    ).fetchone()
    if row is None:
        raise ValueError(f"Poll {pid} not found")
    question = row["question"]

    # 3) Compute total votes across all options
    total_votes = sum(r["total"] for r in opts)

    # 4) Build the markdown list: "1. Option — X votes, coef: Y.YY"
    lines = []
    for i, r in enumerate(opts):
        votes = r["total"]
        opt_text = escape_markdown(r["option"], version=2)
        if votes > 0:
            coef = total_votes / votes
            coef_str = f", коэф.: {coef:.2f}"
        else:
            coef_str = ""
        lines.append(f"{i+1}\\. {opt_text} — {votes} рыженки{coef_str}")
    options_md = "\n".join(lines)
    
    q_md = escape_markdown(question, version=2)
    
    pid_tag = f"\\(\\#{pid}\\)"
    
    text = (
        f"🎲 *{q_md}* {pid_tag}\n\n"
        f"{options_md}\n\n"
        "Сделай свою ставку, нажав на кнопку ниже:"
    )
    
    link = f"https://t.me/{MY_BOT_USERNAME}/{WEB_APP_NAME}?startapp=poll_{pid}"
    kb = InlineKeyboardMarkup([[
        InlineKeyboardButton("Сделать ставку 🔗", url=link)
    ]])

    # 6) Prepare for pie-chart: only nonzero slices
    labels = [r["option"] for r in opts]
    sizes  = [r["total"]  for r in opts]
    nonzero = [(lbl, sz) for lbl, sz in zip(labels, sizes) if sz > 0]

    # 7) If no votes at all, return the default image
    if not nonzero or True:
        with open(CASINO_IMAGE, 'rb') as f:
            buf = io.BytesIO(f.read())
        buf.seek(0)
        return text, kb, buf

    # 8) Otherwise draw the pie
    labels, sizes = zip(*nonzero)
    fig, ax = plt.subplots()
    ax.pie(sizes, labels=labels, autopct='%1.1f%%', startangle=90)
    ax.axis('equal')

    buf = io.BytesIO()
    plt.savefig(buf, format='PNG', bbox_inches='tight')
    plt.close(fig)
    buf.seek(0)
    return text, kb, buf

async def refresh_polls(context: CallbackContext):
    rows = db.execute(
        "SELECT id, chat_id, message_id FROM poll WHERE status=0"
    ).fetchall()

    for row in rows:
        pid, chat, msg_id = row["id"], row["chat_id"], row["message_id"]
        
        if not chat or not msg_id:
            continue

        text, kb, buf = create_bet_image_and_text(pid)
        media = InputMediaPhoto(
            media=buf,
            caption=text,
            parse_mode="Markdown"
        )

        try:
            await context.bot.edit_message_media(
                chat_id=chat,
                message_id=msg_id,
                media=media,
                reply_markup=kb
            )
        except Exception as e:
            print(f"Failed to refresh poll {pid}: {e}")

def get_random_deposit_ts() -> int | None:
    row = db.execute("SELECT ts FROM random_deposit ORDER BY ts LIMIT 1").fetchone()
    return int(row[0]) if row else None

def schedule_next_random_deposit(ctx: ContextTypes.DEFAULT_TYPE) -> None:
    next_dt = datetime.now(TYUMEN) + timedelta(minutes=random.randint(6*60, 36*60))

    with db:
        db.execute("DELETE FROM random_deposit")
        db.execute("INSERT INTO random_deposit (ts) VALUES (?)", (int(next_dt.timestamp()),))

    ctx.job_queue.run_once(
        random_deposit_job,
        when=next_dt,
        name="random_deposit",
    )
    print(f"Random deposit scheduled for {next_dt.isoformat()}")

async def random_deposit_job(ctx: ContextTypes.DEFAULT_TYPE) -> None:
    text = "💸 Время додепа! Раздаётся 10.000 рыженки между всеми, кто поучаствует."
    keyboard = InlineKeyboardMarkup(
        [[InlineKeyboardButton("🎁 Додеп!", callback_data="rd_join")]]
    )
    msg = await ctx.bot.send_message(chat_id=ORIG_CHANNEL_ID, text=text, reply_markup=keyboard)
    print("Random deposit fired at", datetime.now(TYUMEN).isoformat())

    expiry_dt = datetime.now(TYUMEN) + timedelta(minutes=5)
    rd_bucket = _ensure_rd_bucket(ctx)
    rd_bucket[msg.message_id] = {
        "participants": [],
        "seen": set(),
        "expiry": expiry_dt,
    }

    ctx.job_queue.run_once(
        finalize_giveaway,
        when=expiry_dt,
        name=f"rd_finalize_{msg.message_id}",
        data={"message_id": msg.message_id},
    )

    schedule_next_random_deposit(ctx)

async def random_deposit(ctx: ContextTypes.DEFAULT_TYPE):
    ts = get_random_deposit_ts()
    if ts is None:
        await random_deposit_job(ctx)
        return

    stored_dt = datetime.fromtimestamp(ts, TYUMEN)
    now_dt = datetime.now(TYUMEN)

    if stored_dt <= now_dt:
        print(f"Random deposit ts {ts} has already passed ({stored_dt.isoformat()})")
        await random_deposit_job(ctx)
        return

    ctx.job_queue.run_once(
        random_deposit_job,
        when=stored_dt,
        name="random_deposit",
    )
    print(f"Random deposit scheduled for {stored_dt.isoformat()} ({(stored_dt - now_dt).total_seconds()} seconds later)")

async def on_rd_join(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    q = update.callback_query
    user = q.from_user
    msg = q.message
    now = datetime.now(TYUMEN)

    rd_bucket = _ensure_rd_bucket(context)
    state = rd_bucket.get(msg.message_id)
    if not state:
        await q.answer("Раздача уже давно завершена.", show_alert=True)
        try:
            await msg.edit_reply_markup(reply_markup=None)
        except Exception:
            pass
        return

    if now >= state["expiry"]:
        await q.answer("Раздача уже завершена.", show_alert=True)
        try:
            await msg.edit_reply_markup(reply_markup=None)
        except Exception:
            pass
        return

    if user.id in state["seen"]:
        await q.answer("Вы уже участник!", show_alert=False)
        return

    state["seen"].add(user.id)
    display_name = parse_name(user)
    state["participants"].append((user.id, display_name))

    try:
        base = "💸 Время додепа! Раздаётся 10.000 рыженки между всеми, кто поучаствует.\n\n"
        participants_text = "\n".join(f"{i+1}. {name}" for i, (_, name) in enumerate(state["participants"]))
        new_text = f"{base}Участники:\n{participants_text}\n\nНажми кнопку ниже, чтобы присоединиться!"
        await msg.edit_text(new_text, reply_markup=msg.reply_markup, parse_mode="HTML")
    except Exception:
        pass

    await q.answer("Жди результатов! 🎉")

async def finalize_giveaway(ctx: ContextTypes.DEFAULT_TYPE) -> None:
    message_id = ctx.job.data["message_id"]
    rd_bucket = _ensure_rd_bucket(ctx)
    state = rd_bucket.pop(message_id, None)
    if not state:
        return

    participants = state["participants"]

    try:
        await ctx.bot.delete_message(chat_id=ORIG_CHANNEL_ID, message_id=message_id)
    except Exception:
        try:
            await ctx.bot.edit_message_reply_markup(chat_id=ORIG_CHANNEL_ID, message_id=message_id, reply_markup=None)
        except Exception:
            pass

    if not participants:
        #await ctx.bot.send_message(chat_id=ORIG_CHANNEL_ID, text="No participants this time. 😿")
        return

    n = len(participants)
    shares = _random_integer_partition_allow_zero(10000, n)
    shares.sort(reverse=True)

    results = list(zip(participants, shares))

    lines = ["🏁 Результаты раздачи"]
    with db:
        for (uid, name), coins in results:
            if coins > 0:
                cur = db.execute("UPDATE user SET coins = COALESCE(coins, 0) + ? WHERE id = ?", (coins, uid))
                if cur.rowcount == 0:
                    db.execute("INSERT INTO user (id, coins) VALUES (?, ?)", (uid, coins))
            lines.append(f"• {name}: +{coins} рыженки")
    
    lines.append("\n🎉 Спасибо за участие! Потратить вы можете в казино \"ЛАКИ 68\"")
    keyboard = InlineKeyboardMarkup(
        [[InlineKeyboardButton("🎰 Крутануть!", url=CASINO_JOIN_LINK)]]
    )
    await ctx.bot.send_message(chat_id=ORIG_CHANNEL_ID, text="\n".join(lines), parse_mode="HTML", reply_markup=keyboard)

async def transfer_coins(update: Update, context: CallbackContext):
    msg = update.effective_message
    if not msg:
        return

    reply = update.message.reply_to_message
    if not reply:
        await update.message.reply_text(
            "Ты должен ответить на сообщение нужного чатера\n"
            "/transfer <N>"
        )
        return

    user = update.effective_user

    target = reply.from_user
    target_id = target.id
    display_name = parse_mention_with_alias(target)

    if not context.args:
        await update.message.reply_text("❌ Ты должен указать количество рыженки, например /transfer 100")
        return

    diff_str = context.args[0]
    try:
        diff = int(diff_str)
    except ValueError:
        await update.message.reply_text("❌ Вторым аргументом должно быть число.")
        return
    
    if diff < 1:
        await update.message.reply_text("❌ Что за попытка скама, вы должны ввести позитивное число!")
        return
    if user.id == target_id:
        await update.message.reply_text("❌ Нельзя перевести самому себе!")
        return

    with db:
        row = db.execute(
            "SELECT coins FROM user WHERE id = ?",
            (user.id,)
        ).fetchone()
        current = row["coins"] if row else 0
        if current < diff:
            await update.message.reply_text("❌ У тебя нет столько рыженки!")
            return
    update_coins(user.id, -diff)
    update_coins(target_id, diff)

    await update.message.reply_text(f"✅ {display_name} получил {diff} рыженки",
    parse_mode="HTML")