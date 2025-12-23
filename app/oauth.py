from authlib.integrations.flask_client import OAuth

oauth = OAuth()


def init_oauth(app):
	"""Initialize OAuth providers when credentials are configured."""
	oauth.init_app(app)

	client_id = app.config.get("OAUTH_CLIENT_ID")
	client_secret = app.config.get("OAUTH_CLIENT_SECRET")

	if not client_id or not client_secret:
		app.logger.info("OAuth is not configured; skipping provider registration.")
		return

	oauth.register(
		name="google",
		client_id=client_id,
		client_secret=client_secret,
		server_metadata_url="https://accounts.google.com/.well-known/openid-configuration",
		client_kwargs={"scope": "openid email profile"},
	)
	app.logger.info("OAuth provider 'google' registered.")
