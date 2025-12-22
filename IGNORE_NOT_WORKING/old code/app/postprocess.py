from datetime import date
from app.models import db
from app.models.show_run import ShowRun
from app.logger import init_logger

logger = init_logger()

def handle_completed_recording(
	show,
	recording_path,
	analysis_result,
	analysis_json_path=None
):
	"""
	Called AFTER a show recording finishes and analysis is complete.
	Safely writes one ShowRun entry.
	"""

	try:
		run = ShowRun(
			show_id=show.id,
			run_date=date.today(),
			recording_path=recording_path,
			analysis_path=analysis_json_path,
			classification=analysis_result.get("classification"),
			reason=analysis_result.get("reason")
		)

		db.session.add(run)
		db.session.commit()

		logger.info(
			f"ShowRun saved for show {show.id} "
			f"({analysis_result.get('classification')})"
		)

	except Exception as e:
		logger.error(f"Failed to save ShowRun: {e}")
