import csv

def check_psa_compliance(log_path, min_psas=2):
    psa_count = 0

    with open(log_path, newline="", encoding="utf-8") as f:
        reader = csv.DictReader(f)
        for row in reader:
            if row.get("Type", "").lower() == "psa":
                psa_count += 1

    if psa_count < min_psas:
        return {
            "flag": "psa_non_compliance",
            "confidence": "medium",
            "reason": f"only_{psa_count}_psas"
        }

    return None
