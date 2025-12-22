from flask import render_template, send_file, request
from logs.reader import scan_logs
from logs.writer import csv_to_docx
import os
from logs.artist_report import generate_artist_report
from logs.artist_export import export_artist_report
from datetime import datetime

@app.route("/show/<filename>")
def show_detail(filename):
    log_path = os.path.join(LOG_DIR, filename)

    flags = evaluate_show_for_log(log_path, config)

    return render_template(
        "show_detail.html",
        filename=filename,
        flags=flags
    )


@app.route("/reports/artists")
def artist_report():
    start = request.args.get("start")
    end = request.args.get("end")

    start_date = datetime.strptime(start, "%Y-%m-%d").date() if start else None
    end_date = datetime.strptime(end, "%Y-%m-%d").date() if end else None

    report = generate_artist_report(
        config["paths"]["logs"],
        start_date,
        end_date
    )

    return render_template(
        "artist_report.html",
        report=report,
        start=start,
        end=end
    )


def register_views(app, config, capabilities):

    LOG_DIR = config["paths"]["logs"]

    @app.route("/")
    def index():
        sort = request.args.get("sort", "date")

        logs = scan_logs(LOG_DIR)

        if sort == "dj":
            logs.sort(key=lambda l: (l["dj_last"], l["dj_first"]))
        else:
            logs.sort(key=lambda l: l["start_time"], reverse=True)

        return render_template(
            "index.html",
            logs=logs,
            capabilities=capabilities
        )

    @app.route("/view/<filename>")
    def view_log(filename):
        path = os.path.join(LOG_DIR, filename)
        if not os.path.exists(path):
            return "Log not found", 404

        with open(path) as f:
            lines = f.readlines()

        return render_template("view.html", lines=lines, filename=filename)

    

    @app.route("/download/<filename>/<fmt>")
    def download(filename, fmt):
        path = os.path.join(LOG_DIR, filename)

        if fmt == "csv":
            return send_file(path, as_attachment=True)

        if fmt == "docx":
            docx_path = csv_to_docx(path)
            return send_file(docx_path, as_attachment=True)

        return "Invalid format", 400
