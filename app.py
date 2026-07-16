import hashlib
import logging
import os
import random
import sqlite3
from datetime import date

from authlib.integrations.flask_client import OAuth
from dotenv import load_dotenv
from flask import Flask, jsonify, redirect, render_template, request, session, url_for

load_dotenv()

logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s")
logger = logging.getLogger("pixel-game")

# =====================================================================
# CAMPAIGN CONFIGURATION — change these values to tune the game
# =====================================================================
CURRENT_PRIZE = "$10 Amazon Gift Card"
GRID_SIZE = 50  # Example: 50 generates a 50x50 grid. Set 100 for 100x100!
MAX_DAILY_CLICKS = 1

GOOGLE_CLIENT_ID = os.getenv("GOOGLE_CLIENT_ID")
GOOGLE_CLIENT_SECRET = os.getenv("GOOGLE_CLIENT_SECRET")
# =====================================================================

DB_PATH = "game.db"

app = Flask(__name__)
app.secret_key = os.getenv("FLASK_SECRET_KEY", "change-this-flask-session-secret-key")

oauth = OAuth(app)
google = oauth.register(
    name="google",
    client_id=GOOGLE_CLIENT_ID,
    client_secret=GOOGLE_CLIENT_SECRET,
    server_metadata_url="https://accounts.google.com/.well-known/openid-configuration",
    client_kwargs={"scope": "openid email profile"},
)

winning_coordinate = {
    "x": random.randint(1, GRID_SIZE),
    "y": random.randint(1, GRID_SIZE),
}


def get_db():
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    return conn


def init_db():
    """Create the users table and safely migrate old schemas."""
    with get_db() as conn:
        conn.execute("""
            CREATE TABLE IF NOT EXISTS users (
                ip_address TEXT PRIMARY KEY,
                clicks_today INTEGER DEFAULT 0,
                max_clicks INTEGER DEFAULT 1,
                last_click_date TEXT,
                ref_code TEXT,
                email TEXT,
                google_id TEXT
            )
        """)
        # Safe migration: add missing columns to an existing table
        existing = {row["name"] for row in conn.execute("PRAGMA table_info(users)")}
        for column, definition in (("email", "TEXT"), ("google_id", "TEXT")):
            if column not in existing:
                conn.execute(f"ALTER TABLE users ADD COLUMN {column} {definition}")


def generate_ref_code(ip):
    return hashlib.sha256(ip.encode()).hexdigest()[:6].upper()


def get_client_ip():
    return request.headers.get("X-Forwarded-For", request.remote_addr).split(",")[0].strip()


def get_or_create_user(conn, ip):
    user = conn.execute("SELECT * FROM users WHERE ip_address = ?", (ip,)).fetchone()
    if user is None:
        ref_code = generate_ref_code(ip)
        conn.execute(
            "INSERT INTO users (ip_address, clicks_today, max_clicks, last_click_date, ref_code) "
            "VALUES (?, 0, ?, ?, ?)",
            (ip, MAX_DAILY_CLICKS, date.today().isoformat(), ref_code),
        )
        user = conn.execute("SELECT * FROM users WHERE ip_address = ?", (ip,)).fetchone()
    return user


def reset_daily_clicks(conn, user, ip):
    today = date.today().isoformat()
    if user["last_click_date"] != today:
        conn.execute(
            "UPDATE users SET clicks_today = 0, last_click_date = ? WHERE ip_address = ?",
            (today, ip),
        )
        user = conn.execute("SELECT * FROM users WHERE ip_address = ?", (ip,)).fetchone()
    return user


def is_logged_in():
    return "email" in session


@app.route("/")
def index():
    ip = get_client_ip()
    with get_db() as conn:
        user = get_or_create_user(conn, ip)

        ref = request.args.get("ref", "").strip().upper()
        if ref and ref != user["ref_code"]:
            referrer = conn.execute(
                "SELECT * FROM users WHERE ref_code = ?", (ref,)
            ).fetchone()
            if referrer:
                conn.execute(
                    "UPDATE users SET max_clicks = max_clicks + 2 WHERE ref_code = ?",
                    (ref,),
                )

        user = reset_daily_clicks(conn, user, ip)
        clicks_left = max(user["max_clicks"] - user["clicks_today"], 0)

    return render_template(
        "index.html",
        grid_size=GRID_SIZE,
        current_prize=CURRENT_PRIZE,
        clicks_left=clicks_left,
        ref_code=user["ref_code"],
        user_email=session.get("email", ""),
    )


