import requests
from flask import current_app
from app.logger import init_logger

logger = init_logger()

class RadioDJClient:
	def __init__(self):
		self.enabled = current_app.config.get("RADIODJ_ENABLED", False)
		self.base_url = current_app.config.get("RADIODJ_API_URL")
		self.api_key = current_app.config.get("RADIODJ_API_KEY")
		self.timeout = current_app.config.get("RADIODJ_TIMEOUT_SECONDS", 3)

	def _headers(self):
		headers = {}
		if self.api_key:
			headers["Authorization"] = f"Bearer {self.api_key}"
		return headers

	def _safe_request(self, method, endpoint, **kwargs):
		if not self.enabled:
			logger.info("RadioDJ disabled; skipping request.")
			return None

		try:
			response = requests.request(
				method,
				f"{self.base_url}{endpoint}",
				headers=self._headers(),
				timeout=self.timeout,
				**kwargs
			)
			response.raise_for_status()
			return response.json()
		except Exception as e:
			logger.warning(f"RadioDJ request failed: {e}")
			return None
