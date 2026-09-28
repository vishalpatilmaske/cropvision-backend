from app.routes.admin_routes import admin_bp
from app.routes.advisory_routes import advisory_bp
from app.routes.assistant_routes import assistant_bp
from app.routes.auth_routes import auth_bp
from app.routes.disease_routes import disease_bp
from app.routes.farm_routes import farm_bp
from app.routes.recommendation_routes import recommendation_bp
from app.routes.weather_routes import weather_bp


def register_blueprints(app):
    app.register_blueprint(admin_bp)
    app.register_blueprint(advisory_bp)
    app.register_blueprint(assistant_bp)
    app.register_blueprint(auth_bp)
    app.register_blueprint(disease_bp)
    app.register_blueprint(farm_bp)
    app.register_blueprint(recommendation_bp)
    app.register_blueprint(weather_bp)
