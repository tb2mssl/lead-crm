Live demo: https://lead-crm-wug0.onrender.com

## How accounts work
1. **Sign up** (`/signup`): username, email, optional mobile, password. Nothing is created yet.
2. **Confirm email**: the person gets an email with a link (valid 24 h). Clicking it creates the account.
3. **Admin approval**: the account cannot log in until the admin approves it on the website (admin page, "Waiting for approval"). The admin is not emailed; the dashboard shows "Manage users (N waiting)". The user is emailed when approved.
4. **Log in** (`/login`) with username or email. **Forgot password** (`/forgot`) emails a one-hour, single-use reset link; resetting also logs the account out everywhere else.
5. **Inactivity**: anyone (user or admin) is logged out after 15 minutes without activity.

**Admin** has a separate login at `/admin/login` (ID from `ADMIN_USERNAME`, reserved so nobody can sign up with it). Everything is done on the `/admin` page after logging in: approve/reject users, **add a user** (email + password you choose or generate; active immediately, no email involved), remove users, remove unconfirmed sign-ups, and send a test email to check the mail settings. Only the admin can delete leads.

Users can view, add and edit leads and download Excel (with the current filters). Dark / light screen switch on every page.
