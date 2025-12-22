def normalize_artist(name: str) -> str:
    if not name:
        return ""
    return " ".join(name.strip().lower().split())
