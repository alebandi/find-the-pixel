import hashlib
import json
import logging
import os
import random
import re
import sqlite3
import sys
from datetime import date, datetime, timedelta, timezone

import stripe
from authlib.integrations.flask_client import OAuth
from dotenv import load_dotenv
from flask import Flask, jsonify, redirect, render_template, request, session, url_for
from flask_limiter import Limiter

load_dotenv()

logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s")
logger = logging.getLogger("pixel-game")

# =====================================================================
# CAMPAIGN CONFIGURATION — change these values to tune the game
# =====================================================================
CURRENT_PRIZE = "$10 Amazon Gift Card"
GRID_SIZE = 50  # Example: 50 generates a 50x50 grid. Set 100 for 100x100!
MAX_DAILY_CLICKS = 1
DAILY_ENIGMA = "Clue #1: Where the perfect tens cross the age of majority... (Find the winning coordinates)"
LED_SLOTS = 10  # sponsor LED spots around the grid

# Default PRO banner shown when no paid sponsor is active (after expiry or before any purchase).
SPONSOR_PRO = {
    "active": True,
    "name": "@findthepixel_global",
    "message": "Follow us on TikTok!",
    "link": "https://www.tiktok.com/@findthepixel_global",
    "cta_link": "mailto:sponsor@example.com",
}

GOOGLE_CLIENT_ID = os.getenv("GOOGLE_CLIENT_ID")
GOOGLE_CLIENT_SECRET = os.getenv("GOOGLE_CLIENT_SECRET")
STRIPE_SECRET_KEY = os.getenv("STRIPE_SECRET_KEY")
STRIPE_PUBLISHABLE_KEY = os.getenv("STRIPE_PUBLISHABLE_KEY")
STRIPE_PRICE_ID_LED = os.getenv("STRIPE_PRICE_ID_LED")
STRIPE_PRICE_ID_PRO = os.getenv("STRIPE_PRICE_ID_PRO")
STRIPE_WEBHOOK_SECRET = os.getenv("STRIPE_WEBHOOK_SECRET")
stripe.api_key = STRIPE_SECRET_KEY

ADMIN_RESET_KEY = os.getenv("ADMIN_RESET_KEY")
SPONSOR_DURATION = timedelta(hours=24)
DEFAULT_LED_COLORS = ["gold", "azure", "violet"]
HEX_COLOR_RE = re.compile(r"^#[0-9A-Fa-f]{6}$")
# =====================================================================

DB_PATH = "game.db"

app = Flask(__name__)
app.secret_key = os.getenv("FLASK_SECRET_KEY", "change-this-flask-session-secret-key")


def get_client_ip():
    return request.headers.get("X-Forwarded-For", request.remote_addr).split(",")[0].strip()


limiter = Limiter(
    key_func=get_client_ip,
    app=app,
    storage_uri="memory://",
    default_limits=[],
)

oauth = OAuth(app)
google = oauth.register(
    name="google",
    client_id=GOOGLE_CLIENT_ID,
    client_secret=GOOGLE_CLIENT_SECRET,
    server_metadata_url="https://accounts.google.com/.well-known/openid-configuration",
    client_kwargs={"scope": "openid email profile"},
)


def get_daily_winning_coordinate():
    """Deterministic winning pixel for today (same coords across restarts)."""
    seed = int(hashlib.sha256(date.today().isoformat().encode()).hexdigest(), 16) % (2**32)
    rng = random.Random(seed)
    return {"x": rng.randint(1, GRID_SIZE), "y": rng.randint(1, GRID_SIZE)}


winning_coordinate = get_daily_winning_coordinate()


def debug_print_winning_pixel():
    print(
        f'🎯 [DEBUG - TIKTOK] Il pixel vincente di oggi è: '
        f'X={winning_coordinate["x"]}, Y={winning_coordinate["y"]}'
    )


def utc_now_iso():
    return datetime.now(timezone.utc).isoformat()


def default_led_color(slot):
    return DEFAULT_LED_COLORS[slot % len(DEFAULT_LED_COLORS)]


def get_db():
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    return conn


