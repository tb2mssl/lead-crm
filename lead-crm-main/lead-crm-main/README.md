Live demo: https://lead-crm-wug0.onrender.com

## Access
- **Users** sign up at `/signup` (username, email, optional mobile, password) and log in at `/login` (username or email). They can view, add and edit leads.
- **Admin** has a separate login at `/admin/login`, using the ID and password you set in the server environment. The admin ID is reserved (nobody can sign up with it) and is not stored in the users table. Only the admin can delete leads and manage/delete users (`/admin`).

## Excel download
On the leads table, set any filters (search, status, source, added-from / added-to dates) and click **Download Excel**. The file contains exactly the filtered leads, with Excel filter dropdowns on every column. Available to users and admin.

## Environment variables (Render > Environment)
- `ADMIN_USERNAME` - the admin's unique ID
- `ADMIN_PASSWORD` (or `ADMIN_PASSWORD_HASH`)
- `SECRET_KEY` - any long random string
- `INGEST_API_KEY` - optional, for webhooks that POST new leads

## Sample data
A fresh database is seeded with 500 sample leads (`seed.sql`). The database file is not included, so it is created on first start.
