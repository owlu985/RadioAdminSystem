import json
from typing import Any, Optional


def deserialize_json_field(raw: Optional[str]) -> Optional[Any]:
    if not raw:
        return None
    try:
        return json.loads(raw)
    except json.JSONDecodeError:
        return {"raw": raw}
