from .client import RadioDJClient

def get_automation_status():
	"""
	Returns:
	- "automation"
	- "live"
	- None (unknown)
	"""
	client = RadioDJClient()
	data = client._safe_request("GET", "/status")

	if not data:
		return None

	return data.get("mode")  # depends on RadioDJ API
