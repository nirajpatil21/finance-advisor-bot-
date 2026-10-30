from datetime import datetime, date, timedelta
from calendar import month_name
from decimal import Decimal, InvalidOperation
import math
import re

from flask import Flask, render_template, redirect, url_for, request, flash, jsonify
from flask_login import (
    LoginManager, login_user, logout_user, login_required, current_user,
)
from sqlalchemy import func, or_
from sqlalchemy.exc import IntegrityError

from config import Config, IS_VERCEL
from models import db, User, Income, Expense, SavingGoal, EXPENSE_CATEGORIES
import ai_engine


VALID_ROLES = {"individual", "student", "freelancer", "household"}


def _parse_positive_amount(raw_value):
    try:
        value = float(Decimal(raw_value))
    except (InvalidOperation, TypeError, ValueError, OverflowError):
        return None
    if not math.isfinite(value) or value <= 0:
        return None
    return value


def _parse_date(raw_value):
    try:
        return datetime.strptime(raw_value, "%Y-%m-%d").date()
    except (TypeError, ValueError):
        return None


def create_app(test_config=None):
    app = Flask(
        __name__,
        static_folder=None if IS_VERCEL else "public",
        static_url_path="",
    )
    app.config.from_object(Config)
    if test_config:
        app.config.update(test_config)

    db.init_app(app)

    login_manager = LoginManager()
    login_manager.login_view = "login"
    login_manager.init_app(app)

    @login_manager.user_loader
    def load_user(user_id):
        try:
            return db.session.get(User, int(user_id))
        except (TypeError, ValueError):
            return None

    with app.app_context():
        db.create_all()

    # ---------- helpers ----------
    def current_month_key(d=None):
        d = d or date.today()
        return d.strftime("%Y-%m")

    def month_bounds(month_key):
        month_start = datetime.strptime(month_key, "%Y-%m").date().replace(day=1)
        if month_start.month == 12:
            next_month = date(month_start.year + 1, 1, 1)
        else:
            next_month = date(month_start.year, month_start.month + 1, 1)
        return month_start, next_month

    def month_expenses_by_category(user_id, month_key):
        month_start, next_month = month_bounds(month_key)
        rows = (
            db.session.query(Expense.category, func.sum(Expense.amount))
            .filter(
                Expense.user_id == user_id,
                Expense.date >= month_start,
                Expense.date < next_month,
            )
            .group_by(Expense.category)
            .all()
        )
        return {category: float(amount) for category, amount in rows}

    def month_income_total(user_id, month_key):
        month_start, next_month = month_bounds(month_key)
        total = (
            db.session.query(func.sum(Income.amount))
            .filter(
                Income.user_id == user_id,
                Income.date < next_month,
                or_(Income.date >= month_start, Income.is_recurring.is_(True)),
            )
            .scalar()
        )
        return float(total or 0)

    # ---------- auth ----------
    @app.route("/register", methods=["GET", "POST"])
    def register():
        if request.method == "POST":
            username = request.form.get("username", "").strip().lower()
            email = request.form.get("email", "").strip().lower()
            password = request.form.get("password", "")
            role = request.form.get("role", "individual")

            if not username or len(username) > 80:
                flash("Enter a username of up to 80 characters.", "danger")
                return redirect(url_for("register"))
            if (
                len(email) > 120
                or not re.fullmatch(r"[^@\s]+@[^@\s]+\.[^@\s]+", email)
            ):
                flash("Enter a valid email address.", "danger")
                return redirect(url_for("register"))
            if len(password) < 6:
                flash("Password must be at least 6 characters long.", "danger")
                return redirect(url_for("register"))
            if role not in VALID_ROLES:
                flash("Select a valid account type.", "danger")
                return redirect(url_for("register"))

            if User.query.filter(
                (func.lower(User.username) == username)
                | (func.lower(User.email) == email)
            ).first():
                flash("Username or email already exists.", "danger")
                return redirect(url_for("register"))

            user = User(username=username, email=email, role=role)
            user.set_password(password)
            db.session.add(user)
            try:
                db.session.commit()
            except IntegrityError:
                db.session.rollback()
                flash("Username or email already exists.", "danger")
                return redirect(url_for("register"))
            flash("Account created. Please log in.", "success")
            return redirect(url_for("login"))
        return render_template("register.html")

    @app.route("/login", methods=["GET", "POST"])
    def login():
        if request.method == "POST":
            identifier = request.form.get("identifier", "").strip()
            password = request.form.get("password", "")
            user = User.query.filter(
                (func.lower(User.username) == identifier.lower())
                | (func.lower(User.email) == identifier.lower())
            ).first()
            if user and user.check_password(password):
                login_user(user)
                return redirect(url_for("dashboard"))
            flash("Invalid credentials.", "danger")
        return render_template("login.html")

    @app.route("/logout")
    @login_required
    def logout():
        logout_user()
        return redirect(url_for("login"))

    # ---------- dashboard ----------
    @app.route("/")
    @login_required
    def dashboard():
        month_key = current_month_key()
        income_total = month_income_total(current_user.id, month_key)
        expenses = month_expenses_by_category(current_user.id, month_key)
        total_spent = sum(expenses.values())
        savings = income_total - total_spent

        prev_month = (date.today().replace(day=1) - timedelta(days=1)).strftime("%Y-%m")
        prev_expenses = month_expenses_by_category(current_user.id, prev_month)

        insights = ai_engine.generate_spending_insights(income_total, expenses, prev_expenses)
        goals = SavingGoal.query.filter_by(user_id=current_user.id).all()

        return render_template(
            "dashboard.html",
            month_label=month_name[date.today().month] + " " + str(date.today().year),
            income_total=income_total,
            total_spent=total_spent,
            savings=savings,
            expenses=expenses,
            insights=insights,
            goals=goals,
        )

    # ---------- income ----------
    @app.route("/income", methods=["GET", "POST"])
    @login_required
    def income():
        if request.method == "POST":
            source = request.form.get("source", "").strip()
            amount = _parse_positive_amount(request.form.get("amount"))
            income_date = _parse_date(request.form.get("date"))
            if not source or len(source) > 100:
                flash("Enter an income source of up to 100 characters.", "danger")
                return redirect(url_for("income"))
            if amount is None:
                flash("Enter an amount greater than zero.", "danger")
                return redirect(url_for("income"))
            if income_date is None:
                flash("Enter a valid income date.", "danger")
                return redirect(url_for("income"))
            is_recurring = bool(request.form.get("is_recurring"))
            db.session.add(Income(
                user_id=current_user.id, source=source, amount=amount,
                date=income_date, is_recurring=is_recurring,
                notes=request.form.get("notes", "").strip()[:255],
            ))
            db.session.commit()
            flash("Income logged.", "success")
            return redirect(url_for("income"))

        records = Income.query.filter_by(user_id=current_user.id).order_by(Income.date.desc()).all()
        return render_template("income.html", records=records, today=date.today().isoformat())

    @app.route("/income/<int:income_id>/delete", methods=["POST"])
    @login_required
    def delete_income(income_id):
        rec = Income.query.filter_by(id=income_id, user_id=current_user.id).first_or_404()
        db.session.delete(rec)
        db.session.commit()
        return redirect(url_for("income"))

    # ---------- expenses ----------
    @app.route("/expenses", methods=["GET", "POST"])
    @login_required
    def expenses():
        if request.method == "POST":
            category = request.form.get("category", "")
            amount = _parse_positive_amount(request.form.get("amount"))
            expense_date = _parse_date(request.form.get("date"))
            if category not in EXPENSE_CATEGORIES:
                flash("Select a valid expense category.", "danger")
                return redirect(url_for("expenses"))
            if amount is None:
                flash("Enter an amount greater than zero.", "danger")
                return redirect(url_for("expenses"))
            if expense_date is None:
                flash("Enter a valid expense date.", "danger")
                return redirect(url_for("expenses"))
            db.session.add(Expense(
                user_id=current_user.id, category=category, amount=amount,
                date=expense_date,
                description=request.form.get("description", "").strip()[:255],
            ))
            db.session.commit()
            flash("Expense logged.", "success")
            return redirect(url_for("expenses"))

        records = Expense.query.filter_by(user_id=current_user.id).order_by(Expense.date.desc()).all()
        return render_template(
            "expenses.html", records=records, categories=EXPENSE_CATEGORIES,
            today=date.today().isoformat(),
        )

    @app.route("/expenses/<int:expense_id>/delete", methods=["POST"])
    @login_required
    def delete_expense(expense_id):
        rec = Expense.query.filter_by(id=expense_id, user_id=current_user.id).first_or_404()
        db.session.delete(rec)
        db.session.commit()
        return redirect(url_for("expenses"))

    # ---------- budget ----------
    @app.route("/budget")
    @login_required
    def budget():
        month_key = current_month_key()
        income_total = month_income_total(current_user.id, month_key)
        expenses = month_expenses_by_category(current_user.id, month_key)
        plan = ai_engine.generate_budget_plan(income_total, expenses)
        return render_template(
            "budget.html", plan=plan, expenses=expenses, income_total=income_total,
        )

    # ---------- saving goals ----------
    @app.route("/goals", methods=["GET", "POST"])
    @login_required
    def goals():
        if request.method == "POST":
            name = request.form.get("name", "").strip()
            target = _parse_positive_amount(request.form.get("target_amount"))
            deadline_raw = request.form.get("deadline", "").strip()
            deadline = _parse_date(deadline_raw) if deadline_raw else None
            if not name or len(name) > 100:
                flash("Enter a goal name of up to 100 characters.", "danger")
                return redirect(url_for("goals"))
            if target is None:
                flash("Enter a target amount greater than zero.", "danger")
                return redirect(url_for("goals"))
            if deadline_raw and deadline is None:
                flash("Enter a valid goal deadline.", "danger")
                return redirect(url_for("goals"))
            db.session.add(SavingGoal(
                user_id=current_user.id, name=name, target_amount=target, deadline=deadline,
            ))
            db.session.commit()
            flash("Saving goal created.", "success")
            return redirect(url_for("goals"))

        records = SavingGoal.query.filter_by(user_id=current_user.id).all()
        return render_template("goals.html", records=records)

    @app.route("/goals/<int:goal_id>/contribute", methods=["POST"])
    @login_required
    def contribute_goal(goal_id):
        goal = SavingGoal.query.filter_by(id=goal_id, user_id=current_user.id).first_or_404()
        amount = _parse_positive_amount(request.form.get("amount"))
        if amount is None:
            flash("Enter a contribution greater than zero.", "danger")
            return redirect(url_for("goals"))
        goal.current_amount = (goal.current_amount or 0) + amount
        db.session.commit()
        return redirect(url_for("goals"))

    # ---------- monthly report ----------
    @app.route("/report")
    @login_required
    def report():
        month_key = request.args.get("month", current_month_key())
        try:
            parsed_month = datetime.strptime(month_key, "%Y-%m")
        except (TypeError, ValueError):
            flash("Select a valid report month.", "danger")
            return redirect(url_for("report"))
        if parsed_month.strftime("%Y-%m") != month_key:
            flash("Select a valid report month.", "danger")
            return redirect(url_for("report"))
        income_total = month_income_total(current_user.id, month_key)
        expenses = month_expenses_by_category(current_user.id, month_key)
        total_spent = sum(expenses.values())
        savings = income_total - total_spent
        insights = ai_engine.generate_spending_insights(income_total, expenses)

        return render_template(
            "report.html", month_key=month_key, income_total=income_total,
            expenses=expenses, total_spent=total_spent, savings=savings, insights=insights,
        )

    # ---------- simple JSON API (for the chart in dashboard) ----------
    @app.route("/api/expenses-by-category")
    @login_required
    def api_expenses_by_category():
        month_key = current_month_key()
        return jsonify(month_expenses_by_category(current_user.id, month_key))

    return app


app = create_app()

if __name__ == "__main__":
    app.run(debug=True, port=5000)
