import unittest
from datetime import date

import ai_engine
from app import app as default_app, create_app
from models import Expense, Income, SavingGoal, User, db


def tearDownModule():
    with default_app.app_context():
        db.session.remove()
        db.engine.dispose()


class FinanceAdvisorAppTests(unittest.TestCase):
    def setUp(self):
        self.app = create_app({
            "TESTING": True,
            "SECRET_KEY": "test-secret",
            "SQLALCHEMY_DATABASE_URI": "sqlite://",
        })
        self.app_context = self.app.app_context()
        self.app_context.push()
        db.drop_all()
        db.create_all()
        self.client = self.app.test_client()

    def tearDown(self):
        db.session.remove()
        db.drop_all()
        db.engine.dispose()
        self.app_context.pop()

    def register_and_login(self):
        response = self.client.post("/register", data={
            "username": "FinanceUser",
            "email": "User@Example.com",
            "password": "secure-password",
            "role": "individual",
        })
        self.assertEqual(response.status_code, 302)
        return self.client.post("/login", data={
            "identifier": "USER@EXAMPLE.COM",
            "password": "secure-password",
        })

    def test_primary_pages_and_public_stylesheet_render(self):
        self.assertEqual(self.client.get("/login").status_code, 200)
        stylesheet = self.client.get("/css/style.css")
        self.assertEqual(stylesheet.status_code, 200)
        self.assertIn(b".stat-card.wants", stylesheet.data)
        self.register_and_login()
        for path in ("/", "/income", "/expenses", "/budget", "/goals", "/report"):
            with self.subTest(path=path):
                self.assertEqual(self.client.get(path).status_code, 200)

    def test_registration_normalizes_identifiers_and_login_is_case_insensitive(self):
        response = self.register_and_login()
        self.assertEqual(response.status_code, 302)
        self.assertEqual(response.location, "/")
        user = User.query.one()
        self.assertEqual(user.username, "financeuser")
        self.assertEqual(user.email, "user@example.com")

    def test_registration_rejects_invalid_password_role_and_email(self):
        for data in (
            {"username": "short", "email": "a@example.com", "password": "123"},
            {"username": "role", "email": "b@example.com", "password": "secure-password", "role": "admin"},
            {"username": "email", "email": "invalid", "password": "secure-password"},
        ):
            response = self.client.post("/register", data=data)
            self.assertEqual(response.status_code, 302)
        self.assertEqual(User.query.count(), 0)

    def test_duplicate_registration_is_case_insensitive(self):
        self.register_and_login()
        response = self.client.post("/register", data={
            "username": "FINANCEUSER",
            "email": "another@example.com",
            "password": "secure-password",
            "role": "student",
        })
        self.assertEqual(response.status_code, 302)
        self.assertEqual(User.query.count(), 1)

    def test_invalid_money_dates_and_categories_do_not_create_records(self):
        self.register_and_login()
        for amount in ("", "-1", "NaN", "Infinity", "1e9999", "not-a-number"):
            self.client.post("/income", data={
                "source": "Salary", "amount": amount, "date": date.today().isoformat(),
            })
        self.client.post("/income", data={
            "source": "Salary", "amount": "10", "date": "not-a-date",
        })
        self.client.post("/expenses", data={
            "category": "Unknown", "amount": "10", "date": date.today().isoformat(),
        })
        self.client.post("/expenses", data={
            "category": "Food", "amount": "0", "date": date.today().isoformat(),
        })
        self.assertEqual(Income.query.count(), 0)
        self.assertEqual(Expense.query.count(), 0)

    def test_invalid_login_session_id_redirects_to_login(self):
        with self.client.session_transaction() as session:
            session["_user_id"] = "not-an-integer"
            session["_fresh"] = True
        response = self.client.get("/income")
        self.assertEqual(response.status_code, 302)
        self.assertIn("/login", response.location)

    def test_recurring_income_is_included_in_later_months(self):
        self.register_and_login()
        self.client.post("/income", data={
            "source": "Salary",
            "amount": "500",
            "date": date.today().replace(day=1).isoformat(),
            "is_recurring": "on",
        })
        if date.today().month == 12:
            next_month = date(date.today().year + 1, 1, 1)
        else:
            next_month = date(date.today().year, date.today().month + 1, 1)
        response = self.client.get(f"/report?month={next_month:%Y-%m}")
        self.assertEqual(response.status_code, 200)
        self.assertIn("₹500.00", response.get_data(as_text=True))

    def test_invalid_report_month_redirects_with_flash(self):
        self.register_and_login()
        response = self.client.get("/report?month=not-a-month")
        self.assertEqual(response.status_code, 302)
        page = self.client.get(response.location)
        self.assertIn("Select a valid report month.", page.get_data(as_text=True))

    def test_invalid_goal_values_and_contributions_are_rejected(self):
        self.register_and_login()
        self.client.post("/goals", data={
            "name": "Emergency fund", "target_amount": "0",
        })
        self.assertEqual(SavingGoal.query.count(), 0)
        self.client.post("/goals", data={
            "name": "Emergency fund", "target_amount": "1000",
        })
        goal = SavingGoal.query.one()
        self.client.post(f"/goals/{goal.id}/contribute", data={"amount": "-5"})
        self.assertEqual(goal.current_amount, 0)

    def test_expense_api_returns_current_month_category_totals(self):
        self.register_and_login()
        user = User.query.one()
        db.session.add_all([
            Expense(user_id=user.id, category="Food", amount=10, date=date.today()),
            Expense(user_id=user.id, category="Food", amount=15, date=date.today()),
        ])
        db.session.commit()
        response = self.client.get("/api/expenses-by-category")
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.get_json(), {"Food": 25.0})

    def test_model_date_defaults_are_date_values(self):
        user = User(username="defaults", email="defaults@example.com")
        user.set_password("secure-password")
        db.session.add(user)
        db.session.flush()
        income = Income(user_id=user.id, source="Default date", amount=10)
        expense = Expense(user_id=user.id, category="Food", amount=5)
        db.session.add_all([income, expense])
        db.session.commit()
        self.assertEqual(income.date, date.today())
        self.assertEqual(expense.date, date.today())
        self.assertIsNotNone(user.created_at)


class BudgetEngineTests(unittest.TestCase):
    def test_education_is_included_in_needs_allocation(self):
        plan = ai_engine.generate_budget_plan(1000, {"Education": 100})
        self.assertIn("Education", plan["allocations"])
        self.assertEqual(plan["allocations"]["Education"], 500)

    def test_rule_based_insights_return_strings(self):
        insights = ai_engine._rule_based_insights(1000, {"Food": 200}, {})
        self.assertTrue(insights)
        self.assertTrue(all(isinstance(insight, str) for insight in insights))


if __name__ == "__main__":
    unittest.main()
