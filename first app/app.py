from flask import Flask, render_template, request, redirect, url_for, session, flash, jsonify
from flask_sqlalchemy import SQLAlchemy
from flask_migrate import Migrate
from werkzeug.security import generate_password_hash, check_password_hash
from datetime import datetime, date
from sqlalchemy.orm import joinedload

app = Flask(__name__)
app.config['SQLALCHEMY_DATABASE_URI'] = 'sqlite:///re-bytebank.db'
app.config['SQLALCHEMY_TRACK_MODIFICATIONS'] = False
app.config['SECRET_KEY'] = 'replace-this-with-a-secure-random-key'
db = SQLAlchemy(app)
migrate = Migrate(app,db)

# Models
class User(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    name = db.Column(db.String(120), nullable=False)
    email = db.Column(db.String(150), unique=True, nullable=False)
    password_hash = db.Column(db.String(200), nullable=False)
    daily_quota_mb = db.Column(db.Integer, default=1024)  # default 1GB/day in MB
    used_today_mb = db.Column(db.Integer, default=0)
    last_usage_date = db.Column(db.Date, nullable=True)
    is_admin = db.Column(db.Boolean, default=False)
    created_at = db.Column(db.DateTime, default=datetime.utcnow)
    last_login = db.Column(db.DateTime)
    

    wallet = db.relationship('DataWallet', uselist=False, back_populates='user')
    sent_transactions = db.relationship('Transaction', back_populates='sender', foreign_keys='Transaction.sender_id')
    received_transactions = db.relationship('Transaction', back_populates='receiver', foreign_keys='Transaction.receiver_id')

    def set_password(self, raw):
        self.password_hash = generate_password_hash(raw)

    def check_password(self, raw):
        return check_password_hash(self.password_hash, raw)

class DataItem(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    title = db.Column(db.String(100), nullable=False)
    description = db.Column(db.Text, nullable=False)
    price = db.Column(db.Float, nullable=False)
    seller_id = db.Column(db.Integer, db.ForeignKey('user.id'), nullable=False)
    seller = db.relationship('User', backref='data_items')

class DataWallet(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    user_id = db.Column(db.Integer, db.ForeignKey('user.id'), unique=True, nullable=False)
    balance_mb = db.Column(db.Integer, default=0)  # stored in MB
    total_purchased_mb = db.Column(db.Integer,default=0)
    created_at = db.Column(db.DateTime, default=datetime.utcnow)

    user = db.relationship('User', back_populates='wallet')

class Transaction(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    sender_id = db.Column(db.Integer, db.ForeignKey('user.id'), nullable=True)
    receiver_id = db.Column(db.Integer, db.ForeignKey('user.id'), nullable=True)
    amount_mb = db.Column(db.Integer, nullable=False)
    timestamp = db.Column(db.DateTime, default=datetime.utcnow)
    note = db.Column(db.String(250), nullable=True)

    sender = db.relationship('User', back_populates='sent_transactions', foreign_keys=[sender_id])
    receiver = db.relationship('User', back_populates='received_transactions', foreign_keys=[receiver_id])

# Helpers
def current_user():
    """Return the logged-in User object or None."""
    user_id = session.get('user_id')
    if not user_id:
        return None
    return User.query.get(user_id)

def ensure_wallet(user):
    """Ensure a DataWallet exists for a user. Returns the wallet."""
    if not user.wallet:
        wallet = DataWallet(user_id=user.id, balance_mb=0)
        db.session.add(wallet)
        db.session.commit()
        return wallet
    return user.wallet


def simulate_end_of_day_rollover(user):
    """If the last_usage_date is not today, roll leftover quota into wallet."""
    if not user:
        return
    today = date.today()
    # If last_usage_date is None or not today, roll leftover
    if user.last_usage_date == today:
        return
    leftover = max(0, (user.daily_quota_mb or 0) - (user.used_today_mb or 0))
    if leftover > 0:
        wallet = ensure_wallet(user)
        wallet.balance_mb += leftover
        # record rollover as a system -> user transaction (sender_id None)
        txn = Transaction(sender_id=None, receiver_id=user.id, amount_mb=leftover, note='Rollover')
        db.session.add(txn)
    user.used_today_mb = 0
    user.last_usage_date = today
    db.session.commit()

def get_all_the_things():
    """Return data for the index page. Avoid returning None to the template."""
    # Example content — customize as needed
    return {
        "title": "Welcome to ByteBank",
        "items": [
            {"title": "Save unused data", "desc": "Rollover leftover daily quota to your wallet."},
            {"title": "Trade data", "desc": "Transfer or trade your extra data with others."},
            {"title": "Use wallet", "desc": "Use wallet balance when you exceed daily quota."}
        ]
    }

# Routes
@app.route('/')
def index():
    # call helper correctly (was using current_user without parentheses)
    user = current_user()
    main_data = get_all_the_things()
    return render_template('index.html', user=user, main=main_data)

@app.route('/register', methods=['GET', 'POST'])
def register():
    if request.method == 'POST':
        name = request.form['name'].strip()
        email = request.form['email'].strip().lower()
        password = request.form['password']

        if User.query.filter_by(email=email).first():
            flash('Email already registered', 'warning')
            return redirect(url_for('register'))

        user = User(name=name, email=email)
        user.set_password(password)
        db.session.add(user)
        db.session.commit()
        # create wallet
        ensure_wallet(user)
        flash('Registration successful. Please log in.', 'success')
        return redirect(url_for('login'))

    return render_template('register.html')


@app.route('/login', methods=['GET', 'POST'])
def login():
    if request.method == 'POST':
        email = request.form['email'].strip().lower()
        password = request.form['password']

        user = User.query.filter_by(email=email).first()
        if user and user.check_password(password):
            # Set session info
            session['user_id'] = user.id
            session['user_name'] = user.name

            # Update last login time
            user.last_login = datetime.utcnow()

            # Ensure wallet exists for the user
            ensure_wallet(user)

            # Save changes to database
            db.session.commit()

            flash('Logged in successfully', 'success')
            return redirect(url_for('dashboard'))

        # If login fails
        flash('Invalid email or password', 'danger')
        return redirect(url_for('login'))

    return render_template('login.html')
@app.route('/logout')
def logout():
    session.pop('user_id', None)
    flash('Logged out', 'info')
    return redirect(url_for('index'))

@app.route('/dashboard')
def dashboard():
    user = current_user()
    if not user:
        return redirect(url_for('login'))
    simulate_end_of_day_rollover(user)
    wallet = ensure_wallet(user)
    return render_template('dashboard.html', user=user, wallet=wallet)


@app.route('/marketplace')
def marketplace():
    # 1. Check if user is logged in
    user_id = session.get('user_id')
    if not user_id:
        flash("Please log in to access the marketplace.")
        return redirect(url_for('login'))

    # 2. Fetch the user from the database
    user = User.query.get(user_id)
    if not user:
        flash("User not found. Please log in again.")
        session.pop('user_id', None)  # remove invalid session
        return redirect(url_for('login'))

    # 3. Fetch all items safely
    try:
        items = DataItem.query.all()  # returns empty list if no items
    except Exception as e:
        print(f"Error fetching items: {e}")
        items = []

    # 4. Render the marketplace template
    return render_template('marketplace.html', user=user, items=items)

@app.route('/buy_data', methods=['GET', 'POST'])
def buy_data():
    user = current_user()
    if not user:
        return redirect(url_for('login'))

    wallet = ensure_wallet(user)

    if request.method == 'POST':
        raw_amount = request.form.get('amount', '').strip()
        if not raw_amount.isdigit():
            flash("Invalid amount.", "danger")
            return redirect(url_for('dashboard'))

        amount = int(raw_amount)
        if amount <= 0:
            flash("Amount must be greater than 0.", "danger")
            return redirect(url_for('dashboard'))

        # Update wallet balance and total purchased
        wallet.balance_mb += amount
        wallet.total_purchased_mb += amount

        # Add transaction record
        txn = Transaction(
            sender_id=None,
            receiver_id=user.id,
            amount_mb=amount,
            note=f"Bought {amount} MB of data"
        )
        db.session.add(txn)
        db.session.commit()

        flash(f"Successfully bought {amount} MB of data!", "success")
        return redirect(url_for('dashboard'))

    return render_template('buy_data.html', user=user, wallet=wallet)
@app.route('/profile')
def profile():
    user = current_user()

    if not user:
        flash("Please login to view your profile.", "error")
        return redirect(url_for('login'))

    # Get recent transactions with sender and receiver details loaded
    transactions = (
        Transaction.query.options(
            joinedload(Transaction.sender),
            joinedload(Transaction.receiver)
        )
        .filter((Transaction.sender_id == user.id) | (Transaction.receiver_id == user.id))
        .order_by(Transaction.timestamp.desc())
        .limit(10)
        .all()
    )

    return render_template('profile.html', user=user, transactions=transactions)

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


@app.route('/change_password', methods=['GET', 'POST'])
def change_password():
    user = current_user()
    if not user:
        flash("Please login to change your password.", "error")
        return redirect(url_for('login'))

    if request.method == 'POST':
        current_pass = request.form.get('current_password')
        new_pass = request.form.get('new_password')
        confirm_pass = request.form.get('confirm_password')

        if not check_password_hash(user.password, current_pass):
            flash("Current password is incorrect.", "error")
        elif new_pass != confirm_pass:
            flash("New passwords do not match.", "error")
        else:
            user.password = generate_password_hash(new_pass)
            db.session.commit()
            flash("Password changed successfully.", "success")
            return redirect(url_for('profile'))

    return render_template('change_password.html')

@app.route('/sell', methods=['GET', 'POST'])
def sell():
    if 'user_id' not in session:
        return redirect(url_for('login'))

    user = User.query.get(session['user_id'])

    if request.method == 'POST':
        title = request.form['title']
        description = request.form['description']
        price = float(request.form['price'])

        new_item = DataItem(title=title, description=description, price=price, seller=user)
        db.session.add(new_item)
        db.session.commit()
        flash('Data item listed for sale!', 'success')
        return redirect(url_for('marketplace'))

    return render_template('sell.html', user=user)


@app.route('/buy/<int:item_id>')
def buy(item_id):
    if 'user_id' not in session:
        return redirect(url_for('login'))

    buyer = User.query.get(session['user_id'])
    item = DataItem.query.get_or_404(item_id)
    seller = item.seller

    if buyer.id == seller.id:
        flash("You can't buy your own data item!", 'warning')
        return redirect(url_for('marketplace'))

    if buyer.balance < item.price:
        flash('Insufficient balance to buy this item.', 'danger')
        return redirect(url_for('marketplace'))

    # Transfer money
    buyer.balance -= item.price
    seller.balance += item.price

    # Record transaction (optional)
    transaction = Transaction(
        sender_id=buyer.id,
        receiver_id=seller.id,
        amount=item.price,
        description=f'Purchased data: {item.title}'
    )
    db.session.add(transaction)

    db.session.commit()
    flash(f'You bought "{item.title}" for ₹{item.price}!', 'success')
    return redirect(url_for('marketplace'))

@app.route('/use_data', methods=['POST'])
def use_data():
    """Consume data: prefer daily quota then wallet if needed."""
    user = current_user()
    if not user:
        return redirect(url_for('login'))
    simulate_end_of_day_rollover(user)
    try:
        amount_mb = int(request.form.get('amount_mb', 0))
    except (TypeError, ValueError):
        flash('Enter a valid amount', 'warning')
        return redirect(url_for('dashboard'))

    if amount_mb <= 0:
        flash('Enter a valid amount', 'warning')
        return redirect(url_for('dashboard'))

    remaining_quota = max(0, (user.daily_quota_mb or 0) - (user.used_today_mb or 0))
    wallet = ensure_wallet(user)

    used_from_quota = min(remaining_quota, amount_mb)
    remainder = amount_mb - used_from_quota

    if remainder > wallet.balance_mb:
        flash('Insufficient total balance (quota + wallet)', 'danger')
        return redirect(url_for('dashboard'))

    user.used_today_mb = (user.used_today_mb or 0) + used_from_quota
    if remainder > 0:
        wallet.balance_mb -= remainder
        txn = Transaction(sender_id=user.id, receiver_id=None, amount_mb=remainder, note='Wallet usage')
        db.session.add(txn)
    db.session.commit()
    flash(f'Consumed {amount_mb} MB (quota: {used_from_quota} MB, wallet: {remainder} MB)', 'success')
    return redirect(url_for('dashboard'))

@app.route('/transfer', methods=['GET', 'POST'])
def transfer():
    user = current_user()
    if not user:
        return redirect(url_for('login'))
    simulate_end_of_day_rollover(user)
    wallet = ensure_wallet(user)
    if request.method == 'POST':
        to_email = request.form['to_email'].strip().lower()
        try:
            amount_mb = int(request.form['amount_mb'])
        except (TypeError, ValueError):
            flash('Enter a valid amount', 'warning')
            return redirect(url_for('transfer'))

        if amount_mb <= 0:
            flash('Enter a valid amount', 'warning')
            return redirect(url_for('transfer'))

        if wallet.balance_mb < amount_mb:
            flash('Insufficient wallet balance', 'danger')
            return redirect(url_for('transfer'))

        receiver = User.query.filter_by(email=to_email).first()
        if not receiver:
            flash('Recipient not found', 'warning')
            return redirect(url_for('transfer'))

        # perform transfer
        wallet.balance_mb -= amount_mb
        recv_wallet = ensure_wallet(receiver)
        recv_wallet.balance_mb += amount_mb

        txn = Transaction(sender_id=user.id, receiver_id=receiver.id, amount_mb=amount_mb, note='Transfer')
        db.session.add(txn)
        db.session.commit()
        flash(f'Transferred {amount_mb} MB to {receiver.email}', 'success')
        return redirect(url_for('dashboard'))

    return render_template('transfer.html', user=user, wallet=wallet)

@app.route('/transactions')
def transactions():
    user = current_user()
    if not user:
        return redirect(url_for('login'))
    txns = Transaction.query.filter(
        (Transaction.sender_id == user.id) | (Transaction.receiver_id == user.id)
    ).order_by(Transaction.timestamp.desc()).limit(200).all()
    return render_template('transactions.html', user=user, txns=txns)

@app.route('/admin')
def admin_panel():
    user = current_user()
    if not user or not user.is_admin:
        flash('Admin access required', 'danger')
        return redirect(url_for('index'))
    users = User.query.all()
    txns = Transaction.query.order_by(Transaction.timestamp.desc()).limit(200).all()
    return render_template('admin.html', user=user, users=users, txns=txns)

@app.route('/admin/simulate_rollover_all')
def admin_simulate_rollover_all():
    user = current_user()
    if not user or not user.is_admin:
        return jsonify({'error': 'admin required'}), 403
    all_users = User.query.all()
    for u in all_users:
        simulate_end_of_day_rollover(u)
    return jsonify({'status':'ok', 'rolled_over_users': len(all_users)})

# Utilities
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

if __name__== '__main__':
    with app.app_context():
        db.create_all()  # This creates all missing tables
    app.run(debug=True)