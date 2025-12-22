import json
import os
from dataclasses import asdict

def write_analysis(analysis, recording_path, analysis_dir):
    os.makedirs(analysis_dir, exist_ok=True)

    base = os.path.basename(recording_path)
    name, _ = os.path.splitext(base)

    out_path = os.path.join(analysis_dir, f"{name}.analysis.json")

    with open(out_path, "w") as f:
        json.dump(asdict(analysis), f, indent=2)

    return out_path
