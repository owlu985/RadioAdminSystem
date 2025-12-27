# Social Posting Token Setup

Use these steps to fetch API tokens/credentials for each supported platform. Paste the resulting values into **Settings → Social Posting** in RAMS.

> Live delivery requirements (current implementation)
> - RAMS will send live posts to **Facebook (page token)**, **X/Twitter (OAuth2 user bearer token with tweet.write scope)**, and **Bluesky (handle + app password)** when **Enable real delivery** is on **and** **Simulate posts** is off.
> - Instagram remains **simulated/unsupported** with the current settings because it requires additional IDs/keys not yet captured.
> - Blank tokens automatically skip that platform; RAMS never attempts a call without credentials.

> **Security tip:** Treat all tokens/app passwords as secrets. Rotate them if you suspect they were exposed.

---
## Facebook / Instagram (Meta Graph API)
1. Go to [Meta for Developers](https://developers.facebook.com/) → **My Apps** → **Create App** → choose **Business** (or the minimal type Meta allows for Pages/Instagram Basic Display).
2. Add products **Facebook Login** (for token tooling) and **Instagram Basic Display** (if posting to Instagram) plus **Pages API**.
3. Under **Facebook Login → Settings**, add a **Valid OAuth Redirect URI** that matches your RAMS host (e.g., `https://yourhost/login`). This is only for Meta tooling; RAMS posts with page tokens, not user login.
4. Use the **Graph API Explorer** to generate a **User Access Token** with scopes:
   - For Facebook Pages: `pages_manage_posts`, `pages_read_engagement`, `pages_show_list`.
   - For Instagram: `instagram_basic`, `pages_manage_metadata`, `pages_read_engagement`, `instagram_content_publish` (if available for your account).
5. Exchange the short-lived token for a **long-lived User Access Token** (Graph API Explorer → **Generate Access Token** → **Extend Access Token**). Copy the long-lived token into RAMS **Facebook/Instagram Token**.
6. Retrieve your **Page ID** (for Facebook) and, if using Instagram, the **Instagram Business/Creator account ID** linked to that page. Add these IDs in RAMS settings.
7. Keep the app in **Live** mode so tokens remain usable beyond development.

---
## X (Twitter)
RAMS posts using the v2 `/2/tweets` endpoint with an OAuth2 user-context bearer token that has `tweet.write` scope.

1. In the [Twitter/X Developer Portal](https://developer.twitter.com/), create an app with **Read and Write** permissions.
2. Configure OAuth2 (User Context) and request `tweet.read`, `tweet.write`, `offline.access` scopes.
3. Complete the OAuth flow once to obtain a **user bearer/access token** (not just the app-only bearer). Paste that token into **Twitter/X Bearer Token** in RAMS settings.
4. If your account is on a paid tier, ensure posting is allowed for your API access level. Blank tokens are skipped automatically.

---
## Bluesky
1. Open [https://bsky.app/settings/app-passwords](https://bsky.app/settings/app-passwords) while logged into your account.
2. Create an **App Password** (never use your main password).
3. Copy the app password and your handle (e.g., `yourname.bsky.social`) into RAMS **Bluesky** settings.

---
## Discord (Webhook for social mirroring or alerts)
1. In Discord, create or choose a channel → **Edit Channel** → **Integrations** → **Webhooks** → **New Webhook**.
2. Copy the **Webhook URL** into RAMS **Discord Webhook** setting. No bot token is required for simple post mirroring.

---
## Testing tokens inside RAMS
- After saving tokens in **Settings → Social Posting**, use the **Social Posting** page to create a test message with **Simulate Only** checked. RAMS will log what would be sent without hitting the APIs.
- When ready, uncheck **Simulate Only** to post live.
