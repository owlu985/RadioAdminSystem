from flask_sqlalchemy import SQLAlchemy
from datetime import datetime

db = SQLAlchemy()

show_dj = db.Table(
    "show_dj",
    db.Column("show_id", db.Integer, db.ForeignKey("show.id"), primary_key=True),
    db.Column("dj_id", db.Integer, db.ForeignKey("dj.id"), primary_key=True),
)


class DJ(db.Model):
    __tablename__ = "dj"

    id = db.Column(db.Integer, primary_key=True)
    first_name = db.Column(db.String(64), nullable=False)
    last_name = db.Column(db.String(64), nullable=False)
    bio = db.Column(db.Text, nullable=True)
    photo_url = db.Column(db.String(255), nullable=True)

    shows = db.relationship("Show", secondary=show_dj, back_populates="djs")


class LogSheet(db.Model):
    __tablename__ = "log_sheet"

    id = db.Column(db.Integer, primary_key=True)
    dj_first_name = db.Column(db.String(64), nullable=False)
    dj_last_name = db.Column(db.String(64), nullable=False)
    show_name = db.Column(db.String(128), nullable=False)
    show_date = db.Column(db.Date, nullable=False)
    created_at = db.Column(db.DateTime, default=datetime.utcnow, nullable=False)


class Show(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    host_first_name = db.Column(db.String(50), nullable=False)
    host_last_name = db.Column(db.String(50), nullable=False)

    show_name = db.Column(db.String(100), nullable=True)
    genre = db.Column(db.String(50), nullable=True)
    description = db.Column(db.Text, nullable=True)
    is_regular_host = db.Column(db.Boolean, default=True, nullable=False)

    start_date = db.Column(db.Date, nullable=False)
    end_date = db.Column(db.Date, nullable=False)
    start_time = db.Column(db.Time, nullable=False)
    end_time = db.Column(db.Time, nullable=False)
    days_of_week = db.Column(db.String(20), nullable=False)

    djs = db.relationship("DJ", secondary=show_dj, back_populates="shows")

class ShowRun(db.Model):
    __tablename__ = "show_run"

    id = db.Column(db.Integer, primary_key=True)

    dj_first_name = db.Column(db.String(64), nullable=False)
    dj_last_name = db.Column(db.String(64), nullable=False)

    show_name = db.Column(db.String(128), nullable=False)

    start_time = db.Column(db.DateTime, nullable=False)
    end_time = db.Column(db.DateTime, nullable=True)

    created_at = db.Column(
        db.DateTime,
        default=datetime.utcnow,
        nullable=False
    )

    classification = db.Column(db.String(32), nullable=True)
    classification_reason = db.Column(db.String(128), nullable=True)
    avg_db = db.Column(db.Float, nullable=True)
    silence_ratio = db.Column(db.Float, nullable=True)
    automation_ratio = db.Column(db.Float, nullable=True)
    flagged_missed = db.Column(db.Boolean, default=False, nullable=False)

    def __repr__(self):
        return (
            f"<ShowRun {self.show_name} "
            f"{self.dj_first_name} {self.dj_last_name} "
            f"{self.start_time}>"
        )


class LogEntry(db.Model):
    __tablename__ = "log_entry"

    id = db.Column(db.Integer, primary_key=True)

    show_run_id = db.Column(
        db.Integer,
        db.ForeignKey("show_run.id"),
        nullable=True
    )
    log_sheet_id = db.Column(
        db.Integer,
        db.ForeignKey("log_sheet.id"),
        nullable=True
    )

    timestamp = db.Column(db.DateTime, nullable=False)
    message = db.Column(db.String(255), nullable=False)
    entry_time = db.Column(db.Time, nullable=True)

    # Optional metadata for PSA/news compliance and file linkage
    entry_type = db.Column(db.String(32), nullable=True)  # psa | live_read | music | news | probe
    title = db.Column(db.String(200), nullable=True)
    artist = db.Column(db.String(200), nullable=True)
    recording_file = db.Column(db.String(300), nullable=True)
    description = db.Column(db.Text, nullable=True)

    show_run = db.relationship("ShowRun", backref="log_entries")
    log_sheet = db.relationship("LogSheet", backref="entries")


class StreamProbe(db.Model):
    __tablename__ = "stream_probe"

    id = db.Column(db.Integer, primary_key=True)
    show_run_id = db.Column(
        db.Integer,
        db.ForeignKey("show_run.id"),
        nullable=True
    )
    classification = db.Column(db.String(32), nullable=False)
    reason = db.Column(db.String(128), nullable=True)
    avg_db = db.Column(db.Float, nullable=False)
    silence_ratio = db.Column(db.Float, nullable=False)
    automation_ratio = db.Column(db.Float, nullable=False)
    created_at = db.Column(db.DateTime, default=datetime.utcnow, nullable=False)

    show_run = db.relationship("ShowRun", backref="probes")
