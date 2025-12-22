from flask_sqlalchemy import SQLAlchemy

db = SQLAlchemy()

class Show(db.Model):
	id = db.Column(db.Integer, primary_key=True)
	host_first_name = db.Column(db.String(50), nullable=False)
	host_last_name = db.Column(db.String(50), nullable=False)
	start_date = db.Column(db.Date, nullable=False)
	end_date = db.Column(db.Date, nullable=False)
	start_time = db.Column(db.Time, nullable=False)
	end_time = db.Column(db.Time, nullable=False)
	days_of_week = db.Column(db.String(20), nullable=False)