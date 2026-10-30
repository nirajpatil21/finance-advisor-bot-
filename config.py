import os
from dotenv import load_dotenv

BASE_DIR = os.path.abspath(os.path.dirname(__file__))
load_dotenv()

IS_VERCEL = os.environ.get("VERCEL") == "1"
DATABASE_URL = os.environ.get("DATABASE_URL")
SECRET_KEY = os.environ.get("SECRET_KEY")

if IS_VERCEL and not SECRET_KEY:
    raise RuntimeError("Set the SECRET_KEY environment variable in your Vercel project.")

if IS_VERCEL and not DATABASE_URL:
    raise RuntimeError("Set DATABASE_URL to a persistent PostgreSQL database in your Vercel project.")

if IS_VERCEL and DATABASE_URL.startswith("sqlite:"):
    raise RuntimeError("Vercel deployments require a persistent PostgreSQL DATABASE_URL; SQLite is ephemeral.")

if DATABASE_URL and DATABASE_URL.startswith(("postgres://", "postgresql://")):
    DATABASE_URL = DATABASE_URL.replace("postgres://", "postgresql+psycopg://", 1)
    DATABASE_URL = DATABASE_URL.replace("postgresql://", "postgresql+psycopg://", 1)


class Config:
    SECRET_KEY = SECRET_KEY or "dev-secret-key-change-in-production"
    SQLALCHEMY_DATABASE_URI = DATABASE_URL or f"sqlite:///{os.path.join(BASE_DIR, 'finance_advisor.db')}"
    SQLALCHEMY_TRACK_MODIFICATIONS = False
    GEMINI_API_KEY = os.environ.get("GEMINI_API_KEY", "")
    SESSION_COOKIE_HTTPONLY = True
    SESSION_COOKIE_SAMESITE = "Lax"
    SESSION_COOKIE_SECURE = IS_VERCEL
