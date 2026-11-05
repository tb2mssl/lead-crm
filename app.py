"""Lead CRM: Flask + SQLite backend. Users sign up themselves; the admin has a separate login and controls."""
import hashlib
import hmac
import io
import re
import smtplib
import socket
from email.message import EmailMessage
import os
import secrets
import sqlite3
import time
from datetime import date, timedelta
from pathlib import Path

from functools import wraps

from markupsafe import escape
from flask import Flask, g, jsonify, redirect, render_template, request, send_file, session, url_for
from openpyxl import Workbook
from openpyxl.styles import Alignment, Font, PatternFill
from openpyxl.utils import get_column_letter
from werkzeug.middleware.proxy_fix import ProxyFix
from werkzeug.security import check_password_hash, generate_password_hash

BASE = Path(__file__).parent
DB = BASE / "crm.db"

STATUSES = ["New", "Contacted", "Qualified", "Proposal", "Won", "Lost"]
SOURCES = ["Website", "Referral", "LinkedIn", "Cold call", "Event", "Email campaign"]
CURRENCY = "$"  # change to your currency symbol
FIELDS = ("name", "company", "email", "phone", "source", "status", "value", "notes")

app = Flask(__name__)

# ---------- login settings (all come from environment variables) ----------
# SECRET_KEY        signs the login cookie. Set it, or logins break when the server restarts.
# ADMIN_USERNAME    the admin's unique ID. Reserved: nobody can sign up with it. Admin logs in at /admin/login.
# ADMIN_PASSWORD    the password (or ADMIN_PASSWORD_HASH, a werkzeug hash, if you prefer).
# INGEST_API_KEY    optional. Lets webhooks/automations POST new leads without a login.
if not os.environ.get("SECRET_KEY"):
    print("WARNING: SECRET_KEY is not set. Using a temporary key; everyone is logged out on restart.")
app.config.update(
    SECRET_KEY=os.environ.get("SECRET_KEY") or secrets.token_hex(32),
    SESSION_COOKIE_HTTPONLY=True,
    SESSION_COOKIE_SAMESITE="Lax",
    SESSION_COOKIE_SECURE=bool(os.environ.get("RENDER")) or os.environ.get("COOKIE_SECURE") == "1",
    PERMANENT_SESSION_LIFETIME=timedelta(minutes=15),  # also enforced per request, see idle_timeout()
)
if os.environ.get("RENDER"):  # behind Render's proxy: use the real client IP
    app.wsgi_app = ProxyFix(app.wsgi_app, x_for=1, x_proto=1)

ADMIN_USER = os.environ.get("ADMIN_USERNAME", "").strip()
ADMIN_HASH = os.environ.get("ADMIN_PASSWORD_HASH") or (
    generate_password_hash(os.environ["ADMIN_PASSWORD"]) if os.environ.get("ADMIN_PASSWORD") else "")
INGEST_KEY = os.environ.get("INGEST_API_KEY", "")


# ---------- email settings ----------
# The website sends its own email through YOUR email provider's SMTP server (Gmail, Outlook, Zoho, your
# hosting company's mailbox...). No third-party mailing service is used. Set these environment variables:
#   SMTP_HOST      e.g. smtp.gmail.com
#   SMTP_PORT      587 (STARTTLS, default) or 465 (SSL)
#   SMTP_USER      the mailbox login, usually the full email address
#   SMTP_PASSWORD  the mailbox password (Gmail and many providers need an "app password")
#   MAIL_FROM      sender address (defaults to SMTP_USER); MAIL_FROM_NAME is optional
#   BASE_URL       optional, e.g. https://lead-crm-wug0.onrender.com (used for the links inside emails)
#   MAIL_CONSOLE=1 local testing only: prints emails in the server log instead of sending them
SMTP_HOST = os.environ.get("SMTP_HOST", "").strip()
SMTP_PORT = int(os.environ.get("SMTP_PORT", "587") or 587)
MAIL_FROM = os.environ.get("MAIL_FROM", "").strip() or os.environ.get("SMTP_USER", "").strip()
MAIL_FROM_NAME = os.environ.get("MAIL_FROM_NAME", "Lead pipeline")
MAIL_CONSOLE = os.environ.get("MAIL_CONSOLE") == "1"
VERIFY_HOURS = 24  # signup confirmation link
RESET_HOURS = 1  # forgot-password link
IDLE_SECONDS = 15 * 60  # logged out after 15 minutes without activity


def mail_ready():
    return MAIL_CONSOLE or bool(SMTP_HOST and MAIL_FROM)


