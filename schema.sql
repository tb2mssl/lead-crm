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
    created_at    TEXT NOT NULL DEFAULT (datetime('now'))
);