@app.route("/login")
def login():
    redirect_uri = url_for("auth_google_callback", _external=True)
    return google.authorize_redirect(redirect_uri)


@app.route("/auth/google/callback")
def auth_google_callback():
    token = google.authorize_access_token()
    userinfo = token.get("userinfo") or google.userinfo(token=token)

    email = userinfo.get("email")
    google_id = userinfo.get("sub")

    ip = get_client_ip()
    with get_db() as conn:
        user = get_or_create_user(conn, ip)

        # Anti-abuse: one Google account per IP, one IP per Google account
        if user["google_id"] and user["google_id"] != google_id:
            logger.warning("SECURITY: IP %s tried to link a second Google account (%s)", ip, email)
            return redirect(url_for(
                "index",
                login_error="Security limit: This device or account is already associated with another profile.",
            ))

        other = conn.execute(
            "SELECT ip_address FROM users WHERE google_id = ? AND ip_address != ?",
            (google_id, ip),
        ).fetchone()
        if other:
            logger.warning(
                "SECURITY: Google account %s (already on IP %s) tried to link from IP %s",
                email, other["ip_address"], ip,
            )
            return redirect(url_for(
                "index",
                login_error="Security limit: This device or account is already associated with another profile.",
            ))

        is_new_link = user["google_id"] is None
        conn.execute(
            "UPDATE users SET email = ?, google_id = ? WHERE ip_address = ?",
            (email, google_id, ip),
        )
        if is_new_link:
            logger.info("NEW ACCOUNT LINKED: ip=%s email=%s", ip, email)

    session["email"] = email
    session["google_id"] = google_id
    return redirect(url_for("index"))


@app.route("/logout")
def logout():
    session.clear()
    return redirect(url_for("index"))


@app.route("/check_pixel", methods=["POST"])
def check_pixel():
    data = request.get_json(silent=True) or {}
    try:
        x = int(data.get("x"))
        y = int(data.get("y"))
    except (TypeError, ValueError):
        return jsonify({"allowed": False, "win": False, "message": "Invalid coordinate."}), 400

    ip = get_client_ip()
    today = date.today().isoformat()
    logged_in = is_logged_in()

    with get_db() as conn:
        user = get_or_create_user(conn, ip)
        user = reset_daily_clicks(conn, user, ip)

        if user["clicks_today"] >= user["max_clicks"]:
            if not logged_in:
                return jsonify({
                    "allowed": False,
                    "require_login": True,
                    "win": False,
                    "message": "Sign in with Google to generate your referral link and get extra clicks!",
                    "clicks_left": 0,
                })
            return jsonify({
                "allowed": False,
                "require_login": False,
                "win": False,
                "message": "Out of clicks for today!",
                "ref_code": user["ref_code"],
                "clicks_left": 0,
            })

        conn.execute(
            "UPDATE users SET clicks_today = clicks_today + 1, last_click_date = ? "
            "WHERE ip_address = ?",
            (today, ip),
        )
        clicks_left = user["max_clicks"] - (user["clicks_today"] + 1)

    is_winner = x == winning_coordinate["x"] and y == winning_coordinate["y"]

    if is_winner and not logged_in:
        return jsonify({
            "allowed": True,
            "require_login": True,
            "win": False,
            "message": f"🎉 YOU FOUND THE PIXEL! Sign in with Google to verify your account and claim the {CURRENT_PRIZE}!",
            "clicks_left": clicks_left,
        })

    if is_winner:
        return jsonify({
            "allowed": True,
            "require_login": False,
            "win": True,
            "message": f"🎉 YOU WON THE {CURRENT_PRIZE.upper()}!",
            "clicks_left": clicks_left,
        })

    return jsonify({
        "allowed": True,
        "require_login": False,
        "win": False,
        "message": "❌ Wrong pixel! Check today's clue and try again.",
        "clicks_left": clicks_left,
    })


init_db()

if __name__ == "__main__":
    app.run(debug=True)