def init_db():
    """Create tables and safely migrate old schemas."""
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
        existing = {row["name"] for row in conn.execute("PRAGMA table_info(users)")}
        for column, definition in (("email", "TEXT"), ("google_id", "TEXT")):
            if column not in existing:
                conn.execute(f"ALTER TABLE users ADD COLUMN {column} {definition}")

        conn.execute("""
            CREATE TABLE IF NOT EXISTS leds (
                slot INTEGER PRIMARY KEY,
                color TEXT NOT NULL,
                owner_username TEXT,
                owner_google_id TEXT,
                link TEXT,
                expires_at TEXT,
                stripe_session_id TEXT
            )
        """)
        led_cols = {row["name"] for row in conn.execute("PRAGMA table_info(leds)")}
        for column, definition in (
            ("link", "TEXT"),
            ("expires_at", "TEXT"),
            ("stripe_session_id", "TEXT"),
        ):
            if column not in led_cols:
                conn.execute(f"ALTER TABLE leds ADD COLUMN {column} {definition}")

        colors = DEFAULT_LED_COLORS
        for slot in range(LED_SLOTS):
            conn.execute(
                "INSERT OR IGNORE INTO leds (slot, color) VALUES (?, ?)",
                (slot, colors[slot % len(colors)]),
            )

        conn.execute("""
            CREATE TABLE IF NOT EXISTS pro_sponsor (
                id INTEGER PRIMARY KEY CHECK (id = 1),
                active INTEGER DEFAULT 0,
                message TEXT,
                link TEXT,
                expires_at TEXT,
                stripe_session_id TEXT
            )
        """)
        conn.execute("INSERT OR IGNORE INTO pro_sponsor (id, active) VALUES (1, 0)")

        conn.execute("""
            CREATE TABLE IF NOT EXISTS processed_stripe_sessions (
                session_id TEXT PRIMARY KEY,
                processed_at TEXT NOT NULL
            )
        """)


def reset_led_slot(conn, slot):
    conn.execute(
        "UPDATE leds SET color = ?, owner_username = NULL, owner_google_id = NULL, "
        "link = NULL, expires_at = NULL, stripe_session_id = NULL WHERE slot = ?",
        (default_led_color(slot), slot),
    )


def reset_pro_sponsor(conn):
    conn.execute(
        "UPDATE pro_sponsor SET active = 0, message = NULL, link = NULL, "
        "expires_at = NULL, stripe_session_id = NULL WHERE id = 1",
    )


def force_reset_all_sponsors():
    """Immediately clear all active LED sponsors and reset PRO sponsor to default."""
    with get_db() as conn:
        for slot in range(LED_SLOTS):
            reset_led_slot(conn, slot)
        reset_pro_sponsor(conn)
        conn.execute("DELETE FROM processed_stripe_sessions")
    logger.info("FORCE RESET: all LED slots cleared, PRO sponsor reset to default, processed sessions wiped")


def expire_stale_sponsorships(conn):
    now = utc_now_iso()
    expired_leds = conn.execute(
        "SELECT slot FROM leds WHERE owner_username IS NOT NULL "
        "AND expires_at IS NOT NULL AND expires_at <= ?",
        (now,),
    ).fetchall()
    for row in expired_leds:
        reset_led_slot(conn, row["slot"])
        logger.info("LED sponsor expired: slot=%s", row["slot"])

    pro = conn.execute("SELECT * FROM pro_sponsor WHERE id = 1").fetchone()
    if pro and pro["active"] and pro["expires_at"] and pro["expires_at"] <= now:
        reset_pro_sponsor(conn)
        logger.info("PRO sponsor expired")


def is_paid_pro_active(conn):
    expire_stale_sponsorships(conn)
    row = conn.execute("SELECT active FROM pro_sponsor WHERE id = 1").fetchone()
    return bool(row and row["active"])


def get_sponsor_pro(conn):
    expire_stale_sponsorships(conn)
    row = conn.execute("SELECT * FROM pro_sponsor WHERE id = 1").fetchone()
    if row and row["active"] and row["message"]:
        return {
            "active": True,
            "name": row["message"],
            "message": row["message"],
            "link": row["link"],
            "paid": True,
        }
    if SPONSOR_PRO.get("active"):
        return {
            "active": True,
            "name": SPONSOR_PRO["name"],
            "message": SPONSOR_PRO["message"],
            "link": SPONSOR_PRO["link"],
            "paid": False,
        }
    return {
        "active": False,
        "cta_link": SPONSOR_PRO.get("cta_link", "#"),
    }


