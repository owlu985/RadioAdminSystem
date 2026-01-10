from flask import Blueprint, flash, redirect, render_template, request, url_for

from app.auth_utils import admin_required
from app.models import (
    PodcastEpisode,
    PressFeature,
    WebsiteArticle,
    WebsiteBanner,
    WebsiteContent,
)
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

    banner = WebsiteBanner.query.first()

    def resequence_articles():
        articles = WebsiteArticle.query.order_by(WebsiteArticle.position, WebsiteArticle.id).all()
        for idx, art in enumerate(articles, start=1):
            art.position = idx
        db.session.commit()

    def resequence_press():
        features = PressFeature.query.order_by(PressFeature.position, PressFeature.id).all()
        for idx, feat in enumerate(features, start=1):
            feat.position = idx
        db.session.commit()

    def sync_hero_from_articles():
        first_article = (
            WebsiteArticle.query.order_by(WebsiteArticle.position, WebsiteArticle.id).first()
        )
        if first_article:
            content.headline = first_article.title
            content.body = first_article.body
            content.image_url = first_article.image_url
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
        elif action == "save_banner":
            message = request.form.get("banner_message", "").strip()
            link = request.form.get("banner_link", "").strip() or None
            tone = request.form.get("banner_tone", "").strip() or None
            if not banner:
                banner_obj = WebsiteBanner(message=message or None, link=link, tone=tone or None)
                db.session.add(banner_obj)
            else:
                banner.message = message or None
                banner.link = link
                banner.tone = tone or None
            db.session.commit()
            flash("Banner updated.", "success")
            return redirect(url_for("website_plugin.manage"))
        elif action == "add_article":
            title = request.form.get("article_title", "").strip()
            body = request.form.get("article_body", "").strip() or None
            image_url = request.form.get("article_image", "").strip() or None
            if not title:
                flash("Title is required for an article.", "danger")
            else:
                max_pos = db.session.query(db.func.max(WebsiteArticle.position)).scalar() or 0
                art = WebsiteArticle(title=title, body=body, image_url=image_url, position=max_pos + 1)
                db.session.add(art)
                db.session.commit()
                sync_hero_from_articles()
                flash("Article added.", "success")
            return redirect(url_for("website_plugin.manage"))
        elif action == "update_article":
            art_id = request.form.get("article_id")
            if art_id:
                art = WebsiteArticle.query.get(int(art_id))
                if art:
                    art.title = request.form.get("article_title", "").strip() or art.title
                    art.body = request.form.get("article_body", "").strip() or None
                    art.image_url = request.form.get("article_image", "").strip() or None
                    db.session.commit()
                    sync_hero_from_articles()
                    flash("Article updated.", "success")
            return redirect(url_for("website_plugin.manage"))
        elif action == "delete_article":
            art_id = request.form.get("article_id")
            if art_id:
                art = WebsiteArticle.query.get(int(art_id))
                if art:
                    db.session.delete(art)
                    db.session.commit()
                    resequence_articles()
                    sync_hero_from_articles()
                    flash("Article removed.", "info")
            return redirect(url_for("website_plugin.manage"))
        elif action == "move_article":
            art_id = request.form.get("article_id")
            direction = request.form.get("direction")
            if art_id and direction in {"up", "down"}:
                art = WebsiteArticle.query.get(int(art_id))
                if art:
                    delta = -1 if direction == "up" else 1
                    swap_pos = art.position + delta
                    swap = WebsiteArticle.query.filter_by(position=swap_pos).first()
                    if swap:
                        art.position, swap.position = swap.position, art.position
                        db.session.commit()
                    resequence_articles()
                    sync_hero_from_articles()
            return redirect(url_for("website_plugin.manage"))
        elif action == "add_press":
            name = request.form.get("press_name", "").strip()
            url = request.form.get("press_url", "").strip()
            logo = request.form.get("press_logo", "").strip() or None
            if not name or not url:
                flash("Name and URL are required for press features.", "danger")
            else:
                max_pos = db.session.query(db.func.max(PressFeature.position)).scalar() or 0
                feat = PressFeature(name=name, url=url, logo=logo, position=max_pos + 1)
                db.session.add(feat)
                db.session.commit()
                flash("Press feature added.", "success")
            return redirect(url_for("website_plugin.manage"))
        elif action == "delete_press":
            press_id = request.form.get("press_id")
            if press_id:
                feat = PressFeature.query.get(int(press_id))
                if feat:
                    db.session.delete(feat)
                    db.session.commit()
                    resequence_press()
                    flash("Press feature removed.", "info")
            return redirect(url_for("website_plugin.manage"))
        elif action == "move_press":
            press_id = request.form.get("press_id")
            direction = request.form.get("direction")
            if press_id and direction in {"up", "down"}:
                feat = PressFeature.query.get(int(press_id))
                if feat:
                    delta = -1 if direction == "up" else 1
                    swap_pos = feat.position + delta
                    swap = PressFeature.query.filter_by(position=swap_pos).first()
                    if swap:
                        feat.position, swap.position = swap.position, feat.position
                        db.session.commit()
                    resequence_press()
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

    articles = WebsiteArticle.query.order_by(WebsiteArticle.position, WebsiteArticle.id).all()
    press = PressFeature.query.order_by(PressFeature.position, PressFeature.id).all()
    podcasts = PodcastEpisode.query.order_by(PodcastEpisode.created_at.desc()).all()
    return render_template(
        "plugin_website.html",
        plugin=plugin,
        content=content,
        banner=banner,
        articles=articles,
        press=press,
        podcasts=podcasts,
    )


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
