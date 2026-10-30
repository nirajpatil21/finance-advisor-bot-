from datetime import date, datetime, timezone
from flask_sqlalchemy import SQLAlchemy
from flask_login import UserMixin
from werkzeug.security import generate_password_hash, check_password_hash

db = SQLAlchemy()

EXPENSE_CATEGORIES = [
    "Rent", "Food", "Transport", "Entertainment", "Utilities",
    "Education", "Healthcare", "Groceries", "Shopping", "Other",
]


def utc_now():
    return datetime.now(timezone.utc).replace(tzinfo=None)


class User(UserMixin, db.Model):
    id = db.Column(db.Integer, primary_key=True)
    username = db.Column(db.String(80), unique=True, nullable=False)
    email = db.Column(db.String(120), unique=True, nullable=False)
    password_hash = db.Column(db.String(255), nullable=False)
    role = db.Column(db.String(20), default="individual")  # individual, student, freelancer, household
    created_at = db.Column(db.DateTime, default=utc_now)

    incomes = db.relationship("Income", backref="user", cascade="all, delete-orphan")
    expenses = db.relationship("Expense", backref="user", cascade="all, delete-orphan")
    budgets = db.relationship("Budget", backref="user", cascade="all, delete-orphan")
    goals = db.relationship("SavingGoal", backref="user", cascade="all, delete-orphan")

    def set_password(self, password):
        self.password_hash = generate_password_hash(password)

    def check_password(self, password):
        return check_password_hash(self.password_hash, password)


class Income(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    user_id = db.Column(db.Integer, db.ForeignKey("user.id"), nullable=False)
    source = db.Column(db.String(100), nullable=False)
    amount = db.Column(db.Float, nullable=False)
    date = db.Column(db.Date, default=date.today, nullable=False)
    is_recurring = db.Column(db.Boolean, default=False)
    notes = db.Column(db.String(255))

    @property
    def month_key(self):
        return self.date.strftime("%Y-%m")


class Expense(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    user_id = db.Column(db.Integer, db.ForeignKey("user.id"), nullable=False)
    category = db.Column(db.String(50), nullable=False)
    amount = db.Column(db.Float, nullable=False)
    date = db.Column(db.Date, default=date.today, nullable=False)
    description = db.Column(db.String(255))

    @property
    def month_key(self):
        return self.date.strftime("%Y-%m")


class Budget(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    user_id = db.Column(db.Integer, db.ForeignKey("user.id"), nullable=False)
    month = db.Column(db.String(7), nullable=False)  # "YYYY-MM"
    category = db.Column(db.String(50), nullable=False)
    limit_amount = db.Column(db.Float, nullable=False)

    __table_args__ = (db.UniqueConstraint("user_id", "month", "category"),)


class SavingGoal(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    user_id = db.Column(db.Integer, db.ForeignKey("user.id"), nullable=False)
    name = db.Column(db.String(100), nullable=False)
    target_amount = db.Column(db.Float, nullable=False)
    current_amount = db.Column(db.Float, default=0.0)
    deadline = db.Column(db.Date)
    created_at = db.Column(db.DateTime, default=utc_now)

    @property
    def progress_pct(self):
        if self.target_amount <= 0:
            return 0
        return min(100, round((self.current_amount / self.target_amount) * 100, 1))