def send_mail(to, subject, text, html):
    """Send one email through the configured SMTP server. Raises on failure."""
    if MAIL_CONSOLE and not SMTP_HOST:
        print(f"[MAIL_CONSOLE] To: {to}\nSubject: {subject}\n{text}\n")
        return
    msg = EmailMessage()
    msg["From"], msg["To"], msg["Subject"] = f"{MAIL_FROM_NAME} <{MAIL_FROM}>", to, subject
    msg.set_content(text)
    msg.add_alternative(html, subtype="html")
    smtp = (smtplib.SMTP_SSL(SMTP_HOST, SMTP_PORT, timeout=15) if SMTP_PORT == 465
            else smtplib.SMTP(SMTP_HOST, SMTP_PORT, timeout=15))
    with smtp:
        if SMTP_PORT != 465:
            smtp.starttls()
        if os.environ.get("SMTP_USER"):
            smtp.login(os.environ["SMTP_USER"], os.environ.get("SMTP_PASSWORD", ""))
        smtp.send_message(msg)


def explain_mail_error(exc):
    """Plain-language reason for a failed send (shown to the admin only)."""
    if isinstance(exc, smtplib.SMTPAuthenticationError):
        return "The mail server rejected the login. Check SMTP_USER and SMTP_PASSWORD (Gmail and many providers need an app password)."
    if isinstance(exc, smtplib.SMTPRecipientsRefused):
        return "The mail server refused the recipient address."
    if isinstance(exc, smtplib.SMTPSenderRefused):
        return "The mail server refused the sender address. MAIL_FROM must be an address this mailbox may send from."
    if isinstance(exc, smtplib.SMTPException):
        return f"The mail server reported: {exc}"
    if isinstance(exc, socket.gaierror):
        return "Could not find the mail server. Check SMTP_HOST."
    if isinstance(exc, OSError):
        return (f"Could not connect to the mail server on port {SMTP_PORT} (timed out or refused). Check SMTP_HOST and SMTP_PORT. "
                "Some hosts, including Render's free plan, block outbound SMTP ports.")
    return f"{type(exc).__name__}: {exc}"


# ---------- database ----------
def init_db():
    con = sqlite3.connect(DB)
    con.executescript((BASE / "schema.sql").read_text())
    if "approved" not in [r[1] for r in con.execute("PRAGMA table_info(users)")]:
        # database from before admin approval existed: everyone already registered stays approved
        con.execute("ALTER TABLE users ADD COLUMN approved INTEGER NOT NULL DEFAULT 1")
    if con.execute("SELECT COUNT(*) FROM leads").fetchone()[0] == 0:  # 500 sample leads on a fresh database
        con.executescript((BASE / "seed.sql").read_text())
    con.commit()
    con.close()


def db():
    if "db" not in g:
        g.db = sqlite3.connect(DB)
        g.db.row_factory = sqlite3.Row
    return g.db


@app.teardown_appcontext
def close_db(_exc):
    con = g.pop("db", None)
    if con:
        con.close()


def clean(data, partial=False):
    """Validate and normalise incoming lead data. Only whitelisted fields pass."""
    out = {k: ("" if data[k] is None else str(data[k]).strip()) for k in FIELDS if k in data}
    if "value" in out:
        try:
            out["value"] = max(0.0, float(out["value"] or 0))
        except ValueError:
            raise ValueError("Value must be a number.")
    if "status" in out and out["status"] not in STATUSES:
        raise ValueError("Unknown status.")
    if "source" in out and out["source"] not in SOURCES:
        raise ValueError("Unknown source.")
    if ("name" in out and not out["name"]) or (not partial and not out.get("name")):
        raise ValueError("Name is required.")
    return out


def get_lead(lead_id):
    row = db().execute("SELECT * FROM leads WHERE id = ?", (lead_id,)).fetchone()
    return dict(row) if row else None


# ---------- login ----------
FAILS = {}  # "kind:ip" -> (failed attempts, time of first failure)
SIGNUPS = {}  # ip -> list of signup timestamps
MAX_FAILS, LOCK_SECONDS = 5, 15 * 60
MAX_SIGNUPS_PER_HOUR = 10
USERNAME_RE = re.compile(r"^[A-Za-z0-9_.-]{3,30}$")
EMAIL_RE = re.compile(r"^[^@\s]+@[^@\s]+\.[^@\s]+$")
PHONE_RE = re.compile(r"^[0-9+()\-\s]{7,20}$")


def same(a, b):
    return hmac.compare_digest(a.encode(), b.encode())


