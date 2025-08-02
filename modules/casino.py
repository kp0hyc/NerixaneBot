import io
import matplotlib.pyplot as plt

from .utils import *

from telegram import (
    InputMediaPhoto,
)
from telegram.ext import (
    CallbackContext,
)

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
        if votes > 0:
            coef = total_votes / votes
            coef_str = f", коэф.: {coef:.2f}"
        else:
            coef_str = ""
        lines.append(f"{i+1}. {r['option']} — {votes} рыженки{coef_str}")
    options_md = "\n".join(lines)

    # 5) Build the caption text
    text = (
        f"🎲 *{question}* (#{pid})\n\n"
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