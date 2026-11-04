"""Lead CRM: Flask + SQLite backend. Users sign up themselves; the admin has a separate login and controls."""
import hmac
import io
import re
import os
import secrets
import sqlite3
import time
from datetime import date, timedelta
from pathlib import Path

from functools import wraps

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
    PERMANENT_SESSION_LIFETIME=timedelta(hours=12),
)
if os.environ.get("RENDER"):  # behind Render's proxy: use the real client IP
    app.wsgi_app = ProxyFix(app.wsgi_app, x_for=1, x_proto=1)

ADMIN_USER = os.environ.get("ADMIN_USERNAME", "").strip()
ADMIN_HASH = os.environ.get("ADMIN_PASSWORD_HASH") or (
    generate_password_hash(os.environ["ADMIN_PASSWORD"]) if os.environ.get("ADMIN_PASSWORD") else "")
INGEST_KEY = os.environ.get("INGEST_API_KEY", "")


# ---------- database ----------
def init_db():
    con = sqlite3.connect(DB)
    con.executescript((BASE / "schema.sql").read_text())
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


def role():
    """'admin', 'user' or None. Admin and users are separate identities."""
    if ADMIN_USER and session.get("admin") == ADMIN_USER:
        return "admin"
    uid = session.get("uid")
    if uid and db().execute("SELECT 1 FROM users WHERE id = ?", (uid,)).fetchone():
        return "user"  # a deleted user is logged out on their next request
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


PUBLIC = ("static", "login", "signup", "admin_login")


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
    return redirect(url_for("login", next=request.path))


@app.after_request
def no_store(resp):
    if request.endpoint != "static":  # stops the back button showing data after logout
        resp.headers["Cache-Control"] = "no-store"
    return resp


def start_session(**ident):
    session.clear()
    session.permanent = True
    session.update(ident)


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
                FAILS.pop(key, None)
                start_session(uid=row["id"], name=row["username"])
                return redirect(nxt)
            record_fail(key)
            error = "Wrong username or password."
    return render_template("login.html", error=error, next=nxt, notice=request.args.get("notice"))


@app.route("/signup", methods=["GET", "POST"])
def signup():
    if logged_in():
        return redirect(url_for("index"))
    error, form = None, {}
    if request.method == "POST":
        form = {k: request.form.get(k, "").strip() for k in ("username", "email", "phone")}
        pw, pw2 = request.form.get("password", ""), request.form.get("confirm", "")
        ip = request.remote_addr or "unknown"
        recent = [t for t in SIGNUPS.get(ip, []) if time.time() - t < 3600]
        SIGNUPS[ip] = recent
        if len(recent) >= MAX_SIGNUPS_PER_HOUR:
            error = "Too many sign-ups from this address. Try again later."
        elif not USERNAME_RE.match(form["username"]):
            error = "Username must be 3-30 characters: letters, numbers, dot, dash or underscore."
        elif ADMIN_USER and form["username"].lower() == ADMIN_USER.lower():
            error = "That username is reserved. Please choose another."
        elif not EMAIL_RE.match(form["email"]):
            error = "Enter a valid email address."
        elif form["phone"] and not PHONE_RE.match(form["phone"]):
            error = "Enter a valid phone number or leave it blank."
        elif len(pw) < 8:
            error = "Password must be at least 8 characters."
        elif pw != pw2:
            error = "The two passwords do not match."
        else:
            try:
                db().execute("INSERT INTO users (username, email, phone, password_hash) VALUES (?, ?, ?, ?)",
                             (form["username"], form["email"], form["phone"], generate_password_hash(pw)))
                db().commit()
            except sqlite3.IntegrityError:
                error = "That username or email is already registered."
            else:
                SIGNUPS[ip].append(time.time())
                return redirect(url_for("login", notice="Account created. Please log in."))
    return render_template("signup.html", error=error, form=form)


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
    return render_template("admin_login.html", error=error)


@app.post("/logout")
def logout():
    was_admin = role() == "admin"
    session.clear()
    return redirect(url_for("admin_login" if was_admin else "login"))


# ---------- admin ----------
@app.get("/admin")
@admin_only
def admin_page():
    return render_template("admin.html", admin=ADMIN_USER)


@app.get("/api/admin/users")
@admin_only
def admin_users():
    rows = db().execute("SELECT id, username, email, phone, created_at FROM users ORDER BY id DESC").fetchall()
    return jsonify([dict(r) for r in rows])


@app.delete("/api/admin/users/<int:user_id>")
@admin_only
def admin_delete_user(user_id):
    cur = db().execute("DELETE FROM users WHERE id = ?", (user_id,))
    db().commit()
    if cur.rowcount == 0:
        return jsonify(error="User not found."), 404
    return "", 204


# ---------- pages ----------
@app.get("/")
def index():
    return render_template(
        "index.html",
        cfg={"statuses": STATUSES, "sources": SOURCES, "currency": CURRENCY,
             "user": session.get("name", ""), "role": role()})


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
