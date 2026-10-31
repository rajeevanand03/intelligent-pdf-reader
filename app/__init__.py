import logging
import os

from flask import Flask, jsonify

from config import Config


def create_app(config_object=Config):
    app = Flask(__name__)
    app.config.from_object(config_object)

    for key in ("DATA_DIR", "UPLOAD_DIR", "OUTPUT_DIR", "SESSION_DIR", "CACHE_DIR"):
        os.makedirs(app.config[key], exist_ok=True)

    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s")

    from .services.job_manager import JobManager
    from .services.session_store import SessionStore

    app.extensions["session_store"] = SessionStore(app.config)
    app.extensions["job_manager"] = JobManager(app.config, app.extensions["session_store"])
    app.extensions["session_store"].cleanup_expired()

    from .routes.api import api_bp
    from .routes.pages import pages_bp

    app.register_blueprint(pages_bp)
    app.register_blueprint(api_bp, url_prefix="/api")

    @app.errorhandler(413)
    def too_large(_e):
        return jsonify(error=f"File exceeds the {app.config['MAX_FILE_SIZE_MB']} MB limit."), 413

    return app
