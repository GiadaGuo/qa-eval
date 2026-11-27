"""Account and session endpoints for the browser workspace."""

import hashlib
import os
import secrets
import sqlite3
import time
from contextlib import closing
from pathlib import Path
from typing import Literal

from fastapi import APIRouter, Depends, HTTPException, Request, Response
from pydantic import BaseModel, Field

router = APIRouter(prefix="/api/web", tags=["Web workspace"])
COOKIE = "qa_eval_session"
SESSION_SECONDS = 8 * 60 * 60
Role = Literal["admin", "operator"]


class Credentials(BaseModel):
    username: str = Field(min_length=1, max_length=48, pattern=r"^[a-zA-Z0-9_.-]+$")
    password: str = Field(min_length=6, max_length=128)


class AccountCreate(Credentials):
    role: Role


class AccountUpdate(BaseModel):
    role: Role
    enabled: bool


def password_hash(password: str, salt: str) -> str:
    return hashlib.pbkdf2_hmac(
        "sha256", password.encode(), bytes.fromhex(salt), 600_000
    ).hex()


def connect():
    path = Path(
        os.getenv("QA_EVAL_WEB_ACCOUNTS_DB", "data/runtime/web-accounts.sqlite3")
    )
    path.parent.mkdir(parents=True, exist_ok=True)
    with closing(sqlite3.connect(path, check_same_thread=False)) as db:
        db.row_factory = sqlite3.Row
        with db:
            db.execute("""CREATE TABLE IF NOT EXISTS accounts (
                username TEXT PRIMARY KEY, salt TEXT NOT NULL, password_hash TEXT NOT NULL,
                role TEXT NOT NULL, enabled INTEGER NOT NULL)""")
            db.execute("""CREATE TABLE IF NOT EXISTS sessions (
                token_hash TEXT PRIMARY KEY, username TEXT NOT NULL, expires_at INTEGER NOT NULL)""")
            db.execute("""CREATE TABLE IF NOT EXISTS work_checkpoints (
                user_id TEXT NOT NULL REFERENCES accounts(username),
                kind TEXT NOT NULL, object_id TEXT NOT NULL, touched_at TEXT NOT NULL,
                hidden INTEGER NOT NULL DEFAULT 0, PRIMARY KEY (user_id, kind, object_id))""")
            db.execute("""CREATE INDEX IF NOT EXISTS work_checkpoints_recent
                ON work_checkpoints (user_id, hidden, touched_at DESC)""")
            db.execute("""CREATE TABLE IF NOT EXISTS work_drafts (
                user_id TEXT NOT NULL REFERENCES accounts(username),
                kind TEXT NOT NULL, object_id TEXT NOT NULL, payload TEXT NOT NULL,
                PRIMARY KEY (user_id, kind, object_id))""")
            db.execute("""CREATE TABLE IF NOT EXISTS operator_work_drafts (
                user_id TEXT NOT NULL REFERENCES accounts(username),
                draft_id TEXT NOT NULL, version_id TEXT NOT NULL,
                payload TEXT NOT NULL, PRIMARY KEY (user_id, draft_id))""")
            if not db.execute(
                "SELECT 1 FROM accounts WHERE username = 'admin'"
            ).fetchone():
                salt = secrets.token_hex(16)
                db.execute(
                    "INSERT OR IGNORE INTO accounts VALUES ('admin', ?, ?, 'admin', 1)",
                    (
                        salt,
                        password_hash(
                            os.getenv("QA_EVAL_WEB_ADMIN_PASSWORD", "123456"), salt
                        ),
                    ),
                )
        yield db


def token_hash(token: str) -> str:
    return hashlib.sha256(token.encode()).hexdigest()


def current_user(request: Request, db: sqlite3.Connection = Depends(connect)):
    token = request.cookies.get(COOKIE, "")
    account = db.execute(
        """SELECT accounts.username, role, enabled FROM sessions JOIN accounts USING (username)
           WHERE token_hash = ? AND expires_at > ? AND enabled = 1""",
        (token_hash(token), int(time.time())),
    ).fetchone()
    if account is None:
        raise HTTPException(401, "请先登录")
    return dict(account)


def admin_only(user=Depends(current_user)):
    if user["role"] != "admin":
        raise HTTPException(403, "需要管理员权限")


@router.post("/session")
def login(body: Credentials, request: Request, response: Response, db=Depends(connect)):
    row = db.execute(
        "SELECT * FROM accounts WHERE username = ?", (body.username,)
    ).fetchone()
    salt = row["salt"] if row else "0" * 32
    candidate = password_hash(body.password, salt)
    if (
        not row
        or not secrets.compare_digest(candidate, row["password_hash"])
        or not row["enabled"]
    ):
        raise HTTPException(401, "用户名或密码错误，或账号已停用")
    token = secrets.token_urlsafe(32)
    now = int(time.time())
    with db:
        db.execute(
            "DELETE FROM sessions WHERE expires_at <= ? OR token_hash = ?",
            (now, token_hash(request.cookies.get(COOKIE, ""))),
        )
        db.execute(
            "INSERT INTO sessions VALUES (?, ?, ?)",
            (token_hash(token), row["username"], now + SESSION_SECONDS),
        )
    response.set_cookie(
        COOKIE,
        token,
        httponly=True,
        secure=request.url.scheme == "https",
        samesite="lax",
        max_age=SESSION_SECONDS,
    )
    return {"username": row["username"], "role": row["role"], "enabled": True}


@router.get("/session")
def session(user=Depends(current_user)):
    return user


@router.delete("/session")
def logout(request: Request, response: Response, db=Depends(connect)):
    with db:
        db.execute(
            "DELETE FROM sessions WHERE token_hash = ?",
            (token_hash(request.cookies.get(COOKIE, "")),),
        )
    response.delete_cookie(COOKIE)
    return {"ok": True}


@router.get("/accounts", dependencies=[Depends(admin_only)])
def accounts(db=Depends(connect)):
    return [
        dict(row)
        for row in db.execute(
            "SELECT username, role, enabled FROM accounts ORDER BY username"
        )
    ]


@router.post("/accounts", status_code=201, dependencies=[Depends(admin_only)])
def create_account(body: AccountCreate, db=Depends(connect)):
    salt = secrets.token_hex(16)
    digest = password_hash(body.password, salt)
    try:
        with db:
            db.execute(
                "INSERT INTO accounts VALUES (?, ?, ?, ?, 1)",
                (body.username, salt, digest, body.role),
            )
    except sqlite3.IntegrityError as exc:
        raise HTTPException(409, "用户名已存在") from exc
    return {"username": body.username, "role": body.role, "enabled": True}


@router.patch("/accounts/{username}", dependencies=[Depends(admin_only)])
def update_account(username: str, body: AccountUpdate, db=Depends(connect)):
    if username == "admin":
        raise HTTPException(403, "内置管理员账号受保护")
    with db:
        updated = db.execute(
            "UPDATE accounts SET role = ?, enabled = ? WHERE username = ?",
            (body.role, body.enabled, username),
        )
        if updated.rowcount == 0:
            raise HTTPException(404, "账号不存在")
        if not body.enabled:
            db.execute("DELETE FROM sessions WHERE username = ?", (username,))
    return {"username": username, **body.model_dump()}
