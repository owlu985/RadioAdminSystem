from flask import Blueprint, render_template
#from .routes import admin_required  # optional; remove if DJs don’t log in
from .models import Show
from datetime import datetime

logs_dj_bp = Blueprint('logs_dj', __name__, url_prefix='/dj')

@logs_dj_bp.route('/log')
def dj_log():
	"""
	DJ logging interface.
	Authentication optional depending on station policy.
	"""
	return render_template(
		'dj_log.html',
		now=datetime.now().strftime('%Y-%m-%dT%H:%M')
	)
