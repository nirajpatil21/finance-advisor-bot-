"""
AI engine for the Personal Finance Advisor Bot.

Uses Google Gemini (via GEMINI_API_KEY) for natural-language budget
recommendations and spending insights when a key is configured. When no
key is present, falls back to a deterministic rule-based engine (a 50/30/20
style allocation plus simple overspending detection) so the app is fully
functional out of the box.
"""
import os
import json

GEMINI_API_KEY = os.environ.get("GEMINI_API_KEY", "")

NEEDS_CATEGORIES = {"Rent", "Groceries", "Utilities", "Transport", "Healthcare"}
WANTS_CATEGORIES = {"Food", "Entertainment", "Shopping", "Other"}
SAVINGS_TARGET_PCT = 0.20
NEEDS_TARGET_PCT = 0.50
WANTS_TARGET_PCT = 0.30


def _gemini_available():
    if not GEMINI_API_KEY:
        return False
    try:
        import google.generativeai as genai  # noqa: F401
        return True
    except ImportError:
        return False


def _call_gemini(prompt):
    import google.generativeai as genai
    genai.configure(api_key=GEMINI_API_KEY)
    model = genai.GenerativeModel("gemini-1.5-flash")
    response = model.generate_content(prompt)
    return response.text


def generate_budget_plan(monthly_income, expenses_by_category):
    """
    Returns a dict:
    {
        "allocations": {category: recommended_limit, ...},
        "total_needs": float, "total_wants": float, "total_savings": float,
        "narrative": str
    }
    """
    if monthly_income <= 0:
        return {
            "allocations": {},
            "total_needs": 0, "total_wants": 0, "total_savings": 0,
            "narrative": "Log your monthly income first so a budget plan can be generated.",
        }

    needs_pool = monthly_income * NEEDS_TARGET_PCT
    wants_pool = monthly_income * WANTS_TARGET_PCT
    savings_pool = monthly_income * SAVINGS_TARGET_PCT

    needs_cats = [c for c in expenses_by_category if c in NEEDS_CATEGORIES] or list(NEEDS_CATEGORIES)
    wants_cats = [c for c in expenses_by_category if c in WANTS_CATEGORIES] or list(WANTS_CATEGORIES)

    allocations = {}
    if needs_cats:
        per_needs = needs_pool / len(needs_cats)
        for c in needs_cats:
            allocations[c] = round(per_needs, 2)
    if wants_cats:
        per_wants = wants_pool / len(wants_cats)
        for c in wants_cats:
            allocations[c] = round(per_wants, 2)

    result = {
        "allocations": allocations,
        "total_needs": round(needs_pool, 2),
        "total_wants": round(wants_pool, 2),
        "total_savings": round(savings_pool, 2),
    }

    if _gemini_available():
        try:
            prompt = (
                "You are a concise personal finance advisor. A user has a monthly "
                f"income of {monthly_income}. Their current spending by category is: "
                f"{json.dumps(expenses_by_category)}. Recommend a short (4-6 sentence) "
                "budget narrative following a 50% needs / 30% wants / 20% savings "
                "guideline, calling out any category that looks like overspending. "
                "Be specific and actionable, no markdown headers."
            )
            result["narrative"] = _call_gemini(prompt).strip()
            return result
        except Exception:
            pass  # fall through to rule-based narrative

    result["narrative"] = _rule_based_narrative(monthly_income, expenses_by_category, result)
    return result


def _rule_based_narrative(monthly_income, expenses_by_category, plan):
    total_spent = sum(expenses_by_category.values())
    lines = []
    lines.append(
        f"Based on a monthly income of {monthly_income:,.2f}, aim for roughly "
        f"{plan['total_needs']:,.2f} on needs, {plan['total_wants']:,.2f} on wants, "
        f"and {plan['total_savings']:,.2f} toward savings (the 50/30/20 rule)."
    )
    overspent = []
    for cat, amt in expenses_by_category.items():
        limit = plan["allocations"].get(cat)
        if limit and amt > limit:
            overspent.append((cat, amt, limit))
    if overspent:
        parts = [f"{c} ({a:,.0f} vs a recommended {l:,.0f})" for c, a, l in overspent]
        lines.append("You're currently over budget in: " + ", ".join(parts) + ".")
    else:
        lines.append("Your current spending fits within the recommended limits — nice work.")
    if total_spent < monthly_income:
        surplus = monthly_income - total_spent
        lines.append(f"You have an unallocated surplus of {surplus:,.2f} this month; consider moving it to savings.")
    return " ".join(lines)


def generate_spending_insights(monthly_income, expenses_by_category, previous_month_expenses=None):
    """Returns a short list of natural-language insight strings."""
    if _gemini_available():
        try:
            prompt = (
                f"Monthly income: {monthly_income}. Expenses by category this month: "
                f"{json.dumps(expenses_by_category)}. Previous month expenses: "
                f"{json.dumps(previous_month_expenses or {})}. "
                "Give exactly 3 short, specific, actionable spending insights as a "
                "JSON array of strings, nothing else."
            )
            raw = _call_gemini(prompt).strip()
            raw = raw.replace("```json", "").replace("```", "").strip()
            insights = json.loads(raw)
            if isinstance(insights, list) and insights:
                return insights[:3]
        except Exception:
            pass

    return _rule_based_insights(monthly_income, expenses_by_category, previous_month_expenses)


def _rule_based_insights(monthly_income, expenses_by_category, previous_month_expenses):
    insights = []
    if not expenses_by_category:
        return ["No expenses logged yet this month — start tracking to unlock insights."]

    top_cat = max(expenses_by_category, key=expenses_by_category.get)
    insights.append(f"Your biggest spending category this month is {top_cat} at {expenses_by_category[top_cat]:,.2f}.")

    total_spent = sum(expenses_by_category.values())
    if monthly_income > 0:
        savings_rate = max(0, (monthly_income - total_spent) / monthly_income) * 100
        insights.append(f"You're currently saving about {savings_rate:.1f}% of your income this month.")

    if previous_month_expenses:
        for cat, amt in expenses_by_category.items():
            prev = previous_month_expenses.get(cat, 0)
            if prev > 0 and amt > prev * 1.2:
                pct = ((amt - prev) / prev) * 100
                insights.append(f"{cat} spending rose {pct:.0f}% compared to last month.")
                break

    if len(insights) < 3:
        insights.append("Set a saving goal to stay motivated and track progress over time.")

    return insights[:3]
