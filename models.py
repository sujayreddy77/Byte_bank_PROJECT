from datetime import datetime, timezone
from werkzeug.security import generate_password_hash, check_password_hash
from extensions import db

class User(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    name = db.Column(db.String(120), nullable=False)
    email = db.Column(db.String(200), unique=True)
    mobile = db.Column(db.String(20), unique=True)
    password_hash = db.Column(db.String(255))
    created_at = db.Column(db.DateTime(timezone=True), default=lambda: datetime.now(timezone.utc))
    last_login = db.Column(db.DateTime(timezone=True))
    wallet = db.relationship('DataWallet', back_populates='user', uselist=False)
    daily_quota_mb = db.Column(db.Integer, default=2048)
    used_today_mb = db.Column(db.Integer, default=0)
    last_usage_date = db.Column(db.Date(), default=lambda: datetime.now(timezone.utc).date())
    total_used_mb = db.Column(db.Integer, default=0) #cumulative all-time usage

    is_admin = db.Column(db.Boolean, default=False)

    def set_password(self, pw):
        self.password_hash = generate_password_hash(pw)

    def check_password(self, pw):
        return check_password_hash(self.password_hash, pw)


class DataWallet(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    user_id = db.Column(db.Integer, db.ForeignKey('user.id'), unique=True, nullable=False)
    balance_mb = db.Column(db.Integer, default=0)
    total_purchased_mb = db.Column(db.Integer, default=0)
    total_used_mb = db.Column(db.Integer, default=0)
    created_at = db.Column(db.DateTime, default=lambda: datetime.now(timezone.utc))

    user = db.relationship('User', back_populates='wallet')

class DataEntry(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    user_id = db.Column(db.Integer, db.ForeignKey('user.id'))
    amount_mb = db.Column(db.Integer)
    source = db.Column(db.String(50))
    added_on = db.Column(db.DateTime(timezone=True), default=lambda: datetime.now(timezone.utc))
    expiry_date = db.Column(db.DateTime(timezone=True))

    user = db.relationship("User", backref="data_entries")


class Transaction(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    sender_id = db.Column(db.Integer, db.ForeignKey('user.id'), nullable=True)
    receiver_id = db.Column(db.Integer, db.ForeignKey('user.id'), nullable=True)
    amount_mb = db.Column(db.Integer)
    note = db.Column(db.String(200))
    timestamp = db.Column(db.DateTime(timezone=True), default=lambda: datetime.now(timezone.utc))

    sender = db.relationship("User", foreign_keys=[sender_id])
    receiver = db.relationship("User", foreign_keys=[receiver_id])


class DataItem(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    title = db.Column(db.String(120))
    description = db.Column(db.String(300))
    price = db.Column(db.Float)
    seller_id = db.Column(db.Integer, db.ForeignKey('user.id'))
    seller = db.relationship("User")


class OTP(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    identifier = db.Column(db.String(256), nullable=False)
    otp = db.Column(db.String(10), nullable=False)
    purpose = db.Column(db.String(32), nullable=False)
    expires_at = db.Column(db.DateTime(timezone=True), nullable=False)
    created_at = db.Column(db.DateTime(timezone=True), default=lambda: datetime.now(timezone.utc))

    def is_expired(self):
        return datetime.now(timezone.utc) > self.expires_at