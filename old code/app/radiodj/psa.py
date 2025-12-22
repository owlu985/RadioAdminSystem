from .client import RadioDJClient

def list_psas():
	client = RadioDJClient()
	return client._safe_request("GET", "/psa")

def enable_psa(psa_id):
	client = RadioDJClient()
	return client._safe_request("POST", f"/psa/{psa_id}/enable")

def disable_psa(psa_id):
	client = RadioDJClient()
	return client._safe_request("POST", f"/psa/{psa_id}/disable")

def remove_psa(psa_id):
	client = RadioDJClient()
	return client._safe_request("DELETE", f"/psa/{psa_id}")

def import_psa(metadata, file_path):
	"""
	metadata = dict(title, category, length, etc)
	"""
	client = RadioDJClient()
	files = {"file": open(file_path, "rb")}
	data = metadata
	return client._safe_request("POST", "/psa/import", files=files, data=data)
