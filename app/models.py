from flask_sqlalchemy import SQLAlchemy
from datetime import datetime
from sqlalchemy.types import Text

db = SQLAlchemy()


class User(db.Model):
    __tablename__ = "user"

    id = db.Column(db.Integer, primary_key=True)
    email = db.Column(db.String(255), unique=True, nullable=False)
    provider = db.Column(db.String(50), nullable=False)
    external_id = db.Column(db.String(255), nullable=True)
    display_name = db.Column(db.String(255), nullable=True)
    role = db.Column(db.String(50), nullable=True)
    custom_role = db.Column(db.String(50), nullable=True)
    permissions = db.Column(db.Text, nullable=True)
    identities = db.Column(db.Text, nullable=True)
    approval_status = db.Column(db.String(32), default="pending", nullable=False)
    notification_email = db.Column(db.String(255), nullable=True)
    rejected = db.Column(db.Boolean, default=False, nullable=False)
    approved = db.Column(db.Boolean, default=False, nullable=False)
    requested_at = db.Column(db.DateTime, default=datetime.utcnow, nullable=False)
    approved_at = db.Column(db.DateTime, nullable=True)
    last_login_at = db.Column(db.DateTime, nullable=True)
    created_at = db.Column(db.DateTime, default=datetime.utcnow, nullable=False)

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
    description = db.Column(db.Text, nullable=True)
    photo_url = db.Column(db.String(255), nullable=True)

    shows = db.relationship("Show", secondary=show_dj, back_populates="djs")


class DJDisciplinary(db.Model):
    __tablename__ = "dj_disciplinary"

    id = db.Column(db.Integer, primary_key=True)
    dj_id = db.Column(db.Integer, db.ForeignKey("dj.id"), nullable=False)
    issued_at = db.Column(db.DateTime, default=datetime.utcnow, nullable=False)
    severity = db.Column(db.String(32), nullable=True)
    notes = db.Column(db.Text, nullable=True)
    action_taken = db.Column(db.Text, nullable=True)
    resolved = db.Column(db.Boolean, default=False, nullable=False)
    created_by = db.Column(db.String(255), nullable=True)

    dj = db.relationship("DJ", backref="disciplinary_records")


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


class MarathonEvent(db.Model):
    __tablename__ = "marathon_event"

    id = db.Column(db.Integer, primary_key=True)
    name = db.Column(db.String(128), nullable=False)
    safe_name = db.Column(db.String(128), nullable=False)
    start_time = db.Column(db.DateTime, nullable=False)
    end_time = db.Column(db.DateTime, nullable=False)
    chunk_hours = db.Column(db.Integer, default=2, nullable=False)
    status = db.Column(db.String(32), default="pending", nullable=False)
    job_ids = db.Column(db.Text, nullable=True)
    created_at = db.Column(db.DateTime, default=datetime.utcnow, nullable=False)
    canceled_at = db.Column(db.DateTime, nullable=True)

class ShowRun(db.Model):
    __tablename__ = "show_run"

    id = db.Column(db.Integer, primary_key=True)

    dj_first_name = db.Column(db.String(64), nullable=False)
    dj_last_name = db.Column(db.String(64), nullable=False)

    show_name = db.Column(db.String(128), nullable=False)

    start_time = db.Column(db.DateTime, nullable=False, index=True)
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
    created_at = db.Column(db.DateTime, default=datetime.utcnow, nullable=False, index=True)

    show_run = db.relationship("ShowRun", backref="probes")


class IcecastStat(db.Model):
    __tablename__ = "icecast_stat"

    id = db.Column(db.Integer, primary_key=True)
    listeners = db.Column(db.Integer, nullable=True)
    created_at = db.Column(db.DateTime, default=datetime.utcnow, nullable=False)


class DJAbsence(db.Model):
    __tablename__ = "dj_absence"

    id = db.Column(db.Integer, primary_key=True)
    dj_name = db.Column(db.String(128), nullable=False)
    show_name = db.Column(db.String(128), nullable=False)
    show_id = db.Column(db.Integer, db.ForeignKey("show.id"), nullable=True)
    start_time = db.Column(db.DateTime, nullable=False)
    end_time = db.Column(db.DateTime, nullable=False)
    replacement_name = db.Column(db.String(128), nullable=True)
    notes = db.Column(db.Text, nullable=True)
    status = db.Column(db.String(32), default="pending", nullable=False)  # pending|approved|rejected|resolved
    created_at = db.Column(db.DateTime, default=datetime.utcnow, nullable=False)

    show = db.relationship("Show", backref="absences")