def get_leds(conn):
    expire_stale_sponsorships(conn)
    rows = conn.execute(
        "SELECT slot, color, owner_username, link FROM leds ORDER BY slot",
    ).fetchall()
    return [
        {
            "slot": r["slot"],
            "color": r["color"],
            "owner": r["owner_username"],
            "link": r["link"],
        }
        for r in rows
    ]


def is_session_processed(conn, session_id):
    return conn.execute(
        "SELECT 1 FROM processed_stripe_sessions WHERE session_id = ?",
        (session_id,),
    ).fetchone() is not None


def mark_session_processed(conn, session_id):
    conn.execute(
        "INSERT OR IGNORE INTO processed_stripe_sessions (session_id, processed_at) VALUES (?, ?)",
        (session_id, utc_now_iso()),
    )


def activate_led_sponsor(conn, slot_id, color, custom_text, custom_link, session_id, expires_at):
    conn.execute(
        "UPDATE leds SET color = ?, owner_username = ?, link = ?, "
        "expires_at = ?, stripe_session_id = ? WHERE slot = ?",
        (color, custom_text, custom_link, expires_at, session_id, slot_id),
    )
    logger.info("LED sponsor activated: slot=%s text=%s", slot_id, custom_text)


def activate_pro_sponsor(conn, custom_text, custom_link, session_id, expires_at):
    conn.execute(
        "UPDATE pro_sponsor SET active = 1, message = ?, link = ?, "
        "expires_at = ?, stripe_session_id = ? WHERE id = 1",
        (custom_text, custom_link, expires_at, session_id),
    )
    logger.info("PRO sponsor activated: text=%s", custom_text)


def activate_from_stripe_session(checkout_session):
    """Idempotent activation from a paid Stripe Checkout session."""
    session_id = getattr(checkout_session, "id", None)
    if not session_id or getattr(checkout_session, "payment_status", None) != "paid":
        return False

    raw_metadata = getattr(checkout_session, "metadata", None)
    if raw_metadata and hasattr(raw_metadata, "to_dict"):
        metadata = raw_metadata.to_dict()
    elif isinstance(raw_metadata, dict):
        metadata = raw_metadata
    else:
        metadata = {}
    purchase_type = (metadata.get("type") or "").upper()
    now_utc = datetime.now(timezone.utc)
    expires_at = (now_utc + SPONSOR_DURATION).isoformat()

    with get_db() as conn:
        if is_session_processed(conn, session_id):
            return True

        if purchase_type == "LED":
            try:
                slot_id = int(metadata.get("slot_id"))
            except (TypeError, ValueError):
                logger.error("Invalid slot_id in Stripe metadata: %r", metadata)
                return False
            if not (0 <= slot_id < LED_SLOTS):
                return False

            led = conn.execute(
                "SELECT owner_username FROM leds WHERE slot = ?", (slot_id,),
            ).fetchone()
            if led and led["owner_username"]:
                mark_session_processed(conn, session_id)
                return True

            color = metadata.get("color", "#fbbf24")
            if not HEX_COLOR_RE.match(color):
                color = "#fbbf24"
            custom_text = metadata.get("custom_text", "").strip()
            custom_link = metadata.get("custom_link", "").strip()
            if not custom_text or not custom_link:
                return False

            activate_led_sponsor(
                conn, slot_id, color, custom_text, custom_link, session_id, expires_at,
            )

        elif purchase_type == "PRO":
            if is_paid_pro_active(conn):
                mark_session_processed(conn, session_id)
                return True

            custom_text = metadata.get("custom_text", "").strip()
            custom_link = metadata.get("custom_link", "").strip()
            if not custom_text or not custom_link:
                return False

            try:
                pro_days = int(metadata.get("days", 1))
            except (TypeError, ValueError):
                pro_days = 1
            pro_days = max(1, min(pro_days, 7))
            pro_expires_at = (now_utc + timedelta(hours=pro_days * 24)).isoformat()

            activate_pro_sponsor(conn, custom_text, custom_link, session_id, pro_expires_at)

        else:
            logger.error("Unknown sponsor type in Stripe metadata: %r", purchase_type)
            return False

        mark_session_processed(conn, session_id)
    return True


