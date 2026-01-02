from flask import Blueprint, flash, redirect, render_template, request, url_for

from app.auth_utils import admin_required
from app.models import PodcastEpisode, WebsiteContent
from app.plugins import PluginInfo, ensure_plugin_record
from app import db

bp = Blueprint(
    "website_plugin",
    __name__,
    template_folder="templates",
)


@bp.route("/", methods=["GET", "POST"])
@admin_required
def manage():
    plugin = ensure_plugin_record("website_content")
    content = WebsiteContent.query.first()
    if not content:
        content = WebsiteContent(headline="", body="", image_url="")
        db.session.add(content)
        db.session.commit()

    if request.method == "POST":
        action = request.form.get("action")
        if action == "save_content":
            content.headline = request.form.get("headline", "").strip() or None
            content.body = request.form.get("body", "").strip() or None
            content.image_url = request.form.get("image_url", "").strip() or None
            db.session.commit()
            flash("Website content saved.", "success")
            return redirect(url_for("website_plugin.manage"))
        elif action == "add_podcast":
            title = request.form.get("podcast_title", "").strip()
            embed_code = request.form.get("podcast_embed", "").strip()
            description = request.form.get("podcast_description", "").strip() or None
            if not title or not embed_code:
                flash("Title and embed code are required for podcasts.", "danger")
            else:
                episode = PodcastEpisode(title=title, embed_code=embed_code, description=description)
                db.session.add(episode)
                db.session.commit()
                flash("Podcast added.", "success")
            return redirect(url_for("website_plugin.manage"))
        elif action == "delete_podcast":
            pod_id = request.form.get("podcast_id")
            if pod_id:
                episode = PodcastEpisode.query.get(int(pod_id))
                if episode:
                    db.session.delete(episode)
                    db.session.commit()
                    flash("Podcast removed.", "info")
            return redirect(url_for("website_plugin.manage"))

    podcasts = PodcastEpisode.query.order_by(PodcastEpisode.created_at.desc()).all()
    return render_template("plugin_website.html", plugin=plugin, content=content, podcasts=podcasts)


def register_plugin(app):
    # ensure the plugin record exists and register the blueprint under a dedicated prefix
    with app.app_context():
        ensure_plugin_record("website_content")
    app.register_blueprint(bp, url_prefix="/plugins/website")
    return PluginInfo(
        name="website_content",
        display_name="Website Content & Podcasts",
        blueprint=bp,
        url_prefix="/plugins/website",
        manage_endpoint="website_plugin.manage",
    )
