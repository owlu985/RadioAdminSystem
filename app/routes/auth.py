import base64
import hashlib
import json
import secrets
from datetime import datetime

from flask import current_app, flash, jsonify, redirect, render_template, request, session, url_for
from werkzeug.security import check_password_hash

from app.auth_utils import admin_required
from app.logger import init_logger
from app.main_routes import main_bp
from app.models import User, db
from app.oauth import ensure_oauth_initialized, oauth

logger = init_logger()


def _complete_login(user: User):
    user.last_login_at = datetime.utcnow()
    db.session.commit()
    session['authenticated'] = True
    session['user_email'] = user.email
    session['auth_provider'] = user.provider
    session['user_id'] = user.id
    session['display_name'] = user.display_name or user.email
    role = user.custom_role or user.role or 'viewer'
    session['role'] = role
    perms = []
    if user.permissions:
        perms = [p.strip() for p in user.permissions.split(',') if p.strip()]
    session['permissions'] = perms


def _parse_identities(user: User) -> list[dict]:
    try:
        return json.loads(user.identities) if user.identities else []
    except Exception:  # noqa: BLE001
        return []


def _add_identity(user: User, provider: str, external_id: str | None, email: str | None) -> None:
    identities = _parse_identities(user)
    exists = any(
        (i.get('provider') == provider and i.get('external_id') == str(external_id))
        or (email and i.get('email') and i.get('email').lower() == email.lower())
        for i in identities
    )
    if not exists:
        identities.append({
            'provider': provider,
            'external_id': str(external_id) if external_id else None,
            'email': email,
        })
        user.identities = json.dumps(identities)


def _serialize_oauth_token(token: dict | None, provider: str | None = None) -> dict | None:
    """Safely stash an OAuth token in the session for admin inspection."""

    if token is None:
        return None

    def _coerce(value):
        try:
            json.dumps(value)
            return value
        except Exception:
            return str(value)

    if isinstance(token, dict):
        sanitized = {k: _coerce(v) for k, v in token.items()}
    else:
        sanitized = _coerce(token)

    return {
        "provider": provider,
        "token": sanitized,
        "captured_at": datetime.utcnow().isoformat(),
    }


def _pkce_pair():
    verifier = secrets.token_urlsafe(64)
    digest = hashlib.sha256(verifier.encode()).digest()
    challenge = base64.urlsafe_b64encode(digest).rstrip(b"=").decode()
    return verifier, challenge


def _find_user(provider: str, external_id: str | None, email: str | None, display_name: str | None = None):
    external_id = str(external_id) if external_id else None
    email_norm = email.lower() if email else None

    if external_id:
        user = User.query.filter_by(provider=provider, external_id=external_id).first()
        if user:
            return user
        user = User.query.filter(User.identities.ilike(f"%{provider}%"), User.identities.ilike(f"%{external_id}%")).first()
        if user:
            return user

    if email_norm:
        user = User.query.filter(db.func.lower(User.email) == email_norm).first()
        if user:
            return user
        user = User.query.filter(User.identities.ilike(f"%{email_norm}%")).first()
        if user:
            return user

    if display_name:
        user = User.query.filter(User.display_name.ilike(display_name)).first()
        if user:
            return user
    return None


def _redirect_pending(profile):
    session['pending_oauth'] = profile
    flash("Almost done! Please confirm your name to request access.", "info")
    return redirect(url_for('main.oauth_claim'))


def _clean_optional(value):
    if value is None:
        return None
    if isinstance(value, str) and value.strip().lower() in {"", "none", "null"}:
        return None
    return value


@main_bp.route('/login', methods=['GET'])
def login():
    """OAuth-first login landing page with master admin link."""

    ensure_oauth_initialized(current_app)

    google_client = oauth.create_client("google")
    discord_client = oauth.create_client("discord")
    oauth_enabled = google_client is not None or discord_client is not None
    allowed_domain = _clean_optional(current_app.config.get("OAUTH_ALLOWED_DOMAIN"))

    return render_template(
        'login.html',
        oauth_enabled=oauth_enabled,
        oauth_google_enabled=google_client is not None,
        oauth_discord_enabled=discord_client is not None,
        oauth_allowed_domain=allowed_domain,
    )


