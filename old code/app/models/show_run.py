from app.models import db
from datetime import datetime

class ShowRun(db.Model):
	id = db.Column(db.Integer, primary_key=True)

	show_id = db.Column(db.Integer, nullable=False)
	run_date = db.Column(db.Date, nullable=False)

	recording_path = db.Column(db.String(255))
	analysis_path = db.Column(db.String(255))

	classification = db.Column(db.String(32))  # live_show, missed_show, automation_only
	reason = db.Column(db.String(128))

	created_at = db.Column(db.DateTime, default=datetime.utcnow)
