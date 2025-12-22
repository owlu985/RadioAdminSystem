from app import create_app

application = create_app()

with application.app_context():
    pass