def pw_stamp(password_hash):
    return hashlib.sha256(password_hash.encode()).hexdigest()[:16]


def role():
    """'admin', 'user' or None. Admin and users are separate identities."""
    if ADMIN_USER and session.get("admin") == ADMIN_USER:
        return "admin"
    uid = session.get("uid")
    if uid:
        row = db().execute("SELECT approved, password_hash FROM users WHERE id = ?", (uid,)).fetchone()
        # Removed, un-approved or password-changed accounts are logged out on their next request.
        if row and row["approved"] and same(session.get("pv", ""), pw_stamp(row["password_hash"])):
            return "user"
    return None


def logged_in():
    return role() is not None


def admin_only(fn):
    @wraps(fn)
    def wrapper(*a, **kw):
        if role() != "admin":
            if request.path.startswith("/api/"):
                return jsonify(error="Admin access only."), 403
            return redirect(url_for("admin_login"))
        return fn(*a, **kw)
    return wrapper


def locked(key):
    n, t = FAILS.get(key, (0, 0))
    if n and time.time() - t > LOCK_SECONDS:
        FAILS.pop(key, None)
        return False
    return n >= MAX_FAILS


def record_fail(key):
    n, t = FAILS.get(key, (0, time.time()))
    FAILS[key] = (n + 1, t)


def safe_next(target):
    """Only allow redirects back into this site."""
    if target and target.startswith("/") and not target.startswith("//") and "\\" not in target:
        return target
    return url_for("index")


@app.before_request
def idle_timeout():
    """Log out anyone (user or admin) who has been inactive for 15 minutes; otherwise record this activity."""
    if request.endpoint == "static":
        return None
    if session.get("uid") or session.get("admin"):
        if time.time() - session.get("la", 0) > IDLE_SECONDS:
            session.clear()
        else:
            session["la"] = time.time()
    return None


PUBLIC = ("static", "login", "signup", "verify", "resend", "forgot", "reset", "admin_login")


@app.before_request
def require_login():
    if request.endpoint in PUBLIC:
        return None
    if logged_in():
        changes = request.method not in ("GET", "HEAD", "OPTIONS")
        # Extra CSRF guard: data-changing calls must come from our own page's JavaScript.
        if changes and request.endpoint != "logout" and request.headers.get("X-Requested-With") != "fetch":
            return jsonify(error="Bad request."), 400
        return None
    # Automations (webhooks, Zapier, etc.) may only create leads, and only with the API key.
    if (INGEST_KEY and request.endpoint == "create_lead" and request.method == "POST"
            and same(request.headers.get("X-API-Key", ""), INGEST_KEY)):
        return None
    if request.path.startswith("/api/"):
        return jsonify(error="Please log in."), 401
    if request.path.startswith("/admin"):
        return redirect(url_for("admin_login"))  # admin pages always go to the admin's own login
    return redirect(url_for("login", next=request.path))


@app.after_request
def no_store(resp):
    if request.endpoint != "static":  # stops the back button showing data after logout
        resp.headers["Cache-Control"] = "no-store"
    return resp


def start_session(**ident):
    session.clear()
    session.permanent = True
    session.update(ident, la=time.time())


NOTICES = {
    "verified": "Email confirmed. Your account now needs approval from the admin. We will email you once it is approved.",
    "reset": "Password updated. Please log in with your new password.",
    "idle": "You were logged out after 15 minutes of inactivity. Please log in again.",
}


@app.route("/login", methods=["GET", "POST"])
def login():
    nxt = safe_next(request.values.get("next"))
    if logged_in():
        return redirect(nxt)
    error = None
    if request.method == "POST":
        key = "user:" + (request.remote_addr or "unknown")
        if locked(key):
            error = "Too many failed attempts. Try again in 15 minutes."
        else:
            who = request.form.get("username", "").strip()
            row = db().execute("SELECT * FROM users WHERE username = ? OR email = ?", (who, who)).fetchone()
            if row and check_password_hash(row["password_hash"], request.form.get("password", "")):
                if not row["approved"]:
                    error = ("Your email is confirmed, but the admin has not approved your account yet. "
                             "We will email you once it is approved.")
                else:
                    FAILS.pop(key, None)
                    start_session(uid=row["id"], name=row["username"], pv=pw_stamp(row["password_hash"]))
                    return redirect(nxt)
            else:
                record_fail(key)
                error = "Wrong username or password."
    return render_template("login.html", error=error, next=nxt, notice=NOTICES.get(request.args.get("n")))


