# server.py
import os
import sqlite3
from datetime import datetime
from fastapi import FastAPI, Request, HTTPException
from fastapi.responses import JSONResponse, HTMLResponse
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates
from pydantic import BaseModel
from fastapi.responses import StreamingResponse
import io
import matplotlib.pyplot as plt

# Configuration
database_path = os.getenv("DB_PATH", "info.db")
app = FastAPI()

# Serve static files (for CSS/JS)
base_dir = os.path.dirname(os.path.abspath(__file__))
app.mount(
    "/static",
    StaticFiles(directory=os.path.join(base_dir, "static")),
    name="static"
)
templates = Jinja2Templates(directory=os.path.join(base_dir, "templates"))

# Initialize SQLite
db = sqlite3.connect(database_path, check_same_thread=False)
db.row_factory = sqlite3.Row

# Pydantic model for incoming bets
class BetIn(BaseModel):
    poll_id: int
    option_idx: int
    user_id: int
    amount: int  # coins are integers

# Function to update coins for a user in the database (adds to current balance)
def update_coins(uid: int, coins: int):
    with db:
        row = db.execute(
            "SELECT coins FROM user WHERE id = ?", (uid,)
        ).fetchone()
        current = row["coins"] if row else 0
        new_balance = current + coins
        db.execute(
            "INSERT INTO user (id, coins) VALUES (?, ?) "
            "ON CONFLICT(id) DO UPDATE SET coins = excluded.coins",
            (uid, new_balance)
        )

# Helper to log query params
def log_params(path: str, params: dict):
    timestamp = datetime.utcnow().isoformat()
    line = f"{timestamp} - {path} params: {params}\n"
    print(line.strip())
    with open("query_params.log", "a", encoding="utf-8") as f:
        f.write(line)

# -- Root endpoint: catch WebApp launch --
@app.get("/", response_class=HTMLResponse)
async def root(request: Request):
    params = dict(request.query_params)
    log_params("/", params)
    payload = params.get("tgWebAppStartParam") or params.get("startapp")
    poll_id = None
    if payload and payload.startswith("poll_"):
        try:
            poll_id = int(payload.split("_", 1)[1])
        except ValueError:
            poll_id = None
    if poll_id is not None:
        return templates.TemplateResponse(
            "index.html", {"request": request, "poll_id": poll_id}
        )
    return JSONResponse({"query_params": params})

# -- API: fetch poll data with finished flag --
@app.get("/api/poll/{poll_id}")
def get_poll(poll_id: int):
    # Fetch question and finished status
    row = db.execute(
        "SELECT question, status FROM poll WHERE id = ?", (poll_id,)
    ).fetchone()
    if not row:
        raise HTTPException(status_code=404, detail="Poll not found")
    question = row["question"]
    status = row["status"]

    # Fetch options and totals
    opts = db.execute(
        "SELECT idx, option FROM poll_option WHERE poll_id = ? ORDER BY idx", (poll_id,)
    ).fetchall()
    totals_rows = db.execute(
        "SELECT option_idx, SUM(amount) AS total FROM bets WHERE poll_id = ? GROUP BY option_idx", (poll_id,)
    ).fetchall()
    totals = {r["option_idx"]: r["total"] for r in totals_rows}

    return JSONResponse({
        "id": poll_id,
        "question": question,
        "status": status,
        "options": [
            {"idx": o["idx"], "text": o["option"], "total": int(totals.get(o["idx"], 0))}
            for o in opts
        ]
    })

# -- API: fetch user balance --
@app.get("/api/balance/{user_id}")
def get_balance(user_id: int):
    row = db.execute(
        "SELECT coins FROM user WHERE id = ?", (user_id,)
    ).fetchone()
    coins = row["coins"] if row else 0
    return JSONResponse({"user_id": user_id, "coins": coins})

# -- API: fetch a user's bet for a specific poll --
@app.get("/api/bet/{poll_id}/{user_id}")
def get_user_bet(poll_id: int, user_id: int):
    row = db.execute(
        "SELECT option_idx, amount FROM bets WHERE poll_id = ? AND user_id = ?", (poll_id, user_id)
    ).fetchone()
    print("row: ", row)
    if not row:
        raise HTTPException(status_code=404, detail="Bet not found")
    return JSONResponse({
        "option_idx": row["option_idx"],
        "amount":     row["amount"]
    })

