from flask import (
    Flask, render_template, request, redirect, url_for, flash, session, jsonify, current_app
)
from models import User, db, DataWallet, DataEntry, Transaction, DataItem, OTP
from utils import create_and_send_otp, verify_and_consume_otp
from app import current_user, ensure_wallet, create_entry, get_active_entries, cleanup_expired_entries, simulate_end_of_day_rollover
from datetime import datetime, timedelta, timezone, date
from dotenv import load_dotenv
from flask_mail import Mail,Message
from sqlalchemy.orm import joinedload

def send_otp(payload):
    from app import send_otp_email    # or wherever your function lives

    # This will send email OTP
    return send_otp_email(payload)


def register_routes(app):
    @app.route('/')
    def index():
        user = current_user()
        if user:
            return redirect(url_for('dashboard'))
        return render_template('index.html', user=None)

    # Static pages (templates should exist)
    @app.route('/login')
    def login_page():
        return render_template('login.html')

    @app.route('/register')
    def register_page():
        return render_template('register.html')

    @app.route('/otp_page')
    def otp_page():
        return render_template('otp.html')

    # ------- API: send/verify OTP -------
    @app.route('/api/send_otp', methods=['POST'])
    def api_send_otp():
     try:
        data = request.get_json() or {}
        identifier = (data.get('email') or data.get('mobile') or '').strip()

        if not identifier:
            return jsonify({"success": False, "message": "Email or mobile required."}), 400

        # Normalize mobile
        if '@' not in identifier:
            identifier = identifier.replace(" ", "").replace("+", "")
            if not identifier.isdigit():
                return jsonify({"success": False, "message": "Invalid mobile number."}), 400

        purpose = data.get('purpose', 'register')

        # Check duplicate for register
        if purpose == "register" and User.query.filter(
            (User.email == identifier) | (User.mobile == identifier)
        ).first():
            return jsonify({
                "success": False,
                "message": "This email/mobile is already registered."
            }), 400

        ok, method, otp_code = create_and_send_otp(identifier, purpose)

        response = {
            "success": True,
            "message": f"OTP sent to {identifier} via {method}."
        }

        if method == "dev":
            response["otp"] = otp_code

        return jsonify(response)

     except Exception as e:
        print("OTP ERROR:", e)
        return jsonify({"success": False, "message": "Server error sending OTP"}), 500
    
    @app.route('/api/verify_otp', methods=['POST'])
    def api_verify_otp():
        data = request.get_json() or {}
        identifier = (data.get('email') or data.get('mobile') or '').strip()
        otp_val = data.get('otp', '').strip()
        if not identifier or not otp_val:
            return jsonify({"success": False, "message": "Identifier and OTP required."}), 400

        ok, msg = verify_and_consume_otp(identifier, otp_val, purpose=data.get('purpose', 'register'))
        if ok:
            session['otp_verified'] = identifier
            return jsonify({"success": True, "message": msg})
        return jsonify({"success": False, "message": msg}), 400

    # ------- Register & Login (API) -------
    @app.route('/api/register', methods=['POST'])
    def api_register():
        data = request.get_json() or {}
        name = data.get('name', '').strip()
        identifier = (data.get('email') or data.get('mobile') or '').strip()
        password = data.get('password', '')

        if session.get('otp_verified') != identifier:
            return jsonify({"success": False, "message": "OTP not verified for this identifier."}), 400

        if User.query.filter((User.email == identifier) | (User.mobile == identifier)).first():
            return jsonify({"success": False, "message": "Identifier already registered."}), 400

        u = User(name=name)
        if "@" in identifier:
            u.email = identifier.lower()
        else:
            u.mobile = identifier
        u.set_password(password)
        db.session.add(u)
        db.session.commit()

        session.pop('otp_verified', None)
        session['user_id'] = u.id
        return jsonify({"success": True, "message": "Registration complete."})

    @app.route('/api/login', methods=['POST'])
    def api_login():
     data = request.get_json() or {}
     identifier = (data.get('email') or data.get('mobile') or '').strip()
     password = data.get('password', '')

     if not identifier or not password:
        return jsonify({"success": False, "message": "Identifier and password required."}), 400

     user = User.query.filter(
        (User.email == identifier) | (User.mobile == identifier)
     ).first()

     if not user or not user.check_password(password):
        return jsonify({"success": False, "message": "Invalid credentials."}), 401

    # ---------------------------------------
    # UPDATE LAST LOGIN TIME
    # ---------------------------------------
     from datetime import datetime
     user.last_login = datetime.utcnow()
     db.session.commit()

     session['user_id'] = user.id

     return jsonify({"success": True, "message": "Login successful."})

    # ------- Test email (dev) -------
    @app.route('/test_gmail', methods=['GET'])
    def test_gmail():
        recipient = app.config.get('MAIL_USERNAME')
        if not recipient:
            return jsonify({"success": False, "message": "MAIL_USERNAME not configured"}), 500
        try:
            msg = Message(subject="Test OTP from ByteBank", recipients=[recipient], body="Test email from ByteBank", sender=recipient)
            current_app.extensions['mail'].send(msg)
            return jsonify({"success": True, "message": f"Test email sent to {recipient}"})
        except Exception as e:
            app.logger.exception("Test email failed")
            return jsonify({"success": False, "message": "Failed to send test email. Check logs."}), 500

    # ------- Auth routes -------
    @app.route('/logout')
    def logout():
        session.pop('user_id', None)
        flash('Logged out', 'info')
        return redirect(url_for('index'))

    # ------- Dashboard & profile -------
    @app.route('/dashboard')
    def dashboard():
        user = current_user()
        if not user:
            return redirect(url_for('login_page'))

        simulate_end_of_day_rollover(user)
        wallet = ensure_wallet(user)
        cleanup_expired_entries(user)
        active_entries = get_active_entries(user)
        total_active_mb = sum(e.amount_mb for e in active_entries)
        expiring_soon = [e for e in active_entries if (e.expiry_date - datetime.utcnow()).days <= 3]
        remaining_today = max((user.daily_quota_mb or 0) - (user.used_today_mb or 0), 0)
        total_used_mb = user.used_today_mb or 0
        total_all_time = (user.used_today_mb or 0) + (wallet.total_used_mb or 0)

        return render_template(
            'dashboard.html',
            user=user,
            wallet=wallet,
            active_entries=active_entries,
            total_active_mb=total_active_mb,
            expiring_soon=expiring_soon,
            remaining_today=remaining_today,
            total_used_mb=total_used_mb,
            total_all_time=total_all_time,
            wallet_balance=wallet.balance_mb
        )

    @app.route('/profile')
    def profile():
        user = current_user()
        if not user:
            flash("Please log in to view your profile.", "warning")
            return redirect(url_for('login_page'))

        wallet = ensure_wallet(user)
        transactions = (
            Transaction.query.options(joinedload(Transaction.sender), joinedload(Transaction.receiver))
            .filter((Transaction.sender_id == user.id) | (Transaction.receiver_id == user.id))
            .order_by(Transaction.timestamp.desc()).limit(10).all()
        )
        return render_template("profile.html", user=user, wallet=wallet, total_all_time=user.total_used_mb or 0, transactions=transactions)
    @app.route('/update_profile', methods=['GET', 'POST'])
    def update_profile():
        user = current_user()
        if not user:
          flash("Please login to update your profile.", "error")
          return redirect(url_for('login'))

        if request.method == 'POST':
          user.name = request.form.get('name')
          user.email = request.form.get('email')
          db.session.commit()
          flash("Profile updated successfully.", "success")
          return redirect(url_for('profile'))

        return render_template('update_profile.html', user=user)


    # ------- Forgot / Reset password -------
    @app.route('/forgot', methods=['GET', 'POST'])
    def forgot():
     if request.method == 'POST':
        identifier = request.form.get('identifier', '').strip()
        if not identifier:
            flash("Enter email or mobile", "warning")
            return redirect(url_for('forgot'))

        # Check user
        user = User.query.filter(
            (User.email == identifier) | (User.mobile == identifier)
        ).first()
        if not user:
            flash("Account not found", "danger")
            return redirect(url_for('forgot'))

        # Save in session
        session['fp_identifier'] = identifier
        session['fp_step'] = 2

        # Send OTP
        create_and_send_otp(identifier, "forgot")

        flash("OTP sent to your email/mobile.", "info")
        return redirect(url_for('forgot_otp'))

     return render_template("forgot_password.html", step=1)
    

    @app.route('/forgot_otp', methods=['GET', 'POST'])
    def forgot_otp():
     identifier = session.get('fp_identifier')
     if not identifier:
        flash("Start from Forgot Password page", "danger")
        return redirect(url_for('forgot'))

     if request.method == 'POST':
        otp = request.form.get('otp', '').strip()
        if not otp:
            flash("Enter OTP", "danger")
            return redirect(url_for('forgot_otp'))

        ok, msg = verify_and_consume_otp(identifier, otp, "forgot")

        if not ok:
            flash(msg, "danger")
            return redirect(url_for('forgot_otp'))

        session['fp_step'] = 3
        flash("OTP verified. Set a new password.", "success")
        return redirect(url_for('forgot_new'))

     return render_template("forgot_password.html", step=2, identifier=identifier)


    @app.route('/forgot_new', methods=['GET', 'POST'])
    def forgot_new():
     identifier = session.get('fp_identifier')
     step = session.get('fp_step')

     if not identifier or step != 3:
        flash("Start from Forgot Password page", "danger")
        return redirect(url_for('forgot'))

     if request.method == 'POST':
        new_pass = request.form.get('new_password')
        confirm_pass = request.form.get('confirm_password')

        if not new_pass or not confirm_pass:
            flash("All fields required", "danger")
            return redirect(url_for('forgot_new'))

        if new_pass != confirm_pass:
            flash("Passwords do not match", "danger")
            return redirect(url_for('forgot_new'))

        # Update password
        user = User.query.filter(
            (User.email == identifier) | (User.mobile == identifier)
        ).first()

        if not user:
            flash("User not found", "danger")
            return redirect(url_for('forgot'))

        user.set_password(new_pass)
        db.session.commit()

        # Clear session
        session.pop('fp_identifier', None)
        session.pop('fp_step', None)

        flash("Password updated successfully. Login now.", "success")
        return redirect(url_for('login_page'))

     return render_template("forgot_password.html", step=3)
    @app.route('/change_password', methods=['GET', 'POST'])
    def change_password():
     user = current_user()
     if not user:
        flash("Please log in to change your password.", "warning")
        return redirect(url_for('login_page'))  # or 'login' if that's your endpoint

     if request.method == 'POST':
        current_pass = request.form.get('current_password', '').strip()
        new_pass = request.form.get('new_password', '').strip()
        confirm_pass = request.form.get('confirm_password', '').strip()

        # Basic validation
        if not current_pass or not new_pass or not confirm_pass:
            flash("All password fields are required.", "danger")
            return redirect(url_for('change_password'))

        if not user.check_password(current_pass):
            flash("Current password is incorrect.", "danger")
            return redirect(url_for('change_password'))

        if new_pass != confirm_pass:
            flash("New passwords do not match.", "danger")
            return redirect(url_for('change_password'))

        if len(new_pass) < 6:
            flash("Choose a stronger password (at least 6 characters).", "warning")
            return redirect(url_for('change_password'))

        # Update and persist
        user.set_password(new_pass)
        db.session.commit()

        flash("Password changed successfully.", "success")
        return redirect(url_for('profile'))

     # GET — render the change password page
     return render_template('change_password.html', user=user)
    
    # ------- Marketplace / Sell / Buy -------
    @app.route('/marketplace')
    def marketplace():
        user = current_user()
        if not user:
            flash("Please log in to access the marketplace.", "warning")
            return redirect(url_for('login_page'))
        wallet = ensure_wallet(user)
        try:
            items = DataItem.query.all()
        except Exception:
            items = []
        return render_template('marketplace.html', user=user, wallet=wallet, items=items)

    @app.route('/sell', methods=['GET', 'POST'])
    def sell():
        user = current_user()
        if not user:
            flash("Please log in to list an item.", "warning")
            return redirect(url_for('login_page'))
        if request.method == 'POST':
            title = request.form.get('title', '').strip()
            description = request.form.get('description', '').strip()
            price = request.form.get('price', '').strip()
            if not title or not description or not price:
                flash("All fields are required.", "danger")
                return redirect(url_for('sell'))
            new_item = DataItem(title=title, description=description, price=float(price), seller=user)
            db.session.add(new_item)
            db.session.commit()
            flash("Data item listed for sale!", "success")
            return redirect(url_for('marketplace'))
        return render_template('sell.html', user=user)

    # ------- Buy / Use / Transfer / Transactions -------
    @app.route('/buy_data', methods=['GET', 'POST'])
    def buy_data():
        user = current_user()
        if not user:
            return redirect(url_for('login_page'))
        wallet = ensure_wallet(user)
        if request.method == 'POST':
            raw_amount = request.form.get('amount', '').strip()
            if not raw_amount.isdigit():
                flash("Invalid amount.", "danger")
                return redirect(url_for('buy_data'))
            amount = int(raw_amount)
            if amount <= 0:
                flash("Amount must be greater than 0.", "danger")
                return redirect(url_for('buy_data'))
            wallet.balance_mb += amount
            wallet.total_purchased_mb += amount
            expiry_date = datetime.utcnow() + timedelta(days=30)
            new_entry = DataEntry(user_id=user.id, amount_mb=amount, source='purchased', added_on=datetime.utcnow(), expiry_date=expiry_date)
            db.session.add(new_entry)
            txn = Transaction(sender_id=None, receiver_id=user.id, amount_mb=amount, note=f"Bought {amount} MB")
            db.session.add(txn)
            db.session.commit()
            flash(f"Successfully bought {amount} MB of data!", "success")
            return redirect(url_for('dashboard'))
        return render_template('buy_data.html', user=user, wallet=wallet)

    @app.route('/use_data', methods=['POST'])
    def use_data():
        user = current_user()
        if not user:
            return redirect(url_for('login_page'))
        wallet = ensure_wallet(user)
        try:
            amount = int(request.form.get('amount_mb', 0))
        except (TypeError, ValueError):
            flash("Invalid amount entered.", "danger")
            return redirect(url_for('dashboard'))
        source = request.form.get('source')
        if amount <= 0:
            flash("Please enter a valid amount.", "danger")
            return redirect(url_for('dashboard'))
        try:
            if source == 'daily':
                quota_left = (user.daily_quota_mb or 0) - (user.used_today_mb or 0)
                if amount > quota_left:
                    flash("Not enough daily quota remaining.", "danger")
                    return redirect(url_for('dashboard'))
                user.used_today_mb += amount
                user.total_used_mb = (user.total_used_mb or 0) + amount
                flash(f"Used {amount} MB from daily quota.", "success")
            elif source == 'wallet':
                if wallet.balance_mb < amount:
                    flash("Not enough balance in wallet.", "danger")
                    return redirect(url_for('dashboard'))
                active_entries = get_active_entries(user) or []
                remaining = amount
                for entry in active_entries[:]:
                    if remaining <= 0:
                        break
                    entry_amount = entry.amount_mb or 0
                    if entry_amount <= remaining:
                        remaining -= entry_amount
                        db.session.delete(entry)
                    else:
                        entry.amount_mb -= remaining
                        remaining = 0
                wallet.balance_mb -= amount
                wallet.total_used_mb = (wallet.total_used_mb or 0) + amount
                user.total_used_mb = (user.total_used_mb or 0) + amount
                flash(f"Used {amount} MB from wallet balance.", "success")
            else:
                flash("Invalid data source selected.", "danger")
                return redirect(url_for('dashboard'))
            db.session.commit()
            return redirect(url_for('dashboard'))
        except Exception as exc:
            db.session.rollback()
            app.logger.exception("Error using data")
            flash("An error occurred while consuming data.", "danger")
            return redirect(url_for('dashboard'))

    @app.route('/transfer', methods=['GET', 'POST'])
    def transfer():
        user = current_user()
        if not user:
            return redirect(url_for('login_page'))
        simulate_end_of_day_rollover(user)
        wallet = ensure_wallet(user)
        if request.method == 'POST':
            to_email = request.form.get('to_email', '').strip().lower()
            amount_raw = request.form.get('amount_mb', '').strip()
            if not amount_raw.isdigit():
                flash("Enter a valid amount.", "warning")
                return redirect(url_for('transfer'))
            amount_mb = int(amount_raw)
            if amount_mb <= 0 or wallet.balance_mb < amount_mb:
                flash("Invalid or insufficient amount.", "warning")
                return redirect(url_for('transfer'))
            receiver = User.query.filter_by(email=to_email).first()
            if not receiver:
                flash("Recipient not found.", "warning")
                return redirect(url_for('transfer'))
            wallet.balance_mb -= amount_mb
            recv_wallet = ensure_wallet(receiver)
            recv_wallet.balance_mb += amount_mb
            txn = Transaction(sender_id=user.id, receiver_id=receiver.id, amount_mb=amount_mb, note="Transfer")
            db.session.add(txn)
            db.session.commit()
            flash(f"Transferred {amount_mb} MB to {receiver.email}.", "success")
            return redirect(url_for('dashboard'))
        return render_template('transfer.html', user=user, wallet=wallet)

    @app.route('/transactions')
    def transactions():
        user = current_user()
        if not user:
            return redirect(url_for('login_page'))
        txns = Transaction.query.filter((Transaction.sender_id == user.id) | (Transaction.receiver_id == user.id)).order_by(Transaction.timestamp.desc()).limit(200).all()
        return render_template('transactions.html', user=user, txns=txns)

    # Admin-only endpoints
    @app.route('/admin')
    def admin_panel():
        user = current_user()
        if not user or not user.is_admin:
            flash('Admin access required', 'danger')
            return redirect(url_for('index'))
        users = User.query.all()
        txns = Transaction.query.order_by(Transaction.timestamp.desc()).limit(200).all()
        return render_template('admin.html', user=user, users=users, txns=txns)

    @app.route('/admin/cleanup_expired')
    def admin_cleanup_expired():
        user = current_user()
        if not user or not user.is_admin:
            return jsonify({'error': 'admin required'}), 403
        all_entries = DataEntry.query.filter(DataEntry.expiry_date < datetime.utcnow()).all()
        count = len(all_entries)
        for e in all_entries:
            db.session.delete(e)
        db.session.commit()
        return jsonify({'cleaned': count})

    @app.template_filter('mb_to_gb')
    def mb_to_gb(mb):
        try:
            mb_val = (mb or 0)
            return f"{mb_val/1024:.2f} GB"
        except Exception:
            return "0.00 GB"

    @app.context_processor
    def inject_now():
        return {'datetime': datetime}

    @app.errorhandler(404)
    def not_found(e):
        return render_template('404.html'), 404

    @app.errorhandler(500)
    def server_error(e):
        return render_template('500.html'), 500