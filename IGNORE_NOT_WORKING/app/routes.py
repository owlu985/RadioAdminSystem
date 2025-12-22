from flask import Blueprint, render_template, request, redirect, url_for, flash, session, current_app
from .scheduler import refresh_schedule, pause_shows_until
from .utils import update_user_config
from datetime import datetime, time
from .models import db, Show
from sqlalchemy import case
from functools import wraps
from .logger import init_logger

main_bp = Blueprint('main', __name__)
logger = init_logger()
logger.info("Routes logger initialized.")

def admin_required(f):
	"""Decorator to require admin authentication."""
 
	@wraps(f)
	def decorated_function(*args, **kwargs):
		if not session.get('authenticated'):
			logger.warning("Unauthorized access attempt.")
			flash("Please log in to access this page.", "danger")
			return redirect(url_for('main.login'))
		return f(*args, **kwargs)
	return decorated_function

@main_bp.route('/')
def index():
	"""Redirect to the shows page."""

	logger.info("Redirecting to shows page.")
	return redirect(url_for('main.shows'))

# noinspection PyTypeChecker
@main_bp.route('/shows')
@admin_required
def shows():
	"""Render the shows database page sorted and paginated."""

	day_order = case(
		(Show.days_of_week == 'mon', 1),
		(Show.days_of_week == 'tue', 2),
		(Show.days_of_week == 'wed', 3),
		(Show.days_of_week == 'thu', 4),
		(Show.days_of_week == 'fri', 5),
		(Show.days_of_week == 'sat', 6),
		(Show.days_of_week == 'sun', 7)
	)

	page = request.args.get('page', 1, type=int)
	shows_column = Show.query.order_by(
		day_order,
		Show.start_time,
		Show.start_date
	).paginate(page=page, per_page=15)

	logger.info("Rendering shows database page.")
	return render_template('shows_database.html', shows=shows_column)

@main_bp.route('/login', methods=['GET', 'POST'])
def login():
	"""Login route for admin authentication."""

	if request.method == 'POST':
		username = request.form['username']
		password = request.form['password']
		if (username == current_app.config['ADMIN_USERNAME'] and
				password == current_app.config['ADMIN_PASSWORD']):
			session['authenticated'] = True
			logger.info("Admin logged in successfully.")
			flash("You are now logged in.", "success")
			return redirect(url_for('main.shows'))
		else:
			logger.warning("Invalid login attempt.")
			flash("Invalid credentials. Please try again.", "danger")
	return render_template('login.html')

@main_bp.route('/logout')
def logout():
	"""Logout route to clear the session."""

	try:
		session.pop('authenticated', None)
		logger.info("Admin logged out successfully.")
		flash("You have successfully logged out.", "success")
		return redirect(url_for('main.login'))  #Change to index if index ever exists as other than redirect
	except Exception as e:
		logger.error(f"Error logging out: {e}")
		flash(f"Error logging out: {e}", "danger")
		return redirect(url_for('main.shows'))

@main_bp.route('/show/add', methods=['GET', 'POST'])
@admin_required
def add_show():
	"""Route to add a new show."""

	try:
		if request.method == 'POST':
			start_date = request.form['start_date'] or current_app.config['DEFAULT_START_DATE']
			end_date = request.form['end_date'] or current_app.config['DEFAULT_END_DATE']
			start_time = request.form['start_time']
			end_time = request.form['end_time']

			start_date_obj = datetime.strptime(start_date, '%Y-%m-%d').date()
			end_date_obj = datetime.strptime(end_date, '%Y-%m-%d').date()
			start_time_obj = datetime.strptime(start_time, '%H:%M').time()
			end_time_obj = datetime.strptime(end_time, '%H:%M').time()

			today = datetime.today().date()
			if end_date_obj < today:
				flash("End date cannot be in the past!", "danger")
				return redirect(url_for('main.add_show'))

			#if end_time_obj == time(0, 0) and start_time_obj != time(0, 0):
			#	pass
			#elif end_time_obj <= start_time_obj:
			#	flash("End time cannot be before start time!", "danger")
			#	return redirect(url_for('main.add_show'))

			short_day_name = request.form['days_of_week'].lower()[:3]

			show = Show(
				host_first_name=request.form['host_first_name'],
				host_last_name=request.form['host_last_name'],
				start_date=start_date_obj,
				end_date=end_date_obj,
				start_time=start_time_obj,
				end_time=end_time_obj,
				days_of_week=short_day_name
			)
			db.session.add(show)
			db.session.commit()
			refresh_schedule()
			logger.info("Show added successfully.")
			flash("Show added successfully!", "success")
			return redirect(url_for('main.shows'))

		logger.info("Rendering add show page.")
		return render_template('add_show.html')
	except Exception as e:
		logger.error(f"Error adding show: {e}")
		flash(f"Error adding show: {e}", "danger")
		return redirect(url_for('main.shows'))

