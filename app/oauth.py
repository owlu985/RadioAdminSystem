from authlib.integrations.flask_client import OAuth

oauth = OAuth()


def _clean_optional(value):
        if value is None:
                return None
        if isinstance(value, str) and value.strip().lower() in {"", "none", "null"}:
                return None
        return value


def init_oauth(app):
        """Initialize OAuth providers when credentials are configured."""
        oauth.init_app(app)

        client_id = _clean_optional(app.config.get("OAUTH_CLIENT_ID"))
        client_secret = _clean_optional(app.config.get("OAUTH_CLIENT_SECRET"))
        discord_client_id = _clean_optional(app.config.get("DISCORD_OAUTH_CLIENT_ID"))
        discord_client_secret = _clean_optional(app.config.get("DISCORD_OAUTH_CLIENT_SECRET"))

        if not client_id or not client_secret:
                app.logger.info("Google OAuth is not configured; skipping Google provider registration.")
        else:
                oauth.register(
                        name="google",
                        client_id=client_id,
                        client_secret=client_secret,
                        server_metadata_url="https://accounts.google.com/.well-known/openid-configuration",
                        client_kwargs={"scope": "openid email profile"},
                )
                app.logger.info("OAuth provider 'google' registered.")

        if discord_client_id and discord_client_secret:
                oauth.register(
                        name="discord",
                        client_id=discord_client_id,
                        client_secret=discord_client_secret,
                        access_token_url="https://discord.com/api/oauth2/token",
                        authorize_url="https://discord.com/api/oauth2/authorize",
                        api_base_url="https://discord.com/api/",
                        client_kwargs={"scope": "identify email guilds"},
                )
                app.logger.info("OAuth provider 'discord' registered.")
        elif discord_client_id or discord_client_secret:
                app.logger.warning("Discord OAuth partially configured; both client id and secret are required.")

        if not ((client_id and client_secret) or (discord_client_id and discord_client_secret)):
                app.logger.info("No OAuth providers registered.")
