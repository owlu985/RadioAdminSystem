import csv
from datetime import datetime

def export_artist_report(report, out_dir):
    filename = f"artist_frequency_{datetime.now().strftime('%Y%m%d_%H%M')}.csv"
    path = f"{out_dir}/{filename}"

    with open(path, "w", newline="", encoding="utf-8") as f:
        writer = csv.writer(f)
        writer.writerow(["Artist", "Total Plays", "Days Played"])

        for row in sorted(report, key=lambda r: r["plays"], reverse=True):
            writer.writerow([
                row["artist"],
                row["plays"],
                row["days_played"]
            ])

    return path
