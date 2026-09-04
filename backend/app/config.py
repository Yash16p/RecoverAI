import os
from dotenv import load_dotenv

load_dotenv()

class Settings:
    # Razorpay credentials (Test Mode)
    RAZORPAY_KEY_ID: str = os.getenv("RAZORPAY_KEY_ID", "")
    RAZORPAY_KEY_SECRET: str = os.getenv("RAZORPAY_KEY_SECRET", "")
    RAZORPAY_WEBHOOK_SECRET: str = os.getenv("RAZORPAY_WEBHOOK_SECRET", "")

    # App
    APP_ENV: str = os.getenv("APP_ENV", "development")
    DEBUG: bool = os.getenv("DEBUG", "true").lower() == "true"

    # LLM — Gemini (fallback)
    GEMINI_API_KEY: str = os.getenv("GEMINI_API_KEY", "")
    LLM_MODEL: str = os.getenv("LLM_MODEL", "gemini-2.5-flash")

    # LLM — Groq (primary — 14,400 RPD free tier, very fast)
    GROQ_API_KEY: str  = os.getenv("GROQ_API_KEY", "")
    GROQ_MODEL: str    = os.getenv("GROQ_MODEL", "qwen/qwen3.8-27b")
    DATABASE_URL: str = os.getenv("DATABASE_URL", "postgresql://postgres:postgres@localhost:5432/recoverai")

    # Redis — wired later
    REDIS_URL: str = os.getenv("REDIS_URL", "redis://localhost:6379/0")

    # Ngrok / public base URL (set after ngrok tunnel is up)
    PUBLIC_BASE_URL: str = os.getenv("PUBLIC_BASE_URL", "http://localhost:8000")

    # Langfuse observability
    LANGFUSE_PUBLIC_KEY: str = os.getenv("LANGFUSE_PUBLIC_KEY", "")
    LANGFUSE_SECRET_KEY: str = os.getenv("LANGFUSE_SECRET_KEY", "")
    LANGFUSE_HOST: str = os.getenv("LANGFUSE_HOST", "https://cloud.langfuse.com")

settings = Settings()