@main_bp.route('/show/edit/<int:id>', methods=['GET', 'POST'])
@admin_required
def edit_show(id):
	"""Route to edit an existing show."""

	show = Show.query.get_or_404(id)
	try:
		if request.method == 'POST':
			short_day_name = request.form['days_of_week'].lower()[:3]

			show.host_first_name = request.form['host_first_name']
			show.host_last_name = request.form['host_last_name']
			show.start_date = datetime.strptime(request.form['start_date'], '%Y-%m-%d').date()
			show.end_date = datetime.strptime(request.form['end_date'], '%Y-%m-%d').date()
			show.start_time = datetime.strptime(request.form['start_time'].strip(), '%H:%M').time()
			show.end_time = datetime.strptime(request.form['end_time'].strip(), '%H:%M').time()
			show.days_of_week = short_day_name

			db.session.commit()
			refresh_schedule()
			logger.info("Show updated successfully.")
			flash("Show updated successfully!", "success")

			return redirect(url_for('main.shows'))

		logger.info(f'Rendering edit show page for show {id}.')
		return render_template('edit_show.html', show=show)
	except Exception as e:
		logger.error(f"Error editing show: {e}")
		flash(f"Error editing show: {e}", "danger")
		return redirect(url_for('main.shows'))

@main_bp.route('/settings', methods=['GET', 'POST'])
@admin_required
def settings():
	"""Route to update the application settings."""

	if request.method == 'POST':
		try:
			updated_settings = {
				'ADMIN_USERNAME': request.form['admin_username'],
				'ADMIN_PASSWORD': request.form['admin_password'],
				'STREAM_URL': request.form['stream_url'],
				'OUTPUT_FOLDER': request.form['output_folder'],
				'DEFAULT_START_DATE': request.form['default_start_date'],
				'DEFAULT_END_DATE': request.form['default_end_date'],
				'AUTO_CREATE_SHOW_FOLDERS': 'auto_create_show_folders' in request.form,
			}

			update_user_config(updated_settings)

			flash("Settings updated successfully!", "success")
			return redirect(url_for('main.shows'))

		except Exception as e:
			logger.error(f"An error occurred while updating settings: {e}")
			flash(f"An error occurred while updating settings: {e}", "danger")
			return redirect(url_for('main.settings'))

	config = current_app.config
	settings_data = {
		'admin_username': config['ADMIN_USERNAME'],
		'admin_password': config['ADMIN_PASSWORD'],
		'stream_url': config['STREAM_URL'],
		'output_folder': config['OUTPUT_FOLDER'],
		'default_start_date': config['DEFAULT_START_DATE'],
		'default_end_date': config['DEFAULT_END_DATE'],
		'auto_create_show_folders': config['AUTO_CREATE_SHOW_FOLDERS'],
	}

	logger.info(f'Rendering settings page.')
	return render_template('settings.html', **settings_data)

@main_bp.route('/update_schedule', methods=['POST'])
@admin_required
def update_schedule():
	"""Route to refresh the schedule."""

	try:
		refresh_schedule()
		logger.info("Schedule updated successfully.")
		flash("Schedule updated successfully!", "success")
		return redirect(url_for('main.shows'))
	except Exception as e:
		logger.error(f"Error updating schedule: {e}")
		flash(f"Error updating schedule: {e}", "danger")
		return redirect(url_for('main.shows'))

@main_bp.route('/show/delete/<int:id>', methods=['POST'])
@admin_required
def delete_show(id):
	"""Route to delete a show."""

	try:
		show = Show.query.get_or_404(id)
		db.session.delete(show)
		db.session.commit()
		refresh_schedule()
		logger.info("Show deleted successfully.")
		flash("Show deleted successfully!", "success")
		return redirect(url_for('main.shows'))
	except Exception as e:
		logger.error(f"Error deleting show: {e}")
		flash(f"Error deleting show: {e}", "danger")
		return redirect(url_for('main.shows'))

@main_bp.route('/clear_all', methods=['POST'])
@admin_required
def clear_all():
	"""Route to clear all shows."""

	try:
		db.session.query(Show).delete()
		db.session.commit()
		refresh_schedule()
		logger.info("All shows have been deleted.")
		flash("All shows have been deleted.", "info")
		return redirect(url_for('main.shows'))
	except Exception as e:
		logger.error(f"Error deleting shows: {e}")
		flash(f"Error deleting shows: {e}", "danger")
		return redirect(url_for('main.shows'))

@main_bp.route('/pause', methods=['POST'])
@admin_required
def pause():
	"""Pause the recordings until the specified end date or indefinitely."""

	try:
		pause_end_date = request.form.get('pause_end_date')
		if pause_end_date:
			pause_end_date = datetime.strptime(pause_end_date, '%Y-%m-%d')
			pause_shows_until(pause_end_date)
			update_user_config({"PAUSE_SHOW_END_DATE": pause_end_date.strftime('%Y-%m-%d')})

		update_user_config({"PAUSE_SHOWS_RECORDING": True})

		flash(f"Recordings paused{' until ' + pause_end_date.strftime('%d-%m-%y') if pause_end_date else ' indefinitely'}.", "warning")
		logger.info(f"Recordings paused{' until ' + pause_end_date.strftime('%d-%m-%y') if pause_end_date else ' indefinitely'}.")
	except Exception as e:
		logger.error(f"Error pausing recordings: {e}")
		flash(f"Error pausing recordings: {e}", "danger")

	return redirect(url_for('main.settings'))

@main_bp.route('/resume', methods=['POST'])
@admin_required
def resume():
	"""Resume the recordings."""

	try:
		update_user_config({"PAUSE_SHOWS_RECORDING": False, "PAUSE_SHOW_END_DATE": None})
		flash("Recordings resumed.", "success")
		logger.info("Recordings resumed.")
	except Exception as e:
		logger.error(f"Error resuming recordings: {e}")
		flash(f"Error resuming recordings: {e}", "danger")
  
	return redirect(url_for('main.settings'))