# OAuth Setup for RAMS (Google and Discord)

These steps explain how to enable Google and Discord OAuth logins for RAMS admins/managers. OAuth is optional; when enabled, the login page shows the corresponding provider buttons. Only OAuth-subject data (email/name/IDs) is stored—no passwords are kept.

> **Prerequisites**
> - Install Authlib locally: `pip install Authlib` (requirements.txt is intentionally unchanged).
> - Ensure RAMS is reachable at the domain/port you will register with each provider. For local dev, use `http://localhost:5000`.
>
> **Redirect URLs (copy exactly)**
> - Google: `http://<your-host>/login/oauth/google/callback`
> - Discord: `http://<your-host>/login/oauth/discord/callback`
>
> Replace `<your-host>` with the external URL you will log in through (e.g., `localhost:5000`, `rams.example.edu`).

---
## Google OAuth (OpenID Connect)
1. Go to [Google Cloud Console](https://console.cloud.google.com/apis/credentials) → **Create Credentials** → **OAuth client ID** → choose **Web application**.
2. Add the redirect URI: `http://<your-host>/login/oauth/google/callback`.
3. After creation, copy the **Client ID** and **Client Secret**.
4. (Optional) Restrict to a single GSuite/Google Workspace domain (e.g., `example.edu`).
5. In RAMS **Settings** (admin UI):
   - **OAuth Client ID** → paste the Google Client ID.
   - **OAuth Client Secret** → paste the Google Client Secret.
   - **Allowed Email Domain** → set your domain to restrict who can sign in (leave blank to allow any Google account).
6. Save settings and reload the login page; the **Sign in with Google** button will appear.

---
## Discord OAuth
1. Go to [Discord Developer Portal](https://discord.com/developers/applications) → **New Application** → **OAuth2** → **General**.
2. Under **Redirects**, add: `http://<your-host>/login/oauth/discord/callback`.
3. Under **OAuth2 → URL Generator**, select scopes `identify`, `email`, and `guilds` (this matches the app config). Take note of the **Client ID** and **Client Secret** from **General Information**.
4. (Optional) Restrict access to members of a specific guild (server). Copy the Guild ID (Developer Mode → right-click server → *Copy ID*).
5. In RAMS **Settings** (admin UI):
   - **Discord OAuth Client ID** → paste the Discord Client ID.
   - **Discord OAuth Client Secret** → paste the Discord Client Secret.
   - **Discord Allowed Guild ID** → paste the guild/server ID to require membership (leave blank to allow any Discord user with an email).
6. Save settings and reload the login page; the **Sign in with Discord** button will appear.

---
## Operational Notes
- **Session data**: On successful OAuth login, RAMS stores `session['authenticated']`, `session['user_email']`, and provider info. No password is stored.
- **Domain/guild enforcement**: Google logins are blocked if the email’s domain does not match the configured `Allowed Email Domain`. Discord logins are blocked if `Discord Allowed Guild ID` is set and the user is not a member of that guild.
- **Local vs production**: Redirect URIs must exactly match what you register. For HTTPS deployments, use `https://` in both the provider config and RAMS URLs.
- **Disabling OAuth**: Clear the provider fields in Settings to hide the buttons and fall back to the local admin username/password.