@main_bp.route('/login/master', methods=['GET', 'POST'])
def master_login():
    """Password-only master admin login for emergency/owner access."""

    if request.method == 'POST':
        password = request.form.get('password') or ""
        if current_app.config.get("OAUTH_ONLY"):
            flash("Master admin login is disabled while OAuth-only mode is enabled.", "danger")
            return redirect(url_for("main.login"))
        configured_hash = current_app.config.get("ADMIN_PASSWORD_HASH")
        configured_password = current_app.config.get("ADMIN_PASSWORD") or ""
        if configured_hash:
            is_valid = check_password_hash(configured_hash, password)
        else:
            is_valid = bool(password) and secrets.compare_digest(password, str(configured_password))
        if is_valid:
            session['authenticated'] = True
            session['role'] = 'admin'
            session['display_name'] = current_app.config.get('ADMIN_USERNAME', 'Master Admin')
            session['auth_provider'] = 'master'
            logger.info("Master admin logged in via password.")
            flash("You are now logged in as master admin.", "success")
            return redirect(url_for('main.dashboard'))
        flash("Invalid master admin password.", "danger")

    return render_template('master_login.html')


@main_bp.route("/login/oauth/google")
def login_oauth_google():
    """Start a Google OAuth login."""

    ensure_oauth_initialized(current_app)

    client = oauth.create_client("google")
    if client is None:
        flash("Google OAuth is not configured. Please add a client id/secret in Settings.", "danger")
        return redirect(url_for("main.login"))

    redirect_uri = url_for("main.oauth_callback_google", _external=True)
    nonce = secrets.token_urlsafe(16)
    session["oauth_google_nonce"] = nonce
    return client.authorize_redirect(redirect_uri, nonce=nonce)


@main_bp.route("/login/oauth/google/callback")
def oauth_callback_google():
    """Handle Google OAuth callback and establish a session."""

    ensure_oauth_initialized(current_app)

    client = oauth.create_client("google")
    if client is None:
        flash("OAuth is not configured.", "danger")
        return redirect(url_for("main.login"))

    try:
        token = client.authorize_access_token()
        session["last_oauth_token"] = _serialize_oauth_token(token, "google")
        nonce = session.pop("oauth_google_nonce", None)
        userinfo = None
        try:
            userinfo = client.parse_id_token(token, nonce=nonce)
        except Exception as parse_exc:  # noqa: BLE001
            logger.warning(f"ID token parse failed ({parse_exc}); falling back to userinfo endpoint.")
            userinfo = None
        if not userinfo:
            resp = client.get("userinfo")
            userinfo = resp.json() if resp else {}
    except Exception as exc:  # noqa: BLE001
        logger.error(f"OAuth login failed: {exc}")
        flash("OAuth login failed. Please verify the Google client credentials and redirect URI.", "danger")
        return redirect(url_for("main.login"))

    email = (userinfo or {}).get("email")
    if not email:
        flash("OAuth login failed: missing email.", "danger")
        return redirect(url_for("main.login"))

    allowed_domain = _clean_optional(current_app.config.get("OAUTH_ALLOWED_DOMAIN"))
    if allowed_domain and not email.lower().endswith(f"@{allowed_domain.lower()}"):
        logger.warning("OAuth login blocked due to domain restriction.")
        flash("Your account is not permitted to log in with this station.", "danger")
        return redirect(url_for("main.login"))

    external_id = (userinfo or {}).get("sub")
    suggested_name = (userinfo or {}).get("name") or email.split("@")[0]
    existing = _find_user("google", external_id, email, None)

    if existing:
        _add_identity(existing, "google", external_id, email)
        db.session.commit()
        if existing.rejected or existing.approval_status == 'rejected':
            flash("Your account request was rejected. Please contact an administrator.", "danger")
            return redirect(url_for('main.login'))
        if existing.approved or existing.approval_status == 'approved':
            _complete_login(existing)
            logger.info("Admin logged in via Google OAuth.")
            flash("You are now logged in via Google.", "success")
            return redirect(url_for('main.dashboard'))
        session['pending_user_id'] = existing.id
        flash("Your account is pending approval.", "info")
        return redirect(url_for('main.oauth_pending'))

    merge_candidate = None
    if suggested_name:
        merge_candidate = User.query.filter(User.display_name.ilike(suggested_name)).first()

    profile = {
        "provider": "google",
        "email": email,
        "external_id": external_id,
        "suggested_name": suggested_name,
    }
    if merge_candidate:
        profile["merge_candidate_id"] = merge_candidate.id
    return _redirect_pending(profile)