# ---------- email helpers ----------
def token_hash(token):
    return hashlib.sha256(token.encode()).hexdigest()


def abs_url(endpoint, **values):
    base = os.environ.get("BASE_URL", "").rstrip("/")
    return base + url_for(endpoint, **values) if base else url_for(endpoint, _external=True, **values)


def send_action(to, name, subject, intro, button, link, footer):
    """An email with one big button/link."""
    text = f"Hi {name},\n\n{intro}\n{link}\n\n{footer}"
    html = (f"<p>Hi {escape(name)},</p><p>{escape(intro)}</p>"
            f'<p><a href="{escape(link)}">{escape(button)}</a></p>'
            f"<p>Or paste this link into your browser:<br>{escape(link)}</p><p>{escape(footer)}</p>")
    send_mail(to, subject, text, html)


def try_send(*args):
    """send_action that never breaks the page: returns True/False and logs the reason."""
    try:
        send_action(*args)
        return True
    except Exception as exc:
        app.logger.error("Could not send email: %s", exc)
        return False


def rate_ok(ip, bucket="mail"):
    key = f"{bucket}:{ip}"
    SIGNUPS[key] = [t for t in SIGNUPS.get(key, []) if time.time() - t < 3600]
    return len(SIGNUPS[key]) < MAX_SIGNUPS_PER_HOUR


def rate_hit(ip, bucket="mail"):
    SIGNUPS.setdefault(f"{bucket}:{ip}", []).append(time.time())


def password_error(pw, pw2):
    if len(pw) < 8:
        return "Password must be at least 8 characters."
    if pw != pw2:
        return "The two passwords do not match."
    return None


def send_verification(username, email, token):
    send_action(email, username, "Confirm your email - Lead pipeline",
                "Confirm your email to finish creating your Lead pipeline account:", "Confirm my email",
                abs_url("verify", token=token),
                f"This link works for {VERIFY_HOURS} hours. If you did not sign up, ignore this email. "
                "After you confirm, the admin still needs to approve your account before you can log in.")


# ---------- sign up -> confirm email -> admin approves ----------
@app.route("/signup", methods=["GET", "POST"])
def signup():
    if logged_in():
        return redirect(url_for("index"))
    error, form = None, {}
    if request.method == "POST":
        form = {k: request.form.get(k, "").strip() for k in ("username", "email", "phone")}
        pw, pw2 = request.form.get("password", ""), request.form.get("confirm", "")
        ip = request.remote_addr or "unknown"
        con = db()
        con.execute("DELETE FROM pending_signups WHERE created_at < ?", (time.time() - VERIFY_HOURS * 3600,))
        if not mail_ready():
            error = "Email is not set up on the server yet, so sign-up is unavailable. Please contact the admin."
        elif not rate_ok(ip):
            error = "Too many requests from this address. Try again later."
        elif not USERNAME_RE.match(form["username"]):
            error = "Username must be 3-30 characters: letters, numbers, dot, dash or underscore."
        elif ADMIN_USER and form["username"].lower() == ADMIN_USER.lower():
            error = "That username is reserved. Please choose another."
        elif not EMAIL_RE.match(form["email"]):
            error = "Enter a valid email address."
        elif form["phone"] and not PHONE_RE.match(form["phone"]):
            error = "Enter a valid phone number or leave it blank."
        elif password_error(pw, pw2):
            error = password_error(pw, pw2)
        elif con.execute("SELECT 1 FROM users WHERE username = ? OR email = ?", (form["username"], form["email"])).fetchone():
            error = "That username or email is already registered."
        elif con.execute("SELECT 1 FROM pending_signups WHERE username = ? AND email != ?",
                         (form["username"], form["email"])).fetchone():
            error = "That username is waiting to be confirmed by someone else. Please choose another."
        else:
            token = secrets.token_urlsafe(32)
            # signing up again with the same email replaces the earlier unconfirmed attempt
            con.execute("DELETE FROM pending_signups WHERE email = ?", (form["email"],))
            con.execute("INSERT INTO pending_signups (token_hash, username, email, phone, password_hash, created_at) "
                        "VALUES (?, ?, ?, ?, ?, ?)",
                        (token_hash(token), form["username"], form["email"], form["phone"],
                         generate_password_hash(pw), time.time()))
            con.commit()
            try:
                send_verification(form["username"], form["email"], token)
            except Exception as exc:  # network error, bad API key, unverified sender, ...
                app.logger.error("Could not send verification email: %s", exc)
                con.execute("DELETE FROM pending_signups WHERE token_hash = ?", (token_hash(token),))
                con.commit()
                error = "We could not send the verification email. Please check the address and try again shortly."
            else:
                rate_hit(ip)
                return render_template("check_email.html", email=form["email"], resent=False)
        con.commit()
    return render_template("signup.html", error=error, form=form)