class MusicAnalysis(db.Model):
    __tablename__ = "music_analysis"

    id = db.Column(db.Integer, primary_key=True)
    path = db.Column(db.String(500), unique=True, nullable=False)
    duration_seconds = db.Column(db.Float, nullable=True)
    peak_db = db.Column(db.Float, nullable=True)
    rms_db = db.Column(db.Float, nullable=True)
    peaks = db.Column(db.Text, nullable=True)  # JSON list of sample peaks for waveform previews
    bitrate = db.Column(db.Integer, nullable=True)
    hash = db.Column(db.String(64), nullable=True)
    missing_tags = db.Column(db.Boolean, default=False, nullable=False)
    created_at = db.Column(db.DateTime, default=datetime.utcnow, nullable=False)
    updated_at = db.Column(db.DateTime, default=datetime.utcnow, nullable=False)


class MusicCue(db.Model):
    __tablename__ = "music_cue"

    id = db.Column(db.Integer, primary_key=True)
    path = db.Column(db.String(500), unique=True, nullable=False)
    cue_in = db.Column(db.Float, nullable=True)
    intro = db.Column(db.Float, nullable=True)
    outro = db.Column(db.Float, nullable=True)
    cue_out = db.Column(db.Float, nullable=True)
    loop_in = db.Column(db.Float, nullable=True)
    loop_out = db.Column(db.Float, nullable=True)
    hook_in = db.Column(db.Float, nullable=True)
    hook_out = db.Column(db.Float, nullable=True)
    start_next = db.Column(db.Float, nullable=True)
    fade_in = db.Column(db.Float, nullable=True)
    fade_out = db.Column(db.Float, nullable=True)
    updated_at = db.Column(db.DateTime, default=datetime.utcnow, onupdate=datetime.utcnow, nullable=False)


class SavedSearch(db.Model):
    __tablename__ = "saved_search"

    id = db.Column(db.Integer, primary_key=True)
    name = db.Column(db.String(128), nullable=False)
    query = db.Column(db.String(255), nullable=False)
    filters = db.Column(Text, nullable=True)
    created_by = db.Column(db.String(255), nullable=True)
    created_at = db.Column(db.DateTime, default=datetime.utcnow, nullable=False)


class JobHealth(db.Model):
    __tablename__ = "job_health"

    id = db.Column(db.Integer, primary_key=True)
    name = db.Column(db.String(64), unique=True, nullable=False)
    failure_count = db.Column(db.Integer, default=0, nullable=False)
    restart_count = db.Column(db.Integer, default=0, nullable=False)
    last_failure_at = db.Column(db.DateTime, nullable=True)
    last_restart_at = db.Column(db.DateTime, nullable=True)
    last_failure_reason = db.Column(db.String(255), nullable=True)


class LiveReadCard(db.Model):
    __tablename__ = "live_read_card"

    id = db.Column(db.Integer, primary_key=True)
    title = db.Column(db.String(200), nullable=False)
    expires_on = db.Column(db.Date, nullable=True)
    copy = db.Column(db.Text, nullable=False)
    created_at = db.Column(db.DateTime, default=datetime.utcnow, nullable=False)
    updated_at = db.Column(db.DateTime, default=datetime.utcnow, onupdate=datetime.utcnow, nullable=False)


class ArchivistEntry(db.Model):
    __tablename__ = "archivist_entry"

    id = db.Column(db.Integer, primary_key=True)
    title = db.Column(db.String(255), nullable=True)
    artist = db.Column(db.String(255), nullable=True)
    album = db.Column(db.String(255), nullable=True)
    catalog_number = db.Column(db.String(128), nullable=True)
    notes = db.Column(db.Text, nullable=True)
    extra = db.Column(db.Text, nullable=True)
    created_at = db.Column(db.DateTime, default=datetime.utcnow, nullable=False)


class SocialPost(db.Model):
    __tablename__ = "social_post"

    id = db.Column(db.Integer, primary_key=True)
    content = db.Column(db.Text, nullable=False)
    platforms = db.Column(db.Text, nullable=True)  # JSON list of targeted platforms
    image_url = db.Column(db.String(500), nullable=True)
    image_path = db.Column(db.String(500), nullable=True)
    status = db.Column(db.String(32), default="pending", nullable=False)
    result_log = db.Column(db.Text, nullable=True)
    created_at = db.Column(db.DateTime, default=datetime.utcnow, nullable=False)
    sent_at = db.Column(db.DateTime, nullable=True)


class Plugin(db.Model):
    __tablename__ = "plugin"

    id = db.Column(db.Integer, primary_key=True)
    name = db.Column(db.String(100), unique=True, nullable=False)
    enabled = db.Column(db.Boolean, default=True, nullable=False)
    config = db.Column(db.Text, nullable=True)
    created_at = db.Column(db.DateTime, default=datetime.utcnow, nullable=False)
    updated_at = db.Column(db.DateTime, default=datetime.utcnow, onupdate=datetime.utcnow, nullable=False)


