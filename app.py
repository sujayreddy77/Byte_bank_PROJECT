# app.py — cleaned version
import os
from datetime import datetime, timedelta, timezone, date
from dotenv import load_dotenv
from flask import (
    Flask, render_template, request, redirect, url_for, flash, session, jsonify
)
from flask_migrate import Migrate
from flask_mail import Mail
from sqlalchemy.orm import joinedload

# local app modules (no circular imports)
from extensions import db
# models imports must refer to extension-initialized db (models.py should use db = SQLAlchemy() from extensions)
from models import User, DataWallet, DataEntry, Transaction, DataItem, OTP
from utils import create_and_send_otp, verify_and_consume_otp

# -------------------------
# Configuration
# -------------------------
load_dotenv()


def create_app():
    """
    Application factory. Use this to create and configure the Flask app.
    """
    app = Flask(__name__, static_folder="static", template_folder="templates")
    app.secret_key = os.getenv("FLASK_SECRET_KEY", "dev-secret-key")

    # DB
    app.config['SQLALCHEMY_DATABASE_URI'] = os.getenv("DATABASE_URL", "sqlite:///re-bytebank21.db")
    app.config['SQLALCHEMY_TRACK_MODIFICATIONS'] = False

    # Mail (Flask-Mail) — use Gmail app password or any SMTP service
    app.config['MAIL_SERVER'] = os.getenv("MAIL_SERVER", "smtp.gmail.com")
    app.config['MAIL_PORT'] = int(os.getenv("MAIL_PORT", 587))
    app.config['MAIL_USE_TLS'] = os.getenv("MAIL_USE_TLS", "True") == "True"
    app.config['MAIL_USE_SSL'] = os.getenv("MAIL_USE_SSL", "False") == "True"

    # Real env keys for Flask-Mail
    app.config['MAIL_USERNAME'] = os.getenv("GMAIL_USER")
    app.config['MAIL_PASSWORD'] = os.getenv("GMAIL_APP_PASS")

    # init extensions
    db.init_app(app)
    mail = Mail(app)
    migrate = Migrate(app, db)

    # attach mail to app.extensions so helper functions using current_app can find it
    app.extensions['mail'] = mail

    # register routes from routes.py (import inside factory to avoid circular imports)
    # routes.py should export a function register_routes(app) that sets up app.route handlers
    from routes import register_routes
    register_routes(app)

    # create tables if missing (developer convenience)
    with app.app_context():
        db.create_all()

    return app


# -------------------------
# Helpers (app-agnostic)
# -------------------------
def current_user():
    """Return User instance for session['user_id'] or None."""
    uid = session.get('user_id')
    if not uid:
        return None
    return User.query.get(uid)


def ensure_wallet(user):
    """Ensure DataWallet exists for a user and return it. Safe if user is None."""
    if not user:
        return None

    # prefer relationship if available
    wallet = getattr(user, "wallet", None)
    if wallet:
        return wallet

    wallet = DataWallet.query.filter_by(user_id=user.id).first()
    if not wallet:
        wallet = DataWallet(user_id=user.id, balance_mb=0, total_purchased_mb=0, total_used_mb=0)
        db.session.add(wallet)
        db.session.commit()
    return wallet


def create_entry(user_id, amount_mb, source):
    """Create a DataEntry and update wallet summary."""
    now = datetime.utcnow()
    expiry = now + timedelta(days=7 if source == 'earned' else 30)
    entry = DataEntry(user_id=user_id, amount_mb=amount_mb, source=source, added_on=now, expiry_date=expiry)
    db.session.add(entry)

    wallet = DataWallet.query.filter_by(user_id=user_id).first()
    if not wallet:
        wallet = DataWallet(user_id=user_id, balance_mb=0, total_purchased_mb=0, total_used_mb=0)
        db.session.add(wallet)

    wallet.balance_mb = (wallet.balance_mb or 0) + amount_mb
    if source == 'purchased':
        wallet.total_purchased_mb = (wallet.total_purchased_mb or 0) + amount_mb

    db.session.commit()
    return entry


def get_active_entries(user):
    """Return DataEntry rows for a user that haven't expired."""
    if not user:
        return []
    now = datetime.utcnow()
    return DataEntry.query.filter(DataEntry.user_id == user.id, DataEntry.expiry_date >= now).all()


def cleanup_expired_entries(user):
    """Remove expired DataEntry rows for a user."""
    if not user:
        return
    now = datetime.utcnow()
    expired = DataEntry.query.filter(DataEntry.user_id == user.id, DataEntry.expiry_date < now).all()
    if not expired:
        return
    for e in expired:
        db.session.delete(e)
    db.session.commit()


def simulate_end_of_day_rollover(user):
    """
    If last_usage_date != today, reset used_today_mb and optionally
    credit leftover quota as 'earned' entry (this is app-specific logic).
    """
    if not user:
        return
    today = datetime.utcnow().date()
    if user.last_usage_date == today:
        return

    # Example policy: credit leftover daily quota to user as 'earned' entry (optional)
    leftover = max(0, (user.daily_quota_mb or 0) - (user.used_today_mb or 0))
    if leftover > 0:
        create_entry(user.id, leftover, 'earned')
        txn = Transaction(sender_id=None, receiver_id=user.id, amount_mb=leftover, note='Rollover (earned)')
        db.session.add(txn)

    user.used_today_mb = 0
    user.last_usage_date = today
    db.session.commit()


# -------------------------
# Run (development)
# -------------------------
if __name__ == '__main__':
    app = create_app()
    app.run(debug=True)