@app.post("/resend")
def resend():
    """Send a fresh link for an unconfirmed sign-up. The answer is the same whether or not the email exists."""
    email = request.form.get("email", "").strip()
    ip = request.remote_addr or "unknown"
    con = db()
    row = con.execute("SELECT * FROM pending_signups WHERE email = ? AND created_at >= ?",
                      (email, time.time() - VERIFY_HOURS * 3600)).fetchone()
    if row and mail_ready() and rate_ok(ip):
        token = secrets.token_urlsafe(32)
        con.execute("UPDATE pending_signups SET token_hash = ? WHERE id = ?", (token_hash(token), row["id"]))
        con.commit()
        rate_hit(ip)
        try:
            send_verification(row["username"], row["email"], token)
        except Exception as exc:
            app.logger.error("Could not resend verification email: %s", exc)
    return render_template("check_email.html", email=email, resent=True)


@app.get("/verify/<token>")
def verify(token):
    """The link in the email. Creates the account, which then waits for the admin to approve it."""
    con = db()
    row = con.execute("SELECT * FROM pending_signups WHERE token_hash = ?", (token_hash(token),)).fetchone()
    if not row or row["created_at"] < time.time() - VERIFY_HOURS * 3600:
        return render_template("login.html", next=url_for("index"), notice=None,
                               error="This confirmation link is invalid or has expired. Please sign up again.")
    try:
        con.execute("INSERT INTO users (username, email, phone, password_hash, approved) VALUES (?, ?, ?, ?, 0)",
                    (row["username"], row["email"], row["phone"], row["password_hash"]))
    except sqlite3.IntegrityError:  # someone registered the same username/email in the meantime
        con.execute("DELETE FROM pending_signups WHERE id = ?", (row["id"],))
        con.commit()
        return render_template("login.html", next=url_for("index"), notice=None,
                               error="That username or email is already registered. Please log in or sign up with another.")
    con.execute("DELETE FROM pending_signups WHERE id = ?", (row["id"],))
    con.commit()
    return redirect(url_for("login", n="verified"))


# ---------- forgot password ----------
def new_reset_token(con, user_id, hours):
    token = secrets.token_urlsafe(32)
    con.execute("DELETE FROM password_resets WHERE user_id = ?", (user_id,))  # newest link only
    con.execute("INSERT INTO password_resets (token_hash, user_id, expires_at) VALUES (?, ?, ?)",
                (token_hash(token), user_id, time.time() + hours * 3600))
    con.commit()
    return token


def reset_row(token):
    return db().execute(
        "SELECT r.id, r.user_id, u.username FROM password_resets r JOIN users u ON u.id = r.user_id "
        "WHERE r.token_hash = ? AND r.expires_at >= ?", (token_hash(token), time.time())).fetchone()


@app.route("/forgot", methods=["GET", "POST"])
def forgot():
    if logged_in():
        return redirect(url_for("index"))
    if request.method == "POST":
        email = request.form.get("email", "").strip()
        ip = request.remote_addr or "unknown"
        if not mail_ready():
            return render_template("forgot.html", sent=False, email="",
                                   error="Email is not set up on the server, so passwords cannot be reset. Please contact the admin.")
        if not rate_ok(ip):
            return render_template("forgot.html", sent=False, email="", error="Too many requests. Try again later.")
        rate_hit(ip)
        con = db()
        con.execute("DELETE FROM password_resets WHERE expires_at < ?", (time.time(),))
        row = con.execute("SELECT id, username, email FROM users WHERE email = ?", (email,)).fetchone()
        if row:
            token = new_reset_token(con, row["id"], RESET_HOURS)
            try_send(row["email"], row["username"], "Reset your password - Lead pipeline",
                     "We received a request to reset your password. Click the link to choose a new one:",
                     "Reset my password", abs_url("reset", token=token),
                     f"This link works for {RESET_HOURS} hour and can be used once. If you did not ask for it, "
                     "ignore this email and your password stays the same.")
        # same answer whether or not the email is registered
        return render_template("forgot.html", sent=True, email=email, error=None)
    return render_template("forgot.html", sent=False, email="", error=None)


