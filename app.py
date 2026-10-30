from datetime import datetime, date
from calendar import month_name
from collections import defaultdict

from flask import Flask, render_template, redirect, url_for, request, flash, jsonify
from flask_login import (
    LoginManager, login_user, logout_user, login_required, current_user,
)

from config import Config, IS_VERCEL
from models import db, User, Income, Expense, Budget, SavingGoal, EXPENSE_CATEGORIES
import ai_engine


def create_app():
    app = Flask(
        __name__,
        static_folder=None if IS_VERCEL else "public",
        static_url_path="",
    )
    app.config.from_object(Config)

    db.init_app(app)

    login_manager = LoginManager()
    login_manager.login_view = "login"
    login_manager.init_app(app)

    @login_manager.user_loader
    def load_user(user_id):
        return User.query.get(int(user_id))

    with app.app_context():
        db.create_all()

    # ---------- helpers ----------
    def current_month_key(d=None):
        d = d or date.today()
        return d.strftime("%Y-%m")

    def month_expenses_by_category(user_id, month_key):
        rows = Expense.query.filter_by(user_id=user_id).all()
        data = defaultdict(float)
        for r in rows:
            if r.month_key == month_key:
                data[r.category] += r.amount
        return dict(data)

    def month_income_total(user_id, month_key):
        rows = Income.query.filter_by(user_id=user_id).all()
        return sum(r.amount for r in rows if r.month_key == month_key)

    # ---------- auth ----------
    @app.route("/register", methods=["GET", "POST"])
    def register():
        if request.method == "POST":
            username = request.form["username"].strip()
            email = request.form["email"].strip()
            password = request.form["password"]
            role = request.form.get("role", "individual")

            if User.query.filter((User.username == username) | (User.email == email)).first():
                flash("Username or email already exists.", "danger")
                return redirect(url_for("register"))

            user = User(username=username, email=email, role=role)
            user.set_password(password)
            db.session.add(user)
            db.session.commit()
            flash("Account created. Please log in.", "success")
            return redirect(url_for("login"))
        return render_template("register.html")

    @app.route("/login", methods=["GET", "POST"])
    def login():
        if request.method == "POST":
            identifier = request.form["identifier"].strip()
            password = request.form["password"]
            user = User.query.filter(
                (User.username == identifier) | (User.email == identifier)
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

        prev_month = (date.today().replace(day=1) - __import__("datetime").timedelta(days=1)).strftime("%Y-%m")
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
            source = request.form["source"].strip()
            amount = float(request.form["amount"])
            income_date = datetime.strptime(request.form["date"], "%Y-%m-%d").date()
            is_recurring = bool(request.form.get("is_recurring"))
            db.session.add(Income(
                user_id=current_user.id, source=source, amount=amount,
                date=income_date, is_recurring=is_recurring,
                notes=request.form.get("notes", ""),
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
            category = request.form["category"]
            amount = float(request.form["amount"])
            expense_date = datetime.strptime(request.form["date"], "%Y-%m-%d").date()
            db.session.add(Expense(
                user_id=current_user.id, category=category, amount=amount,
                date=expense_date, description=request.form.get("description", ""),
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
            name = request.form["name"].strip()
            target = float(request.form["target_amount"])
            deadline_raw = request.form.get("deadline")
            deadline = datetime.strptime(deadline_raw, "%Y-%m-%d").date() if deadline_raw else None
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
        amount = float(request.form["amount"])
        goal.current_amount = (goal.current_amount or 0) + amount
        db.session.commit()
        return redirect(url_for("goals"))

    # ---------- monthly report ----------
    @app.route("/report")
    @login_required
    def report():
        month_key = request.args.get("month", current_month_key())
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
