from flask import Flask
from .views import register_views

def start_admin(config, capabilities):
    if not config["admin_panel"]["enabled"]:
        return

    app = Flask(__name__)
    register_views(app, config, capabilities)

    app.run(
        host=config["rest_api"]["host"],
        port=config["rest_api"]["port"],
        debug=False
    )