@app.route("/reset/<token>", methods=["GET", "POST"])
def reset(token):
    row = reset_row(token)
    if not row:
        return render_template("forgot.html", sent=False, email="",
                               error="This reset link is invalid or has expired. Please request a new one.")
    error = None
    if request.method == "POST":
        pw, pw2 = request.form.get("password", ""), request.form.get("confirm", "")
        error = password_error(pw, pw2)
        if not error:
            con = db()
            con.execute("UPDATE users SET password_hash = ? WHERE id = ?", (generate_password_hash(pw), row["user_id"]))
            con.execute("DELETE FROM password_resets WHERE user_id = ?", (row["user_id"],))
            con.commit()  # the new password also logs the account out everywhere else (see role())
            return redirect(url_for("login", n="reset"))
    return render_template("reset.html", token=token, username=row["username"], error=error)


@app.route("/admin/login", methods=["GET", "POST"])
def admin_login():
    if role() == "admin":
        return redirect(url_for("admin_page"))
    error = None
    if not (ADMIN_USER and ADMIN_HASH):
        error = "Admin login is not set up. Set ADMIN_USERNAME and ADMIN_PASSWORD on the server."
    elif request.method == "POST":
        key = "admin:" + (request.remote_addr or "unknown")
        if locked(key):
            error = "Too many failed attempts. Try again in 15 minutes."
        else:
            user_ok = same(request.form.get("username", "").strip(), ADMIN_USER)
            pass_ok = check_password_hash(ADMIN_HASH, request.form.get("password", ""))
            if user_ok and pass_ok:
                FAILS.pop(key, None)
                start_session(admin=ADMIN_USER, name=ADMIN_USER)
                return redirect(url_for("admin_page"))
            record_fail(key)
            error = "Wrong admin ID or password."
    return render_template("admin_login.html", error=error, notice=NOTICES.get(request.args.get("n")))


@app.post("/logout")
def logout():
    was_admin = role() == "admin"
    session.clear()
    return redirect(url_for("admin_login" if was_admin else "login"))


# ---------- admin ----------
@app.get("/admin")
@admin_only
def admin_page():
    if SMTP_HOST and MAIL_FROM:
        mail = f"Email is set up: {SMTP_HOST}:{SMTP_PORT}, sending as {MAIL_FROM}."
    elif MAIL_CONSOLE:
        mail = "Email is in test mode: messages are printed in the server log, not sent."
    else:
        mail = "Email is not set up, so sign-up and forgot-password are switched off. You can still add users below."
    return render_template("admin.html", admin=ADMIN_USER, mail=mail)


def fmt_ts(ts):
    return time.strftime("%Y-%m-%d %H:%M", time.gmtime(ts))


def username_taken(con, name):
    return bool((ADMIN_USER and name.lower() == ADMIN_USER.lower())
                or con.execute("SELECT 1 FROM users WHERE username = ?", (name,)).fetchone()
                or con.execute("SELECT 1 FROM pending_signups WHERE username = ?", (name,)).fetchone())


def derive_username(con, email):
    base = re.sub(r"[^A-Za-z0-9_.-]", "", email.split("@")[0])[:24]
    if len(base) < 3:
        base = (base + "user")[:24]
    name, n = base, 1
    while username_taken(con, name):
        n += 1
        name = f"{base}{n}"
    return name


@app.get("/api/admin/users")
@admin_only
def admin_users():
    con = db()
    rows = [dict(r) for r in con.execute(
        "SELECT id, username, email, phone, created_at, approved FROM users ORDER BY id DESC")]
    unconfirmed = [{"id": r["id"], "username": r["username"], "email": r["email"], "phone": r["phone"],
                    "created_at": fmt_ts(r["created_at"])}
                   for r in con.execute("SELECT * FROM pending_signups ORDER BY id DESC")]
    return jsonify(waiting=[u for u in rows if not u["approved"]], users=[u for u in rows if u["approved"]],
                   unconfirmed=unconfirmed)


@app.post("/api/admin/users")
@admin_only
def admin_add_user():
    """Admin creates an account directly on the website. It is active at once; no email is needed."""
    data = request.get_json(silent=True) or {}
    email, username = str(data.get("email", "")).strip(), str(data.get("username", "")).strip()
    password = str(data.get("password", ""))
    con = db()
    if not EMAIL_RE.match(email):
        return jsonify(error="Enter a valid email address."), 400
    if len(password) < 8:
        return jsonify(error="Password must be at least 8 characters."), 400
    if con.execute("SELECT 1 FROM users WHERE email = ?", (email,)).fetchone():
        return jsonify(error="That email is already registered."), 409
    if not username:
        username = derive_username(con, email)
    elif not USERNAME_RE.match(username):
        return jsonify(error="Username must be 3-30 characters: letters, numbers, dot, dash or underscore."), 400
    elif username_taken(con, username):
        return jsonify(error="That username is taken or reserved."), 409
    con.execute("DELETE FROM pending_signups WHERE email = ?", (email,))
    cur = con.execute("INSERT INTO users (username, email, phone, password_hash, approved) VALUES (?, ?, '', ?, 1)",
                      (username, email, generate_password_hash(password)))
    con.commit()
    return jsonify(id=cur.lastrowid, username=username, email=email), 201


