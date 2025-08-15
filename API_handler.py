# app.py
from fastapi import FastAPI, Header, HTTPException, Depends
from fastapi.responses import JSONResponse
from starlette.status import HTTP_401_UNAUTHORIZED
import os
import hmac
import hashlib
import sqlite3
from contextlib import contextmanager
from pydantic import BaseModel, conint
import logging
from datetime import datetime

DB_PATH = "info.db"

app = FastAPI(title="API-key protected API")

logging.basicConfig(
    filename="charge.log",
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
)

# ---- Config / storage -------------------------------------------------
# Store *hashed* API keys in env (comma-separated). Example:
# APPROVED_KEY_HASHES="sha256:abcd...,sha256:efgh..."
APPROVED_KEY_HASHES = {
    h.strip() for h in os.getenv("APPROVED_KEY_HASHES", "").split(",") if h.strip()
}

def sha256_digest(raw: str) -> str:
    salt = os.getenv("HASH_SALT", "")
    d = hashlib.sha256()
    d.update((salt + raw).encode("utf-8"))
    return "sha256:" + d.hexdigest()

def verify_api_key(x_api_key: str | None) -> None:
    if not x_api_key:
        raise HTTPException(
            status_code=HTTP_401_UNAUTHORIZED,
            detail="Missing X-API-Key header",
            headers={"WWW-Authenticate": "API key"},
        )
    submitted = sha256_digest(x_api_key)
    for good in APPROVED_KEY_HASHES:
        if hmac.compare_digest(submitted, good):
            return  # OK
    raise HTTPException(
        status_code=HTTP_401_UNAUTHORIZED,
        detail="Invalid API key",
        headers={"WWW-Authenticate": "API key"},
    )

def require_key(x_api_key: str | None = Header(default=None, alias="X-API-Key")):
    verify_api_key(x_api_key)

@contextmanager
def db() -> sqlite3.Connection:
    conn = sqlite3.connect(DB_PATH)
    try:
        yield conn
    finally:
        conn.close()

class ChargeRequest(BaseModel):
    id: str
    amount: conint(strict=True, gt=0)

# ---- Public endpoint ---------------------------------------------------
@app.get("/health")
def health():
    return {"ok": True}

@app.post("/charge", dependencies=[Depends(require_key)])
def charge(req: ChargeRequest):
    user_id = req.id
    amount = int(req.amount)

    with db() as conn:
        conn.isolation_level = None
        cur = conn.cursor()
        try:
            cur.execute("BEGIN IMMEDIATE")
            cur.execute(
                "UPDATE user SET coins = coins - ? WHERE id = ? AND coins >= ?;",
                (amount, user_id, amount),
            )
            if cur.rowcount == 1:
                cur.execute("COMMIT")
                logging.info(f"SUCCESS: Deducted {amount} coins from user {user_id}")
                return {"ok": True}
            else:
                cur.execute("ROLLBACK")
                cur.execute("SELECT coins FROM user WHERE id = ?;", (user_id,))
                row = cur.fetchone()
                if row is None:
                    logging.warning(f"FAIL: User {user_id} not found (amount {amount})")
                    return {"ok": False, "error": "not_found"}
                else:
                    logging.warning(
                        f"FAIL: Insufficient funds for user {user_id} "
                        f"(balance {row[0]}, tried to deduct {amount})"
                    )
                    return {"ok": False, "error": "insufficient_funds"}
        except Exception:
            cur.execute("ROLLBACK")
            logging.error(f"ERROR: Exception for user {user_id}, amount {amount}: {e}")
            raise

# ---- Error handler (optional: uniform JSON) ---------------------------
@app.exception_handler(HTTPException)
def http_exc(_, exc: HTTPException):
    return JSONResponse(status_code=exc.status_code, content={"error": exc.detail})
