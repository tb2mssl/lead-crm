Live demo: https://lead-crm-wug0.onrender.com

## How accounts work
1. **Sign up** (`/signup`): username, email, optional mobile, password. Nothing is created yet.
2. **Confirm email**: the person gets an email with a link (valid 24 h). Clicking it creates the account.
3. **Admin approval**: the account cannot log in until the admin approves it on the website (admin page, "Waiting for approval"). The admin is not emailed; the dashboard shows "Manage users (N waiting)". The user is emailed when approved.
4. **Log in** (`/login`) with username or email. **Forgot password** (`/forgot`) emails a one-hour, single-use reset link; resetting also logs the account out everywhere else.
5. **Inactivity**: anyone (user or admin) is logged out after 15 minutes without activity.

**Admin** has a separate login at `/admin/login` (ID from `ADMIN_USERNAME`, reserved so nobody can sign up with it). Everything is done on the `/admin` page after logging in: approve/reject users, **add a user** (email + password you choose or generate; active immediately, no email involved), remove users, remove unconfirmed sign-ups, and send a test email to check the mail settings. Only the admin can delete leads.

Users can view, add and edit leads and download Excel (with the current filters). Dark / light screen switch on every page.

## Email: your own mail server, no third-party service
The website sends confirmation and reset emails itself, through your email provider's SMTP server.

| Variable | Meaning |
|---|---|
| `SMTP_HOST` | e.g. `smtp.gmail.com` |
| `SMTP_PORT` | `587` (default, STARTTLS) or `465` (SSL) |
| `SMTP_USER`, `SMTP_PASSWORD` | the mailbox login (Gmail and many providers need an *app password*) |
| `MAIL_FROM` | sender address (defaults to `SMTP_USER`); `MAIL_FROM_NAME` optional |
| `BASE_URL` | e.g. `https://lead-crm-wug0.onrender.com` (links inside emails) |
| `BREVO_API_KEY` | sends through Brevo's web API (port 443), which works on Render's free plan. Needs `MAIL_FROM` = a sender verified in Brevo |
| `MAIL_CONSOLE=1` | local testing only: prints emails in the log instead of sending |

**Hosting note:** Render's free plan blocks outbound SMTP ports (25, 465, 587). On the free plan use `BREVO_API_KEY` (web API, no SMTP); SMTP works on a paid instance. Use the **Send test** button on the admin page to check. Every email attempt, sent or failed, is recorded and shown by `GET /api/admin/emails`. If no email is configured at all, sign-up still works (the admin approves), "Forgot password" flags the account for the admin, and the admin sets a new password.

## Other variables
`DATABASE_URL` (PostgreSQL connection string; leave empty to use the local SQLite file, which Render's free plan erases on restart), `ADMIN_USERNAME`, `ADMIN_PASSWORD` (admin login), `SECRET_KEY` (any long random string), `INGEST_API_KEY` (optional, for webhooks that POST new leads).

## Sample data
A fresh database is seeded with 500 sample leads (`seed.sql`). Existing databases are upgraded automatically; users registered before approval existed stay approved.

## Database
Set `DATABASE_URL` to use PostgreSQL (`schema_postgres.sql`, `seed_postgres.sql`); without it the app uses SQLite (`schema.sql`, `seed.sql`). Step-by-step setup: `DATABASE_SETUP.md`.

## Admin management API (admin login required)
| Call | What it does |
|---|---|
| `GET /api/admin/users` | users, waiting for approval, unconfirmed sign-ups, and `reset_requests` |
| `POST /api/admin/users/<id>/approve` | approve a user |
| `POST /api/admin/users/<id>/send-verification` | email that user a confirm-your-email link |
| `POST /api/admin/users/<id>/send-reset` | email that user a one-hour password reset link |
| `POST /api/admin/users/<id>/password` | set a password (give `{"password": "..."}` or leave empty to generate one) |
| `DELETE /api/admin/users/<id>` | remove a user |
| `POST /api/admin/pending/<id>/resend` | resend the confirmation link of an unconfirmed sign-up |
| `GET /api/admin/emails` | last 100 emails and whether each was sent |
| `GET /api/admin/audit` | last 100 admin actions |
| `POST /api/account/password` | a logged-in user changes their own password |
