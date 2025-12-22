from flask import Blueprint, request, current_app, jsonify
from datetime import datetime
from .models import db, LogEntry

logs_api_bp = Blueprint('logs_api', __name__, url_prefix='/api/logs')

@logs_api_bp.route('/submit', methods=['POST'])
def submit_log_entry():
	"""
	Accepts JSON payloads from DJ logging interface.
	Fails gracefully if anything goes wrong.
	"""
	try:
		data = request.get_json(force=True)

		entry = LogEntry(
			show_id=data.get('show_id'),
			timestamp=datetime.fromisoformat(data['timestamp']),
			entry_type=data['entry_type'],
			title=data.get('title'),
			artist=data.get('artist'),
			description=data.get('description'),
			recording_file=data.get('recording_file')
		)

		db.session.add(entry)
		db.session.commit()

		return jsonify({"status": "ok"}), 201

	except Exception as e:
		current_app.logger.error(f"Log submission failed: {e}")
		return jsonify({
			"status": "error",
			"message": "Log submission failed"
		}), 500
