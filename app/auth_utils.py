from functools import wraps
from flask import session, flash, redirect, url_for


def admin_required(f):
    """Decorator to require admin authentication."""

    @wraps(f)
    def decorated_function(*args, **kwargs):
        if not session.get('authenticated'):
            flash("Please log in to access this page.", "danger")
            return redirect(url_for('main.login'))
        return f(*args, **kwargs)

    return decorated_function