@app.post("/api/admin/test-email")
@admin_only
def admin_test_email():
    """Lets the admin check the SMTP settings from the website."""
    to = str((request.get_json(silent=True) or {}).get("email", "")).strip()
    if not EMAIL_RE.match(to):
        return jsonify(error="Enter a valid email address to send the test to."), 400
    if not mail_ready():
        return jsonify(error="Email is not set up. Add SMTP_HOST, SMTP_USER, SMTP_PASSWORD and MAIL_FROM on the server."), 400
    try:
        send_mail(to, "Test email - Lead pipeline", "This is a test email from your Lead pipeline website. Email is working.",
                  "<p>This is a test email from your Lead pipeline website. Email is working.</p>")
    except Exception as exc:
        app.logger.error("Test email failed: %s", exc)
        return jsonify(error=explain_mail_error(exc)), 502
    return jsonify(ok=True)


@app.get("/api/ping")
def ping():
    """Keeps a session alive while the person is active on a page (the idle check runs before this)."""
    return "", 204


@app.post("/api/admin/users/<int:user_id>/approve")
@admin_only
def admin_approve_user(user_id):
    con = db()
    row = con.execute("SELECT * FROM users WHERE id = ?", (user_id,)).fetchone()
    if not row:
        return jsonify(error="User not found."), 404
    con.execute("UPDATE users SET approved = 1 WHERE id = ?", (user_id,))
    con.commit()
    emailed = mail_ready() and try_send(row["email"], row["username"], "Your account is approved - Lead pipeline",
                                        "Good news: the admin approved your account. You can log in now:",
                                        "Log in", abs_url("login"), "Welcome aboard.")
    return jsonify(ok=True, emailed=bool(emailed))


@app.delete("/api/admin/users/<int:user_id>")
@admin_only
def admin_delete_user(user_id):
    con = db()
    con.execute("DELETE FROM password_resets WHERE user_id = ?", (user_id,))
    cur = con.execute("DELETE FROM users WHERE id = ?", (user_id,))
    con.commit()
    if cur.rowcount == 0:
        return jsonify(error="User not found."), 404
    return "", 204


@app.delete("/api/admin/pending/<int:pending_id>")
@admin_only
def admin_delete_pending(pending_id):
    cur = db().execute("DELETE FROM pending_signups WHERE id = ?", (pending_id,))
    db().commit()
    if cur.rowcount == 0:
        return jsonify(error="Sign-up not found."), 404
    return "", 204


# ---------- pages ----------
@app.get("/")
def index():
    return render_template(
        "index.html",
        cfg={"statuses": STATUSES, "sources": SOURCES, "currency": CURRENCY,
             "user": session.get("name", ""), "role": role(),
             "waiting": (db().execute("SELECT COUNT(*) FROM users WHERE approved = 0").fetchone()[0]
                         if role() == "admin" else 0)})


# ---------- API ----------
@app.get("/api/leads")
def list_leads():
    rows = db().execute("SELECT * FROM leads ORDER BY created_at DESC, id DESC").fetchall()
    return jsonify([dict(r) for r in rows])


def filtered_leads():
    """Leads matching the optional ?q= ?status= ?source= ?date_from= ?date_to= filters (same ones the table uses)."""
    where, args = [], []
    q = request.args.get("q", "").strip().lower()
    if q:
        where.append("(lower(name) LIKE ? OR lower(company) LIKE ? OR lower(email) LIKE ?)")
        args += [f"%{q}%"] * 3
    for col, allowed in (("status", STATUSES), ("source", SOURCES)):
        val = request.args.get(col, "").strip()
        if val in allowed:
            where.append(f"{col} = ?")
            args.append(val)
    for param, op in (("date_from", ">="), ("date_to", "<=")):
        val = request.args.get(param, "").strip()
        if re.fullmatch(r"\d{4}-\d{2}-\d{2}", val):
            where.append(f"created_at {op} ?")
            args.append(val)
    sql = "SELECT * FROM leads" + (" WHERE " + " AND ".join(where) if where else "") + " ORDER BY created_at DESC, id DESC"
    return db().execute(sql, args).fetchall()


