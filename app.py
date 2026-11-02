"""Lead CRM: Flask + SQLite backend serving a dashboard (HTML/CSS/JS), protected by a login."""
import hmac
import os
import secrets
import sqlite3
import time
from datetime import timedelta
from pathlib import Path

from flask import Flask, g, jsonify, redirect, render_template, request, session, url_for
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
# ADMIN_USERNAME    the login name.
# ADMIN_PASSWORD    the password (or ADMIN_PASSWORD_HASH, a werkzeug hash, if you prefer).
# INGEST_API_KEY    optional. Lets webhooks/automations POST new leads without a login.
if not os.environ.get("SECRET_KEY"):
    print("WARNING: SECRET_KEY is not set. Using a temporary key; everyone is logged out on restart.")
app.config.update(
    SECRET_KEY=os.environ.get("SECRET_KEY") #or secrets.token_hex(32),
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
    fresh = not DB.exists()
    con = sqlite3.connect(DB)
    con.executescript((BASE / "schema.sql").read_text())
    if fresh:  # sample data only on the very first run
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
FAILS = {}  # ip -> (failed attempts, time of first failure)
MAX_FAILS, LOCK_SECONDS = 5, 15 * 60


def same(a, b):
    return hmac.compare_digest(a.encode(), b.encode())


def logged_in():
    return bool(ADMIN_USER) and session.get("user") == ADMIN_USER


def locked(ip):
    n, t = FAILS.get(ip, (0, 0))
    if n and time.time() - t > LOCK_SECONDS:
        FAILS.pop(ip, None)
        return False
    return n >= MAX_FAILS


def record_fail(ip):
    n, t = FAILS.get(ip, (0, time.time()))
    FAILS[ip] = (n + 1, t)


def safe_next(target):
    """Only allow redirects back into this site."""
    if target and target.startswith("/") and not target.startswith("//") and "\\" not in target:
        return target
    return url_for("index")


@app.before_request
def require_login():
    if request.endpoint in ("static", "login"):
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


@app.route("/login", methods=["GET", "POST"])
def login():
    nxt = safe_next(request.values.get("next"))
    if logged_in():
        return redirect(nxt)
    error = None
    if not (ADMIN_USER and ADMIN_HASH):
        error = "Login is not set up. Set ADMIN_USERNAME and ADMIN_PASSWORD on the server."
    elif request.method == "POST":
        ip = request.remote_addr or "unknown"
        if locked(ip):
            error = "Too many failed attempts. Try again in 15 minutes."
        else:
            user_ok = same(request.form.get("username", "").strip(), ADMIN_USER)
            pass_ok = check_password_hash(ADMIN_HASH, request.form.get("password", ""))
            if user_ok and pass_ok:
                FAILS.pop(ip, None)
                session.clear()
                session.permanent = True
                session["user"] = ADMIN_USER
                return redirect(nxt)
            record_fail(ip)
            error = "Wrong username or password."
    return render_template("login.html", error=error, next=nxt)


@app.post("/logout")
def logout():
    session.clear()
    return redirect(url_for("login"))


# ---------- pages ----------
@app.get("/")
def index():
    return render_template(
        "index.html",
        cfg={"statuses": STATUSES, "sources": SOURCES, "currency": CURRENCY, "user": session.get("user", "")})


# ---------- API ----------
@app.get("/api/leads")
def list_leads():
    rows = db().execute("SELECT * FROM leads ORDER BY created_at DESC, id DESC").fetchall()
    return jsonify([dict(r) for r in rows])


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
