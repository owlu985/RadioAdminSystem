from datetime import datetime
from app.models import db, ShowRun


def start_show_run(dj_first_name, dj_last_name, show_name):
    """
    Create and persist a new ShowRun.
    """
    show_run = ShowRun(
        dj_first_name=dj_first_name.strip(),
        dj_last_name=dj_last_name.strip(),
        show_name=show_name.strip(),
        start_time=datetime.utcnow()
    )

    db.session.add(show_run)
    db.session.commit()

    return show_run


def end_show_run(show_run_id):
    """
    End an existing ShowRun by setting end_time.
    """
    show_run = ShowRun.query.get(show_run_id)

    if not show_run:
        raise ValueError(f"ShowRun {show_run_id} not found")

    if show_run.end_time is not None:
        return show_run  # already ended

    show_run.end_time = datetime.utcnow()
    db.session.commit()

    return show_run
