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
| `MAIL_CONSOLE=1` | local testing only: prints emails in the log instead of sending |

**Hosting note:** Render's free plan blocks outbound SMTP ports (25, 465, 587). Run the site on a paid Render instance or any host that allows outbound SMTP, otherwise emails cannot be sent. Use the **Send test** button on the admin page to check. Without working email, sign-up and forgot-password show a clear message, and the admin can still add users directly.

## Other variables
`ADMIN_USERNAME`, `ADMIN_PASSWORD` (admin login), `SECRET_KEY` (any long random string), `INGEST_API_KEY` (optional, for webhooks that POST new leads).

## Sample data
A fresh database is seeded with 500 sample leads (`seed.sql`). Existing databases are upgraded automatically; users registered before approval existed stay approved.