@main_bp.route("/login/oauth/discord")
def login_oauth_discord():
    """Start a Discord OAuth login."""

    ensure_oauth_initialized(current_app)

    client = oauth.create_client("discord")
    if client is None:
        flash("Discord OAuth is not configured. Please add the Discord client id/secret in Settings.", "danger")
        return redirect(url_for("main.login"))

    redirect_uri = url_for("main.oauth_callback_discord", _external=True)
    return client.authorize_redirect(redirect_uri)


@main_bp.route("/login/oauth/discord/callback")
def oauth_callback_discord():
    """Handle Discord OAuth callback and establish a session."""

    ensure_oauth_initialized(current_app)

    client = oauth.create_client("discord")
    if client is None:
        flash("Discord OAuth is not configured.", "danger")
        return redirect(url_for("main.login"))

    try:
        token = client.authorize_access_token()
        session["last_oauth_token"] = _serialize_oauth_token(token, "discord")
        userinfo_resp = client.get("users/@me")
        userinfo = userinfo_resp.json() if userinfo_resp else {}
    except Exception as exc:  # noqa: BLE001
        logger.error(f"Discord OAuth login failed: {exc}")
        flash("Discord login failed. Please try again or contact an admin.", "danger")
        return redirect(url_for("main.login"))

    if not userinfo:
        flash("Discord login failed: missing user info.", "danger")
        return redirect(url_for("main.login"))

    email = userinfo.get("email")
    if not email:
        logger.warning("Discord OAuth did not return an email; access denied.")
        flash("Discord account is missing an email address; cannot log in.", "danger")
        return redirect(url_for("main.login"))

    allowed_guild_id = _clean_optional(current_app.config.get("DISCORD_ALLOWED_GUILD_ID"))
    guild_member = True
    if allowed_guild_id:
        try:
            guilds_resp = client.get("users/@me/guilds")
            guilds = guilds_resp.json() if guilds_resp else []
            guild_member = any(str(g.get("id")) == str(allowed_guild_id) for g in guilds)
        except Exception as exc:  # noqa: BLE001
            logger.error(f"Discord guild lookup failed: {exc}")
            flash("Discord login failed while checking permissions.", "danger")
            return redirect(url_for("main.login"))

    external_id = (userinfo or {}).get("id")
    suggested_name = userinfo.get("global_name") or userinfo.get("username") or email.split("@")[0]
    existing = _find_user("discord", external_id, email, None)

    if existing:
        _add_identity(existing, "discord", external_id, email)
        db.session.commit()
        if existing.rejected or existing.approval_status == 'rejected':
            flash("Your account request was rejected. Please contact an administrator.", "danger")
            return redirect(url_for('main.login'))
        if existing.approved or existing.approval_status == 'approved':
            _complete_login(existing)
            logger.info("Admin logged in via Discord OAuth.")
            flash("You are now logged in via Discord.", "success")
            return redirect(url_for('main.dashboard'))
        session['pending_user_id'] = existing.id
        flash("Your account is pending approval.", "info")
        return redirect(url_for('main.oauth_pending'))

    if allowed_guild_id and not guild_member:
        flash("Please submit your name to request access; you aren't in the authorized Discord guild yet.", "info")

    merge_candidate = None
    if suggested_name:
        merge_candidate = User.query.filter(User.display_name.ilike(suggested_name)).first()

    profile = {
        "provider": "discord",
        "email": email,
        "external_id": external_id,
        "suggested_name": suggested_name,
    }
    if merge_candidate:
        profile["merge_candidate_id"] = merge_candidate.id
    return _redirect_pending(profile)


