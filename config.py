"""قراءة الإعدادات من ملف .env (محلياً) أو من Secrets (على GitHub)."""
import os
from dotenv import load_dotenv

load_dotenv()


def _float(name: str, default: float) -> float:
    try:
        return float(os.getenv(name) or default)
    except ValueError:
        return default


GROQ_API_KEY = os.getenv("GROQ_API_KEY", "")
GROQ_MODEL = os.getenv("GROQ_MODEL") or "openai/gpt-oss-120b"
TELEGRAM_BOT_TOKEN = os.getenv("TELEGRAM_BOT_TOKEN", "")
TELEGRAM_CHAT_ID = os.getenv("TELEGRAM_CHAT_ID", "")
SUPABASE_URL = os.getenv("SUPABASE_URL", "")
SUPABASE_KEY = os.getenv("SUPABASE_KEY", "")
CAPITAL_EGP = _float("CAPITAL_EGP", 50000)   # قيمة أولية فقط
RISK_PCT = _float("RISK_PCT", 0.02)          # أقصى مخاطرة للصفقة = 2%