class WebsiteContent(db.Model):
    __tablename__ = "website_content"

    id = db.Column(db.Integer, primary_key=True)
    headline = db.Column(db.String(255), nullable=True)
    body = db.Column(db.Text, nullable=True)
    image_url = db.Column(db.String(500), nullable=True)
    updated_at = db.Column(db.DateTime, default=datetime.utcnow, onupdate=datetime.utcnow, nullable=False)


class WebsiteArticle(db.Model):
    __tablename__ = "website_article"

    id = db.Column(db.Integer, primary_key=True)
    title = db.Column(db.String(255), nullable=False)
    body = db.Column(db.Text, nullable=True)
    image_url = db.Column(db.String(500), nullable=True)
    position = db.Column(db.Integer, nullable=False, default=0)
    created_at = db.Column(db.DateTime, default=datetime.utcnow, nullable=False)


class PressFeature(db.Model):
    __tablename__ = "press_feature"

    id = db.Column(db.Integer, primary_key=True)
    name = db.Column(db.String(255), nullable=False)
    url = db.Column(db.String(1024), nullable=False)
    logo = db.Column(db.String(512), nullable=True)
    position = db.Column(db.Integer, nullable=False, default=0)
    created_at = db.Column(db.DateTime, default=datetime.utcnow, nullable=False)


class WebsiteBanner(db.Model):
    __tablename__ = "website_banner"

    id = db.Column(db.Integer, primary_key=True)
    message = db.Column(db.String(500), nullable=True)
    link = db.Column(db.String(1024), nullable=True)
    tone = db.Column(db.String(50), nullable=True)
    updated_at = db.Column(db.DateTime, default=datetime.utcnow, onupdate=datetime.utcnow, nullable=False)


class PodcastEpisode(db.Model):
    __tablename__ = "podcast_episode"

    id = db.Column(db.Integer, primary_key=True)
    title = db.Column(db.String(255), nullable=False)
    description = db.Column(db.Text, nullable=True)
    embed_code = db.Column(db.Text, nullable=False)
    created_at = db.Column(db.DateTime, default=datetime.utcnow, nullable=False)


class HostedAudio(db.Model):
    __tablename__ = "hosted_audio"

    id = db.Column(db.Integer, primary_key=True)
    title = db.Column(db.String(255), nullable=False)
    description = db.Column(db.Text, nullable=True)
    file_url = db.Column(db.String(512), nullable=False)
    backdrop_url = db.Column(db.String(512), nullable=True)
    created_at = db.Column(db.DateTime, default=datetime.utcnow, nullable=False)


class AutomationRule(db.Model):
    __tablename__ = "automation_rule"

    id = db.Column(db.Integer, primary_key=True)
    name = db.Column(db.String(255), nullable=False)
    match_tag = db.Column(db.String(255), nullable=True)
    days_of_week = db.Column(db.String(32), nullable=True)  # csv of mon,tue,wed,thu,fri,sat,sun
    start_time = db.Column(db.Time, nullable=True)
    end_time = db.Column(db.Time, nullable=True)
    action = db.Column(db.String(64), nullable=False, default="insert_top")
    radiodj_id = db.Column(db.String(64), nullable=True)
    radiodj_title = db.Column(db.String(255), nullable=True)
    enabled = db.Column(db.Boolean, default=True, nullable=False)
    created_at = db.Column(db.DateTime, default=datetime.utcnow, nullable=False)


class RemoteLinkSession(db.Model):
    __tablename__ = "remote_link_session"

    id = db.Column(db.Integer, primary_key=True)
    label = db.Column(db.String(255), nullable=False)
    passcode = db.Column(db.String(64), nullable=True)
    notes = db.Column(db.Text, nullable=True)
    created_at = db.Column(db.DateTime, default=datetime.utcnow, nullable=False)


class RemoteLinkSignal(db.Model):
    __tablename__ = "remote_link_signal"

    id = db.Column(db.Integer, primary_key=True)
    session_id = db.Column(db.Integer, db.ForeignKey("remote_link_session.id"), nullable=False, index=True)
    sender_id = db.Column(db.String(64), nullable=False)
    sender_label = db.Column(db.String(255), nullable=True)
    signal_type = db.Column(db.String(32), nullable=False)
    payload = db.Column(db.Text, nullable=False)
    created_at = db.Column(db.DateTime, default=datetime.utcnow, nullable=False, index=True)

    session = db.relationship("RemoteLinkSession", backref="signals")