# -- API: post a bet --
@app.post("/api/bet")
def post_bet(bet: BetIn):
    print("api bet called")
    # 1) Fetch poll status
    poll = db.execute(
        "SELECT status FROM poll WHERE id = ?",
        (bet.poll_id,)
    ).fetchone()
    if not poll:
        raise HTTPException(404, "Poll not found")
    if poll["status"] != 0:
        raise HTTPException(400, "Betting is not open on this poll")

    # 2) Validate amount
    if bet.amount <= 0:
        raise HTTPException(400, "Bet amount must be positive")

    # 3) Check existing bet (if any)
    existing = db.execute(
        "SELECT option_idx, amount FROM bets WHERE poll_id = ? AND user_id = ?",
        (bet.poll_id, bet.user_id)
    ).fetchone()

    # 4) Check balance
    bal_row = db.execute(
        "SELECT coins FROM user WHERE id = ?", (bet.user_id,)
    ).fetchone()
    balance = bal_row["coins"] if bal_row else 0
    if bet.amount > balance:
        raise HTTPException(400, "Insufficient balance")

    with db:
        if existing:
            # top‑up only if same option
            if existing["option_idx"] != bet.option_idx:
                raise HTTPException(400, "Cannot bet on a different option once placed")
            new_amount = existing["amount"] + bet.amount
            db.execute(
                "UPDATE bets SET amount = ? WHERE poll_id = ? AND user_id = ?",
                (new_amount, bet.poll_id, bet.user_id)
            )
        else:
            # first‑time bet
            db.execute(
                "INSERT INTO bets (poll_id, user_id, option_idx, amount) VALUES (?,?,?,?)",
                (bet.poll_id, bet.user_id, bet.option_idx, bet.amount)
            )
        # deduct coins
        db.execute(
            "UPDATE user SET coins = coins - ? WHERE id = ?",
            (bet.amount, bet.user_id)
        )

    return JSONResponse({"success": True})

# -- Legacy poll page route --
@app.get("/poll/{poll_id}", response_class=HTMLResponse)
def poll_page(request: Request, poll_id: int):
    params = dict(request.query_params)
    log_params(f"/poll/{poll_id}", params)
    return templates.TemplateResponse(
        "index.html", {"request": request, "poll_id": poll_id}
    )

# -- API: return pie-chart distribution for a poll --
@app.get("/api/poll/{poll_id}/chart")
def get_poll_chart(poll_id: int):
    # 1) Fetch each option’s total
    rows = db.execute(
        """
        SELECT po.option AS text,
               COALESCE(SUM(b.amount), 0) AS total
        FROM poll_option po
        LEFT JOIN bets b
          ON po.poll_id = b.poll_id
         AND po.idx     = b.option_idx
        WHERE po.poll_id = ?
        GROUP BY po.idx, po.option
        """,
        (poll_id,)
    ).fetchall()

    if not rows:
        # Poll doesn’t exist
        raise HTTPException(status_code=404, detail="Poll not found")

    # 2) Filter out zero-vote slices
    nonzero = [(r["text"], r["total"]) for r in rows if r["total"] > 0]

    # 3) If no votes at all, serve the default image
    if not nonzero:
        raise HTTPException(status_code=404, detail="No votes yet")

    # 4) Draw the pie chart
    labels, sizes = zip(*nonzero)
    fig, ax = plt.subplots()
    ax.pie(sizes, labels=labels, autopct='%1.1f%%', startangle=90)
    ax.axis('equal')

    # 5) Stream it back
    buf = io.BytesIO()
    plt.savefig(buf, format='PNG', bbox_inches='tight')
    plt.close(fig)
    buf.seek(0)
    return StreamingResponse(buf, media_type="image/png")

if __name__ == "__main__":
    import uvicorn
    uvicorn.run(app, host="0.0.0.0", port=int(os.getenv("PORT", 8000)))
