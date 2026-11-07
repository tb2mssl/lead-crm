CREATE TABLE IF NOT EXISTS leads (
    id         INTEGER PRIMARY KEY AUTOINCREMENT,
    name       TEXT NOT NULL,
    company    TEXT DEFAULT '',
    email      TEXT DEFAULT '',
    phone      TEXT DEFAULT '',
    source     TEXT NOT NULL DEFAULT 'Website',
    status     TEXT NOT NULL DEFAULT 'New'
               CHECK (status IN ('New','Contacted','Qualified','Proposal','Won','Lost')),
    value      REAL NOT NULL DEFAULT 0 CHECK (value >= 0),
    notes      TEXT DEFAULT '',
    created_at TEXT NOT NULL DEFAULT (date('now'))
);

CREATE INDEX IF NOT EXISTS idx_leads_status  ON leads(status);
CREATE INDEX IF NOT EXISTS idx_leads_created ON leads(created_at);

-- Regular users who sign up themselves. The admin is NOT stored here: it is a
-- separate identity configured with ADMIN_USERNAME / ADMIN_PASSWORD on the server.
CREATE TABLE IF NOT EXISTS users (
    id            INTEGER PRIMARY KEY AUTOINCREMENT,
    username      TEXT NOT NULL COLLATE NOCASE UNIQUE,
    email         TEXT NOT NULL COLLATE NOCASE UNIQUE,
    phone         TEXT DEFAULT '',
    password_hash TEXT NOT NULL,
    approved      INTEGER NOT NULL DEFAULT 0,  -- 0 = waiting for the admin, 1 = can log in
    email_verified  INTEGER NOT NULL DEFAULT 0,  -- 1 = the person clicked the link in a verification email
    reset_requested INTEGER NOT NULL DEFAULT 0,  -- 1 = asked for a new password while email was off
    created_at    TEXT NOT NULL DEFAULT (datetime('now'))
);

-- Sign-ups waiting for the person to click the link in their verification email.
-- The real account is only created in `users` after that click.
CREATE TABLE IF NOT EXISTS pending_signups (
    id            INTEGER PRIMARY KEY AUTOINCREMENT,
    token_hash    TEXT NOT NULL UNIQUE,
    username      TEXT NOT NULL COLLATE NOCASE,
    email         TEXT NOT NULL COLLATE NOCASE,
    phone         TEXT DEFAULT '',
    password_hash TEXT NOT NULL,
    created_at    REAL NOT NULL
);

-- One-time links for "forgot password" and for users the admin adds.
CREATE TABLE IF NOT EXISTS password_resets (
    id         INTEGER PRIMARY KEY AUTOINCREMENT,
    token_hash TEXT NOT NULL UNIQUE,
    user_id    INTEGER NOT NULL,
    expires_at REAL NOT NULL
);

-- One-time links the admin sends to an existing user to confirm their email address.
CREATE TABLE IF NOT EXISTS email_verifications (
    id         INTEGER PRIMARY KEY AUTOINCREMENT,
    token_hash TEXT NOT NULL UNIQUE,
    user_id    INTEGER NOT NULL,
    expires_at REAL NOT NULL
);

-- Every email the website tried to send, and whether it worked.
CREATE TABLE IF NOT EXISTS email_log (
    id         INTEGER PRIMARY KEY AUTOINCREMENT,
    to_email   TEXT NOT NULL,
    subject    TEXT NOT NULL DEFAULT '',
    status     TEXT NOT NULL,
    error      TEXT DEFAULT '',
    created_at TEXT NOT NULL
);

-- What the admin did (approve, delete, reset password, send email).
CREATE TABLE IF NOT EXISTS audit_log (
    id         INTEGER PRIMARY KEY AUTOINCREMENT,
    actor      TEXT NOT NULL,
    action     TEXT NOT NULL,
    target     TEXT DEFAULT '',
    created_at TEXT NOT NULL
);
