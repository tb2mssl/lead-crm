# Setup: permanent database + working email (Render free plan)

Why: on Render's free plan the local SQLite file is erased on restart (users vanish) and SMTP ports are blocked (no emails).
This setup fixes both: a PostgreSQL database that keeps your users, and Brevo's web API for email.

## Part 1 - Create the PostgreSQL database (Neon, free)
1. Go to https://neon.tech, sign up, click **Create project**. Name it `lead-crm`. Pick the region closest to your Render service.
2. On the project page click **Connect** and copy the connection string. It looks like
   `postgresql://user:password@ep-xxxx.region.aws.neon.tech/neondb?sslmode=require`. Keep it secret.
3. (Optional, the app does this by itself on start) Open **SQL Editor**, paste all of `schema_postgres.sql`, click **Run**.
   Then paste `seed_postgres.sql` and run it to load the 500 sample leads.
4. Check: run `SELECT count(*) FROM leads;` (500) and `SELECT * FROM users;` (empty until people sign up).

Supabase or Render's own Postgres also work: you only need their connection string. Check their current free-plan limits first
(some free databases pause or expire).

## Part 2 - Set up Brevo email
1. Log in to https://app.brevo.com. Go to **Senders, domains & dedicated IPs > Senders > Add a sender**.
2. Add `tb2phd@gmail.com` (the name is what people see, e.g. "Lead pipeline"). Brevo emails a code/link to that Gmail: confirm it.
3. Go to **SMTP & API > API keys > Generate a new API key**. Name it `lead-crm`, copy the key (shown once).
4. Note: mail sent "from" a Gmail address through another service often lands in spam. It works for testing; later, with your own domain,
   authenticate it in Brevo for better delivery.

## Part 3 - Put the settings on Render
Render dashboard > your web service > **Environment**. Add:

| Name | Value |
|---|---|
| `DATABASE_URL` | the Neon connection string from Part 1 |
| `BREVO_API_KEY` | the key from Part 2 |
| `MAIL_FROM` | `tb2phd@gmail.com` (must match the verified sender) |
| `MAIL_FROM_NAME` | `Lead pipeline` |
| `BASE_URL` | `https://lead-crm-wug0.onrender.com` |
| `SECRET_KEY` | any long random string |
| `ADMIN_USERNAME`, `ADMIN_PASSWORD` | your admin login (as now) |

Do not set `SMTP_HOST` (the web API is used when `BREVO_API_KEY` is set). Make sure `requirements.txt` (with `psycopg2-binary`) is in the repo, then push
the new files (`app.py`, `schema.sql`, `schema_postgres.sql`, `seed_postgres.sql`, `requirements.txt`) to GitHub. Render redeploys.

## Part 4 - Test
1. Open `/admin/login`, log in, use **Send test** with your own address. A failure shows the reason (wrong key, unverified sender...).
2. Open `/signup`, create a user with a second email address of yours. Click the link in the email, then approve the user on `/admin`.
3. Log in as that user. Try `/forgot`: the reset email should arrive and the link should work once.
4. Restart the service (Manual deploy). The user must still exist: that proves the data is permanent.
5. If something fails: Render > **Logs**, and `GET /api/admin/emails` (while logged in as admin) shows each email and its error.

## Management: what the admin can do per user ID
See the table in `README.md` (send verification, send reset link, set password, resend sign-up link, email log, audit log).
These are API calls; the buttons for them must be added to `templates/admin.html`.

## If you meant Microsoft SQL Server
Render cannot host it, and the app would need an ODBC driver. The PostgreSQL files above are the simplest working route. If you
specifically need Microsoft SQL Server (for example a server you already own), say so and I will write the T-SQL schema and adapter.