@main_bp.route("/api/oauth/last-token")
@admin_required
def last_oauth_token():
    """Return the most recent OAuth token captured in this session."""

    token = session.get("last_oauth_token")
    if not token:
        return jsonify({"status": "empty", "message": "No OAuth token captured in this session."}), 404
    return jsonify({"status": "ok", "token": token})


@main_bp.route("/api/oauth/x-token")
@admin_required
def twitter_oauth_token():
    token = session.get("last_oauth_token_twitter") or session.get("last_oauth_token")
    if not token or token.get("provider") != "twitter":
        return jsonify({"status": "empty", "message": "No Twitter/X OAuth token captured in this session."}), 404
    return jsonify({"status": "ok", "token": token})


@main_bp.route('/logout')
def logout():
    """Logout route to clear the session."""

    try:
        session.pop('authenticated', None)
        session.pop('user_email', None)
        session.pop('auth_provider', None)
        session.pop('role', None)
        session.pop('permissions', None)
        session.pop('display_name', None)
        session.pop('pending_user_id', None)
        session.pop('pending_oauth', None)
        logger.info("Admin logged out successfully.")
        flash("You have successfully logged out.", "success")
        return redirect(url_for('main.login'))  # Change to index if index ever exists as other than redirect
    except Exception as e:  # noqa: BLE001
        logger.error(f"Error logging out: {e}")
        flash(f"Error logging out: {e}", "danger")
        return redirect(url_for('main.shows'))


@main_bp.route("/login/claim", methods=["GET", "POST"])
def oauth_claim():
    pending = session.get("pending_oauth")
    if not pending:
        flash("No pending OAuth request found.", "warning")
        return redirect(url_for("main.login"))

    if request.method == "POST":
        display_name = request.form.get("display_name")
        if not display_name:
            flash("Please provide your name to continue.", "danger")
            return redirect(url_for("main.oauth_claim"))

        merge_candidate_id = pending.get("merge_candidate_id")
        target = None
        if merge_candidate_id:
            target = User.query.get(merge_candidate_id)
        if not target:
            target = _find_user(pending.get("provider"), pending.get("external_id"), pending.get("email"), None)

        if target:
            target.display_name = display_name or target.display_name
            if pending.get("email") and not target.email:
                target.email = pending.get("email")
            if pending.get("provider") and not target.provider:
                target.provider = pending.get("provider")
            if pending.get("external_id") and not target.external_id:
                target.external_id = str(pending.get("external_id"))
            target.rejected = False
            target.approval_status = target.approval_status or 'pending'
            target.requested_at = datetime.utcnow()
            _add_identity(target, pending.get("provider", "oauth"), pending.get("external_id"), pending.get("email"))
            db.session.commit()
            if target.approved or target.approval_status == 'approved':
                session.pop("pending_oauth", None)
                _complete_login(target)
                flash("Profile linked and logged in.", "success")
                return redirect(url_for('main.dashboard'))
            session['pending_user_id'] = target.id
        else:
            new_user = User(
                email=pending.get("email"),
                provider=pending.get("provider", "oauth"),
                external_id=str(pending.get("external_id")) if pending.get("external_id") else None,
                display_name=display_name,
                approved=False,
                rejected=False,
                approval_status='pending',
                role=None,
                requested_at=datetime.utcnow(),
            )
            _add_identity(new_user, pending.get("provider", "oauth"), pending.get("external_id"), pending.get("email"))
            db.session.add(new_user)
            db.session.commit()
            session['pending_user_id'] = new_user.id

        session.pop("pending_oauth", None)
        flash("Request submitted. An admin will approve your access soon.", "info")
        return redirect(url_for("main.oauth_pending"))

    return render_template("oauth_claim.html", pending=pending)


@main_bp.route("/login/pending")
def oauth_pending():
    pending_id = session.get("pending_user_id")
    user = None
    if pending_id:
        user = User.query.get(pending_id)
    return render_template("oauth_pending.html", user=user)
