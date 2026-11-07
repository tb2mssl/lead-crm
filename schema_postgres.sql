-- Lead CRM: PostgreSQL schema.
-- Safe to run more than once (every statement is CREATE ... IF NOT EXISTS).
-- The app also runs this file by itself every time it starts when DATABASE_URL is set.

-- Case-insensitive text, so "Tushar" and "tushar" are the same username / email.
CREATE EXTENSION IF NOT EXISTS citext;

-- ---------- leads ----------
CREATE TABLE IF NOT EXISTS leads (
    id         BIGSERIAL PRIMARY KEY,
    name       TEXT NOT NULL,
    company    TEXT DEFAULT '',
    email      TEXT DEFAULT '',
    phone      TEXT DEFAULT '',
    source     TEXT NOT NULL DEFAULT 'Website',
    status     TEXT NOT NULL DEFAULT 'New'
               CHECK (status IN ('New','Contacted','Qualified','Proposal','Won','Lost')),
    value      DOUBLE PRECISION NOT NULL DEFAULT 0 CHECK (value >= 0),
    notes      TEXT DEFAULT '',
    created_at TEXT NOT NULL DEFAULT to_char(current_date, 'YYYY-MM-DD')
);

CREATE INDEX IF NOT EXISTS idx_leads_status  ON leads(status);
CREATE INDEX IF NOT EXISTS idx_leads_created ON leads(created_at);

-- ---------- users ----------
-- Regular users. The admin is NOT stored here: it is set with ADMIN_USERNAME / ADMIN_PASSWORD on the server.
CREATE TABLE IF NOT EXISTS users (
    id              BIGSERIAL PRIMARY KEY,
    username        CITEXT NOT NULL UNIQUE,
    email           CITEXT NOT NULL UNIQUE,
    phone           TEXT DEFAULT '',
    password_hash   TEXT NOT NULL,                 -- never the password itself
    approved        INTEGER NOT NULL DEFAULT 0,    -- 0 = waiting for the admin, 1 = can log in
    email_verified  INTEGER NOT NULL DEFAULT 0,    -- 1 = the person clicked the link in a verification email
    reset_requested INTEGER NOT NULL DEFAULT 0,    -- 1 = asked for a new password while email was off
    created_at      TEXT NOT NULL DEFAULT to_char((now() AT TIME ZONE 'UTC'), 'YYYY-MM-DD HH24:MI:SS')
);

-- Sign-ups waiting for the person to click the link in their verification email.
-- The real account is created in `users` only after that click.
CREATE TABLE IF NOT EXISTS pending_signups (
    id            BIGSERIAL PRIMARY KEY,
    token_hash    TEXT NOT NULL UNIQUE,            -- the link's token is stored only as a hash
    username      CITEXT NOT NULL,
    email         CITEXT NOT NULL,
    phone         TEXT DEFAULT '',
    password_hash TEXT NOT NULL,
    created_at    DOUBLE PRECISION NOT NULL        -- unix time
);

-- One-time "forgot password" links (one hour).
CREATE TABLE IF NOT EXISTS password_resets (
    id         BIGSERIAL PRIMARY KEY,
    token_hash TEXT NOT NULL UNIQUE,
    user_id    BIGINT NOT NULL REFERENCES users(id) ON DELETE CASCADE,
    expires_at DOUBLE PRECISION NOT NULL
);

-- One-time email-verification links the admin sends to an existing user (24 hours).
CREATE TABLE IF NOT EXISTS email_verifications (
    id         BIGSERIAL PRIMARY KEY,
    token_hash TEXT NOT NULL UNIQUE,
    user_id    BIGINT NOT NULL REFERENCES users(id) ON DELETE CASCADE,
    expires_at DOUBLE PRECISION NOT NULL
);

-- ---------- backend management ----------
-- Every email the website tried to send, and whether it worked.
CREATE TABLE IF NOT EXISTS email_log (
    id         BIGSERIAL PRIMARY KEY,
    to_email   TEXT NOT NULL,
    subject    TEXT NOT NULL DEFAULT '',
    status     TEXT NOT NULL,                      -- 'sent' or 'failed'
    error      TEXT DEFAULT '',
    created_at TEXT NOT NULL
);

-- What the admin did (approve, delete, reset password, send email).
CREATE TABLE IF NOT EXISTS audit_log (
    id         BIGSERIAL PRIMARY KEY,
    actor      TEXT NOT NULL,
    action     TEXT NOT NULL,
    target     TEXT DEFAULT '',
    created_at TEXT NOT NULL
);

CREATE INDEX IF NOT EXISTS idx_email_log_created ON email_log(id DESC);
CREATE INDEX IF NOT EXISTS idx_audit_log_created ON audit_log(id DESC);
