import hashlib
import random
import sqlite3
from datetime import date

from flask import Flask, jsonify, render_template, request

app = Flask(__name__)

GRID_SIZE = 50
DB_PATH = "game.db"

winning_coordinate = {
    "x": random.randint(1, GRID_SIZE),
    "y": random.randint(1, GRID_SIZE),
}


def get_db():
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    return conn


def init_db():
    with get_db() as conn:
        conn.execute("""
            CREATE TABLE IF NOT EXISTS users (
                ip_address TEXT PRIMARY KEY,
                clicks_today INTEGER DEFAULT 0,
                max_clicks INTEGER DEFAULT 1,
                last_click_date TEXT,
                ref_code TEXT
            )
        """)


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
            "VALUES (?, 0, 1, ?, ?)",
            (ip, date.today().isoformat(), ref_code),
        )
        user = conn.execute("SELECT * FROM users WHERE ip_address = ?", (ip,)).fetchone()
    return user


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

        today = date.today().isoformat()
        if user["last_click_date"] != today:
            conn.execute(
                "UPDATE users SET clicks_today = 0, last_click_date = ? WHERE ip_address = ?",
                (today, ip),
            )
            user = conn.execute("SELECT * FROM users WHERE ip_address = ?", (ip,)).fetchone()

        clicks_left = max(user["max_clicks"] - user["clicks_today"], 0)

    return render_template(
        "index.html",
        grid_size=GRID_SIZE,
        clicks_left=clicks_left,
        ref_code=user["ref_code"],
    )


@app.route("/check_pixel", methods=["POST"])
def check_pixel():
    data = request.get_json(silent=True) or {}
    try:
        x = int(data.get("x"))
        y = int(data.get("y"))
    except (TypeError, ValueError):
        return jsonify({"allowed": False, "win": False, "message": "Coordinata non valida."}), 400

    ip = get_client_ip()
    today = date.today().isoformat()

    with get_db() as conn:
        user = get_or_create_user(conn, ip)

        if user["last_click_date"] != today:
            conn.execute(
                "UPDATE users SET clicks_today = 0, last_click_date = ? WHERE ip_address = ?",
                (today, ip),
            )
            user = conn.execute("SELECT * FROM users WHERE ip_address = ?", (ip,)).fetchone()

        if user["clicks_today"] >= user["max_clicks"]:
            return jsonify({
                "allowed": False,
                "win": False,
                "message": "Hai esaurito i click di oggi!",
                "ref_code": user["ref_code"],
                "clicks_left": 0,
            })

        conn.execute(
            "UPDATE users SET clicks_today = clicks_today + 1, last_click_date = ? "
            "WHERE ip_address = ?",
            (today, ip),
        )
        clicks_left = user["max_clicks"] - (user["clicks_today"] + 1)

    if x == winning_coordinate["x"] and y == winning_coordinate["y"]:
        return jsonify({
            "allowed": True,
            "win": True,
            "message": f"Hai vinto! Il pixel vincente era X: {x}, Y: {y}!",
            "clicks_left": clicks_left,
        })

    return jsonify({
        "allowed": True,
        "win": False,
        "message": f"Pixel sbagliato (X: {x}, Y: {y}). Riprova!",
        "clicks_left": clicks_left,
    })


init_db()

if __name__ == "__main__":
    app.run(debug=True)
