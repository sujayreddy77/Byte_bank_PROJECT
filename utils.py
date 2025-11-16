import random
import smtplib
from datetime import datetime, timedelta, timezone
from email.mime.text import MIMEText
from flask import current_app
from extensions import db
from models import OTP


# ------------------------------
# Generate OTP
# ------------------------------
def generate_otp():
    return f"{random.randint(100000, 999999)}"


# ------------------------------
# Send Email (Gmail SMTP)
# ------------------------------
def send_email_smtp(to_email, subject, body):
    try:
        username = current_app.config["MAIL_USERNAME"]
        password = current_app.config["MAIL_PASSWORD"]

        msg = MIMEText(body)
        msg["Subject"] = subject
        msg["From"] = username
        msg["To"] = to_email

        with smtplib.SMTP("smtp.gmail.com", 587) as server:
            server.ehlo()
            server.starttls()
            server.ehlo()
            server.login(username, password)
            server.send_message(msg)

        return True
    except Exception as e:
        print("SMTP ERROR:", e)
        return False


# ------------------------------
# Create + Send OTP
# ------------------------------
def create_and_send_otp(identifier, purpose="register", otp_ttl_minutes=5):
    otp_code = generate_otp()
    expires_at = datetime.now(timezone.utc) + timedelta(minutes=otp_ttl_minutes)

    # Delete old OTP entries
    OTP.query.filter_by(identifier=identifier, purpose=purpose).delete()
    db.session.commit()

    # Save new OTP
    new_otp = OTP(
        identifier=identifier,
        otp=otp_code,
        purpose=purpose,
        expires_at=expires_at
    )
    db.session.add(new_otp)
    db.session.commit()

    # If identifier is NOT email → return OTP in dev mode
    if "@" not in identifier:
        return True, "dev", otp_code

    # Try sending email
    sent = send_email_smtp(identifier, "ByteBank OTP", f"Your OTP is {otp_code}")

    if sent:
        return True, "email", otp_code
    else:
        return True, "dev", otp_code


# ------------------------------
# Verify OTP
# ------------------------------
def verify_and_consume_otp(identifier, otp_value, purpose):
    record = OTP.query.filter_by(identifier=identifier, purpose=purpose) \
                      .order_by(OTP.created_at.desc()).first()

    if not record:
        return False, "No OTP found"

    now = datetime.now(timezone.utc)
    exp = record.expires_at

    if exp.tzinfo is None:
        exp = exp.replace(tzinfo=timezone.utc)

    if now > exp:
        db.session.delete(record)
        db.session.commit()
        return False, "OTP expired"

    if str(record.otp).strip() != str(otp_value).strip():
        return False, "Invalid OTP"

    db.session.delete(record)
    db.session.commit()
    return True, "OTP verified"