def validate_sponsor_fields(custom_text, custom_link, color=None):
    custom_text = (custom_text or "").strip()
    custom_link = (custom_link or "").strip()
    if not custom_text or not custom_link:
        return None, "Please fill in all required fields."
    if len(custom_text) > 120:
        custom_text = custom_text[:120]
    if not custom_link.startswith(("http://", "https://")):
        return None, "Destination URL must start with http:// or https://"
    if color is not None:
        color = (color or "#fbbf24").strip()
        if not HEX_COLOR_RE.match(color):
            color = "#fbbf24"
        return {"custom_text": custom_text, "custom_link": custom_link, "color": color}, None
    return {"custom_text": custom_text, "custom_link": custom_link}, None


def generate_ref_code(ip):
    return hashlib.sha256(ip.encode()).hexdigest()[:6].upper()


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
    payment_success = False
    if request.args.get("payment") == "success":
        session_id = request.args.get("session_id", "").strip()
        if session_id and STRIPE_SECRET_KEY:
            try:
                checkout_session = stripe.checkout.Session.retrieve(session_id)
                if activate_from_stripe_session(checkout_session):
                    payment_success = True
            except stripe.StripeError as e:
                logger.error("Stripe session retrieve error: %s", e)
        return redirect(url_for("index", payment_success="1" if payment_success else "0"))

    payment_notice = None
    if request.args.get("payment_success") == "1":
        payment_notice = "Payment successful! Your sponsorship is now live for 24 hours."
    elif request.args.get("payment_success") == "0":
        payment_notice = "Payment received, but sponsorship activation failed. Contact support."

    ip = get_client_ip()
    with get_db() as conn:
        user = get_or_create_user(conn, ip)

        ref = request.args.get("ref", "").strip().upper()
        if ref and ref != user["ref_code"]:
            referrer = conn.execute(
                "SELECT * FROM users WHERE ref_code = ?", (ref,),
            ).fetchone()
            if referrer:
                conn.execute(
                    "UPDATE users SET max_clicks = max_clicks + 2 WHERE ref_code = ?",
                    (ref,),
                )

        user = reset_daily_clicks(conn, user, ip)
        clicks_left = max(user["max_clicks"] - user["clicks_today"], 0)
        leds = get_leds(conn)
        sponsor_pro = get_sponsor_pro(conn)
        pro_purchasable = not is_paid_pro_active(conn)

    return render_template(
        "index.html",
        grid_size=GRID_SIZE,
        current_prize=CURRENT_PRIZE,
        daily_enigma=DAILY_ENIGMA,
        sponsor_pro=sponsor_pro,
        pro_purchasable=pro_purchasable,
        leds_json=json.dumps(leds),
        clicks_left=clicks_left,
        ref_code=user["ref_code"],
        user_email=session.get("email", ""),
        payment_notice=payment_notice,
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


@app.route("/create-checkout-session", methods=["POST"])
@limiter.limit("10 per minute")
def create_checkout_session():
    if not STRIPE_SECRET_KEY:
        return jsonify({"error": "Stripe is not configured."}), 503

    data = request.get_json(silent=True) or {}
    purchase_type = (data.get("type") or "").upper()
    quantity = 1

    if purchase_type == "LED":
        price_id = STRIPE_PRICE_ID_LED
        try:
            slot_id = int(data.get("slot_id"))
        except (TypeError, ValueError):
            return jsonify({"error": "Invalid LED slot."}), 400
        if not (0 <= slot_id < LED_SLOTS):
            return jsonify({"error": "Invalid LED slot."}), 400

        fields, err = validate_sponsor_fields(
            data.get("custom_text"), data.get("custom_link"), data.get("color"),
        )
        if err:
            return jsonify({"error": err}), 400

        with get_db() as conn:
            expire_stale_sponsorships(conn)
            led = conn.execute(
                "SELECT owner_username FROM leds WHERE slot = ?", (slot_id,),
            ).fetchone()
            if led and led["owner_username"]:
                return jsonify({"error": "This LED slot is already taken."}), 409

        metadata = {
            "type": "LED",
            "slot_id": str(slot_id),
            "custom_text": fields["custom_text"],
            "custom_link": fields["custom_link"],
            "color": fields["color"],
        }

    elif purchase_type == "PRO":
        price_id = STRIPE_PRICE_ID_PRO
        fields, err = validate_sponsor_fields(data.get("custom_text"), data.get("custom_link"))
        if err:
            return jsonify({"error": err}), 400

        try:
            days = int(data.get("days", 1))
        except (TypeError, ValueError):
            days = 1
        days = max(1, min(days, 7))

        with get_db() as conn:
            if is_paid_pro_active(conn):
                return jsonify({"error": "PRO Sponsor spot is already taken."}), 409

        metadata = {
            "type": "PRO",
            "custom_text": fields["custom_text"],
            "custom_link": fields["custom_link"],
            "days": str(days),
        }
        quantity = days

    else:
        return jsonify({"error": "Invalid purchase type."}), 400

    if not price_id:
        return jsonify({"error": "Stripe price ID is not configured."}), 503

    success_url = (
        url_for("index", _external=True)
        + "?payment=success&session_id={CHECKOUT_SESSION_ID}"
    )

    try:
        line_items = [{"price": price_id, "quantity": quantity if purchase_type == "PRO" else 1}]
        checkout_session = stripe.checkout.Session.create(
            payment_method_types=["card"],
            line_items=line_items,
            mode="payment",
            success_url=success_url,
            cancel_url=url_for("index", _external=True) + "?payment=cancelled",
            metadata=metadata,
        )
        return jsonify({"url": checkout_session.url})
    except stripe.StripeError as e:
        logger.error("Stripe checkout error: %s", e)
        return jsonify({"error": "Payment service unavailable. Please try again later."}), 502


@app.route("/webhook", methods=["POST"])
def stripe_webhook():
    if not STRIPE_WEBHOOK_SECRET:
        return jsonify({"error": "Webhook secret not configured."}), 503

    payload = request.get_data()
    sig_header = request.headers.get("Stripe-Signature", "")

    try:
        event = stripe.Webhook.construct_event(payload, sig_header, STRIPE_WEBHOOK_SECRET)
    except ValueError:
        logger.warning("Webhook: invalid payload")
        return jsonify({"error": "Invalid payload."}), 400
    except stripe.SignatureVerificationError:
        logger.warning("Webhook: invalid signature")
        return jsonify({"error": "Invalid signature."}), 400

    if event.type == "checkout.session.completed":
        checkout_session = event.data.object
        activate_from_stripe_session(checkout_session)

    return jsonify({"received": True}), 200


@app.errorhandler(429)
def rate_limit_exceeded(e):
    logger.warning("SECURITY: rate limit exceeded by IP %s on %s", get_client_ip(), request.path)
    return jsonify({
        "allowed": False,
        "win": False,
        "message": "Too many attempts. Please slow down and try again in a minute.",
    }), 429


@app.route("/check_pixel", methods=["POST"])
@limiter.limit("5 per minute")
def check_pixel():
    data = request.get_json(silent=True) or {}
    try:
        x = int(data.get("x"))
        y = int(data.get("y"))
    except (TypeError, ValueError):
        logger.warning(
            "SECURITY: malformed coordinates from IP %s: payload=%r",
            get_client_ip(), data,
        )
        return jsonify({"allowed": False, "win": False, "message": "Invalid coordinate."}), 400

    if not (1 <= x <= GRID_SIZE and 1 <= y <= GRID_SIZE):
        logger.warning(
            "SECURITY: out-of-grid coordinates from IP %s: x=%s y=%s (grid is %sx%s)",
            get_client_ip(), x, y, GRID_SIZE, GRID_SIZE,
        )
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


# =====================================================================
# ADMIN — hidden reset route, protected by $ADMIN_RESET_KEY env var
# =====================================================================
@app.route("/admin/reset-sponsors", methods=["POST"])
def admin_reset_sponsors():
    """Immediately wipe all LED activations, reset PRO sponsor to @findthepixel_global default,
    and clear processed Stripe session history. Requires X-Admin-Key header matching ADMIN_RESET_KEY."""
    if not ADMIN_RESET_KEY:
        return jsonify({"error": "Admin reset is not configured (ADMIN_RESET_KEY not set)."}), 503

    key = request.headers.get("X-Admin-Key", "")
    if key != ADMIN_RESET_KEY:
        return jsonify({"error": "Forbidden"}), 403

    force_reset_all_sponsors()
    return jsonify({"status": "ok", "message": "All sponsors have been reset to defaults."}), 200


init_db()
debug_print_winning_pixel()

if __name__ == "__main__":
    # CLI flag: python app.py --reset
    if "--reset" in sys.argv:
        print("⚠️  --reset flag detected: forcing sponsor reset immediately...")
        force_reset_all_sponsors()
        print("✅ All sponsors reset to defaults. Starting server normally.")
    app.run(debug=True)