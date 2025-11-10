from app import app,db, User, ensure_wallet

def init():
    with app.app_context():
     db.create_all()
     exit()

    # create admin
    admin_email = 'admin@bytebank.local'
    if not User.query.filter_by(email=admin_email).first():
        admin = User(name='Admin', email=admin_email, is_admin=True)
        admin.set_password('adminpass')
        admin.daily_quota_mb = 2048
        db.session.add(admin)
        db.session.commit()
        ensure_wallet(admin)
        print("Admin created:", admin_email, "password: adminpass")

    # create demo user
    user_email = 'demo@bytebank.local'
    if not User.query.filter_by(email=user_email).first():
        demo = User(name='Demo User', email=user_email)
        demo.set_password('demopass')
        demo.daily_quota_mb = 1024
        db.session.add(demo)
        db.session.commit()
        w = ensure_wallet(demo)
        w.balance_mb = 500  # give some initial wallet MB for demo
        db.session.commit()
        print("Demo user created:", user_email, "password: demopass")

if __name__ == '__main__':
    init()
    print("Database initialized successfully")