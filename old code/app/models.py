from flask_sqlalchemy import SQLAlchemy
from datetime import datetime

db = SQLAlchemy()

class Show(db.Model):
	id = db.Column(db.Integer, primary_key=True)
	host_first_name = db.Column(db.String(50), nullable=False)
	host_last_name = db.Column(db.String(50), nullable=False)
	
	show_name = db.Column(db.String(100), nullable=True)
	genre = db.Column(db.String(50), nullable=True)
	description = db.Column(db.Text, nullable=True)
	
	start_date = db.Column(db.Date, nullable=False)
	end_date = db.Column(db.Date, nullable=False)
	start_time = db.Column(db.Time, nullable=False)
	end_time = db.Column(db.Time, nullable=False)
	days_of_week = db.Column(db.String(20), nullable=False)

class LogEntry(db.Model):
	__tablename__ = 'log_entry'

	id = db.Column(db.Integer, primary_key=True)

	# Optional relationship to a scheduled show
	show_id = db.Column(db.Integer, db.ForeignKey('show.id'), nullable=True)
	show = db.relationship('Show', backref=db.backref('logs', lazy=True))

	# Core log data
	timestamp = db.Column(db.DateTime, nullable=False, default=datetime.utcnow)
	entry_type = db.Column(db.String(20), nullable=False)
	# entry_type: music | psa | event

	title = db.Column(db.String(200), nullable=True)
	artist = db.Column(db.String(200), nullable=True)

	description = db.Column(db.Text, nullable=True)

	# Link to recording if available
	recording_file = db.Column(db.String(300), nullable=True)

