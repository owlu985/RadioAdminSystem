@bp.route('/api/logs/submit', methods=['POST'])
def submit_log():
	try:
		data = request.json
		entry = LogEntry(
			show_id=data.get("show_id"),
			timestamp=datetime.fromisoformat(data["timestamp"]),
			entry_type=data["type"],
			title=data.get("title"),
			artist=data.get("artist"),
			description=data.get("description")
		)
		db.session.add(entry)
		db.session.commit()
		return {"status": "ok"}
	except Exception as e:
		current_app.logger.error(e)
		return {"status": "error", "message": str(e)}, 500