def excel_safe(v):
    """Stop text from being treated as a formula when the file is opened in Excel."""
    return "'" + v if isinstance(v, str) and v[:1] in ("=", "@") else v


@app.get("/api/leads/export.xlsx")
def export_leads():
    rows = filtered_leads()
    wb = Workbook()
    ws = wb.active
    ws.title = "Leads"
    heads = ["ID", "Name", "Company", "Email", "Phone", "Source", "Status", f"Value ({CURRENCY})", "Notes", "Added"]
    ws.append(heads)
    for r in rows:
        try:
            added = date.fromisoformat(r["created_at"][:10])
        except ValueError:
            added = r["created_at"]
        ws.append([r["id"], *(excel_safe(r[k]) for k in ("name", "company", "email", "phone", "source", "status")),
                   r["value"], excel_safe(r["notes"]), added])
    for c in ws[1]:
        c.font = Font(bold=True, color="FFFFFF")
        c.fill = PatternFill("solid", fgColor="22324A")
        c.alignment = Alignment(vertical="center")
    for i, w in enumerate([7, 22, 26, 34, 16, 16, 12, 14, 38, 12], 1):
        ws.column_dimensions[get_column_letter(i)].width = w
    for r in range(2, ws.max_row + 1):
        ws.cell(r, 8).number_format = "#,##0"
        ws.cell(r, 10).number_format = "yyyy-mm-dd"
    ws.freeze_panes = "A2"
    ws.auto_filter.ref = f"A1:J{max(ws.max_row, 2)}"  # filter dropdowns on every column in Excel
    buf = io.BytesIO()
    wb.save(buf)
    buf.seek(0)
    return send_file(buf, as_attachment=True, download_name=f"leads-{date.today().isoformat()}.xlsx",
                     mimetype="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet")


@app.post("/api/leads")
def create_lead():
    try:
        data = clean(request.get_json(force=True))
    except ValueError as e:
        return jsonify(error=str(e)), 400
    cols = ", ".join(data)
    marks = ", ".join("?" * len(data))
    cur = db().execute(f"INSERT INTO leads ({cols}) VALUES ({marks})", list(data.values()))
    db().commit()
    return jsonify(get_lead(cur.lastrowid)), 201


@app.put("/api/leads/<int:lead_id>")
def update_lead(lead_id):
    try:
        data = clean(request.get_json(force=True), partial=True)
    except ValueError as e:
        return jsonify(error=str(e)), 400
    if not data:
        return jsonify(error="Nothing to update."), 400
    sets = ", ".join(f"{k} = ?" for k in data)
    cur = db().execute(f"UPDATE leads SET {sets} WHERE id = ?", [*data.values(), lead_id])
    db().commit()
    if cur.rowcount == 0:
        return jsonify(error="Lead not found."), 404
    return jsonify(get_lead(lead_id))


@app.delete("/api/leads/<int:lead_id>")
@admin_only
def delete_lead(lead_id):
    cur = db().execute("DELETE FROM leads WHERE id = ?", (lead_id,))
    db().commit()
    if cur.rowcount == 0:
        return jsonify(error="Lead not found."), 404
    return "", 204


@app.get("/api/stats")
def stats():
    con = db()
    found = {
        r["status"]: {"n": r["n"], "value": r["value"]}
        for r in con.execute("SELECT status, COUNT(*) n, SUM(value) value FROM leads GROUP BY status")
    }
    by_status = {s: found.get(s, {"n": 0, "value": 0}) for s in STATUSES}
    sources = [dict(r) for r in con.execute(
        "SELECT source, COUNT(*) n FROM leads GROUP BY source ORDER BY n DESC")]
    monthly = [dict(r) for r in con.execute(
        "SELECT strftime('%Y-%m', created_at) month, COUNT(*) n FROM leads GROUP BY month ORDER BY month")]

    won, lost = by_status["Won"], by_status["Lost"]
    closed = won["n"] + lost["n"]
    return jsonify(
        total=sum(v["n"] for v in by_status.values()),
        open_value=sum(v["value"] for s, v in by_status.items() if s not in ("Won", "Lost")),
        won_value=won["value"],
        win_rate=round(100 * won["n"] / closed) if closed else 0,
        by_status=by_status,
        sources=sources,
        monthly=monthly,
    )


init_db()

if __name__ == "__main__":
    app.run(debug=os.environ.get("FLASK_DEBUG") == "1")
