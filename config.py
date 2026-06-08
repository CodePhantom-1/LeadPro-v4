
"""
LeadPro v3 — Configuration
Safe int/bool parsing — handles empty strings in .env gracefully.
"""
import os
import sys
import secrets
import warnings
import base64
import hashlib
from dotenv import load_dotenv
load_dotenv()


def _int(key: str, default: int) -> int:
    """Safely parse an env var as int. Returns default if empty or invalid."""
    val = os.getenv(key, "")
    if not val or not val.strip():
        return default
    try:
        return int(val.strip())
    except ValueError:
        return default


def _bool(key: str, default: bool = False) -> bool:
    """Safely parse an env var as bool."""
    val = os.getenv(key, "").strip().lower()
    if not val:
        return default
    return val in ("true", "1", "yes")


def _str(key: str, default: str = "") -> str:
    """Get env var with fallback, strip whitespace."""
    return (os.getenv(key) or default).strip()


# ── Crypto helpers for SMTP passwords at rest ──
_ENCRYPTION_KEY: bytes | None = None

def _derive_encryption_key() -> bytes:
    """Derive a 32-byte Fernet-compatible key from JWT_SECRET using SHA-256."""
    global _ENCRYPTION_KEY
    if _ENCRYPTION_KEY is not None:
        return _ENCRYPTION_KEY
    raw = (JWT_SECRET or "insecure-fallback-key-change-me").encode("utf-8")
    _ENCRYPTION_KEY = hashlib.sha256(raw).digest()
    return _ENCRYPTION_KEY


def encrypt_password(plaintext: str) -> str:
    """Encrypt a password with AES-256-CBC (Fernet). Returns base64 token."""
    if not plaintext:
        return ""
    try:
        from cryptography.fernet import Fernet
        key = base64.urlsafe_b64encode(_derive_encryption_key())
        f = Fernet(key)
        return f.encrypt(plaintext.encode("utf-8")).decode("utf-8")
    except Exception:
        return plaintext


def decrypt_password(ciphertext: str) -> str:
    """Decrypt a password. Handles both encrypted tokens and plaintext (backward compat)."""
    if not ciphertext:
        return ""
    # Detect plaintext: no dots (Fernet tokens always have dots)
    if "." not in ciphertext:
        return ciphertext
    try:
        from cryptography.fernet import Fernet, InvalidToken
        key = base64.urlsafe_b64encode(_derive_encryption_key())
        f = Fernet(key)
        return f.decrypt(ciphertext.encode("utf-8")).decode("utf-8")
    except (InvalidToken, Exception):
        return ciphertext


def _load_all():
    """Load all configuration variables from environment."""
    global SERPER_API_KEY, OPENROUTER_API_KEY, PAGESPEED_API_KEY, HUNTER_API_KEY
    global GOOGLE_PLACES_API_KEY, YELP_API_KEY, CRUNCHBASE_API_KEY, SHOPIFY_API_KEY
    global GITHUB_API_KEY, CLEARBIT_API_KEY, WHATSAPP_ACCOUNT_SID, WHATSAPP_AUTH_TOKEN
    global WHATSAPP_PHONE_NUMBER, PHANTOMBUSTER_API_KEY, TWILIO_ACCOUNT_SID, TWILIO_AUTH_TOKEN
    global TWILIO_PHONE_FROM, LINKEDIN_SESSION_COOKIE, SLACK_WEBHOOK_URL, DISCORD_WEBHOOK_URL
    global JWT_SECRET, YOUR_NAME, YOUR_COMPANY, YOUR_EMAIL, YOUR_APP_PASSWORD
    global SMTP_SERVER, SMTP_PORT, IMAP_SERVER, DB_PATH, SCRAPE_THREADS
    global EMAIL_DELAY_SEC, DAILY_EMAIL_CAP, TRACKING_DOMAIN, BASE_URL, MAIL_DOMAINS
    global POSTFIX_HOST, POSTFIX_PORT, WARMUP_ENABLED, USE_LEGACY_LEADGEN
    global INTEL_COMPETITOR_COUNT, INTEL_KEYWORD_COUNT, AUDIT_PAGE_EXPIRY_HOURS, ALLOWED_ORIGINS, VERIFY_SSL
    global SOCIAL_PROOF_TEXT, CACHE_TTL_SECONDS, SCHEDULER_ENABLED, REPLY_INTELLIGENCE_ENABLED
    global AB_TEST_ENABLED, FULLTEXT_SEARCH_ENABLED, MAX_CONCURRENT_TASKS
    global RATE_LIMIT_PER_MINUTE, RATE_LIMIT_PER_DAY
    global BREVO_SMTP_LOGIN, BREVO_SMTP_KEY, BREVO_SMTP_HOST, BREVO_SMTP_PORT
    global CENTRAL_INBOX_EMAIL, CENTRAL_INBOX_PASSWORD, CENTRAL_INBOX_IMAP
    
    SERPER_API_KEY = _str("SERPER_API_KEY")
    OPENROUTER_API_KEY = _str("OPENROUTER_API_KEY")
    PAGESPEED_API_KEY = _str("PAGESPEED_API_KEY")
    HUNTER_API_KEY = _str("HUNTER_API_KEY")
    GOOGLE_PLACES_API_KEY = _str("GOOGLE_PLACES_API_KEY")
    YELP_API_KEY = _str("YELP_API_KEY")
    CRUNCHBASE_API_KEY = _str("CRUNCHBASE_API_KEY")
    SHOPIFY_API_KEY = _str("SHOPIFY_API_KEY")
    GITHUB_API_KEY = _str("GITHUB_API_KEY")
    CLEARBIT_API_KEY = _str("CLEARBIT_API_KEY")
    WHATSAPP_ACCOUNT_SID = _str("WHATSAPP_ACCOUNT_SID")
    WHATSAPP_AUTH_TOKEN = _str("WHATSAPP_AUTH_TOKEN")
    WHATSAPP_PHONE_NUMBER = _str("WHATSAPP_PHONE_NUMBER")
    PHANTOMBUSTER_API_KEY = _str("PHANTOMBUSTER_API_KEY")
    TWILIO_ACCOUNT_SID = _str("TWILIO_ACCOUNT_SID")
    TWILIO_AUTH_TOKEN = _str("TWILIO_AUTH_TOKEN")
    TWILIO_PHONE_FROM = _str("TWILIO_PHONE_FROM")
    LINKEDIN_SESSION_COOKIE = _str("LINKEDIN_SESSION_COOKIE")
    SLACK_WEBHOOK_URL = _str("SLACK_WEBHOOK_URL")
    DISCORD_WEBHOOK_URL = _str("DISCORD_WEBHOOK_URL")
    _new_jwt = _str("JWT_SECRET")
    if _new_jwt and _new_jwt != "generate-random-secret-on-startup":
        JWT_SECRET = _new_jwt
    YOUR_NAME = _str("YOUR_NAME", "Alex")
    YOUR_COMPANY = _str("YOUR_COMPANY", "DD Marketer")
    YOUR_EMAIL = _str("YOUR_EMAIL")
    YOUR_APP_PASSWORD = _str("YOUR_APP_PASSWORD")
    SMTP_SERVER = _str("SMTP_SERVER", "smtp.gmail.com")
    SMTP_PORT = _int("SMTP_PORT", 587)
    IMAP_SERVER = _str("IMAP_SERVER", "imap.gmail.com")
    DB_PATH = _str("DB_PATH", "leadpro.db")
    SCRAPE_THREADS = _int("SCRAPE_THREADS", 30)
    EMAIL_DELAY_SEC = _int("EMAIL_DELAY_SEC", 20)
    DAILY_EMAIL_CAP = _int("DAILY_EMAIL_CAP", 200)
    TRACKING_DOMAIN = _str("TRACKING_DOMAIN")
    BASE_URL = _str("BASE_URL", "http://localhost:8000")
    MAIL_DOMAINS = _str("MAIL_DOMAINS")
    POSTFIX_HOST = _str("POSTFIX_HOST")
    POSTFIX_PORT = _int("POSTFIX_PORT", 25)
    WARMUP_ENABLED = _bool("WARMUP_ENABLED", False)
    USE_LEGACY_LEADGEN = _bool("USE_LEGACY_LEADGEN", False)
    INTEL_COMPETITOR_COUNT = _int("INTEL_COMPETITOR_COUNT", 3)
    INTEL_KEYWORD_COUNT = _int("INTEL_KEYWORD_COUNT", 5)
    raw_origins = _str("ALLOWED_ORIGINS", "*")
    ALLOWED_ORIGINS = [o.strip() for o in raw_origins.split(",") if o.strip()]
    VERIFY_SSL = _bool("VERIFY_SSL", True)
    AUDIT_PAGE_EXPIRY_HOURS = _int("AUDIT_PAGE_EXPIRY_HOURS", 48)
    SOCIAL_PROOF_TEXT = _str("SOCIAL_PROOF_TEXT", 
        "Join 500+ businesses that used this audit to improve their digital presence.")
    CACHE_TTL_SECONDS = _int("CACHE_TTL_SECONDS", 3600)
    SCHEDULER_ENABLED = _bool("SCHEDULER_ENABLED", True)
    REPLY_INTELLIGENCE_ENABLED = _bool("REPLY_INTELLIGENCE_ENABLED", True)
    AB_TEST_ENABLED = _bool("AB_TEST_ENABLED", True)
    FULLTEXT_SEARCH_ENABLED = _bool("FULLTEXT_SEARCH_ENABLED", True)
    MAX_CONCURRENT_TASKS = _int("MAX_CONCURRENT_TASKS", 5)
    RATE_LIMIT_PER_MINUTE = _int("RATE_LIMIT_PER_MINUTE", 60)
    RATE_LIMIT_PER_DAY = _int("RATE_LIMIT_PER_DAY", 1000)

    BREVO_SMTP_LOGIN = _str("BREVO_SMTP_LOGIN")
    BREVO_SMTP_KEY = _str("BREVO_SMTP_KEY")
    BREVO_SMTP_HOST = _str("BREVO_SMTP_HOST", "smtp-relay.brevo.com")
    BREVO_SMTP_PORT = _int("BREVO_SMTP_PORT", 587)

    CENTRAL_INBOX_EMAIL = _str("CENTRAL_INBOX_EMAIL")
    CENTRAL_INBOX_PASSWORD = _str("CENTRAL_INBOX_PASSWORD")
    CENTRAL_INBOX_IMAP = _str("CENTRAL_INBOX_IMAP", "imap.gmail.com")






    

JWT_SECRET = ""
ALLOWED_ORIGINS: list[str] = ["*"]
VERIFY_SSL: bool = True

_load_all()

# ── Database ──────────────────────────────────────────────────────────────────


# ── Engine Tuning ─────────────────────────────────────────────────────────────

AVG_LEADS_PER_QUERY   = 15
REQUEST_TIMEOUT       = 12
BATCH_COMMIT_SIZE     = 25

# ── Outreach Pacing ───────────────────────────────────────────────────────────

RATE_LIMIT_PAUSE_SEC  = 30
FOLLOWUP_SCHEDULE     = [3, 7, 14]

# ── Tracking ──────────────────────────────────────────────────────────────────


# ── Mail Infrastructure ───────────────────────────────────────────────────────


# ── Warmup ────────────────────────────────────────────────────────────────────




# ── Lead Scoring Weights ──────────────────────────────────────────────────────
SCORE_WEIGHTS = {
    "has_website": -15, "has_ssl": -5, "is_mobile_friendly": -8,
    "has_tracking_pixel": -10, "rating_below_4": 10, "free_email": 8,
    "site_dead": 12, "low_review_count": 7, "no_social_presence": 10,
    "slow_pagespeed": 8,
}

# ── AI Model Pool ─────────────────────────────────────────────────────────────
AI_MODELS = [
    "google/gemma-3-27b-it:free",
    "google/gemini-2.0-flash-exp:free",
    "meta-llama/llama-3.3-70b-instruct:free",
    "mistralai/mistral-small-3.1-24b-instruct:free",
    "nousresearch/hermes-3-llama-3.1-405b:free",
    "deepseek/deepseek-r1-0528:free",
    "mistralai/mistral-7b-instruct:free",
    "meta-llama/llama-3.2-3b-instruct:free",
    "microsoft/phi-4:free",
]

# ── Competitor Filters ────────────────────────────────────────────────────────
COMPETITOR_KEYWORDS = [
    "marketing", "agency", "web design", "seo", "media group",
    "digital solutions", "consulting", "advertising", "software",
    "creative studio", "growth hacking",
]



# Niche-specific social proof examples
NICHE_SOCIAL_PROOF = {
    "dentist": "23 dental clinics used this audit to increase patient bookings by 40%",
    "roofer": "18 roofing companies fixed website gaps and doubled lead volume",
    "plumber": "31 plumbing businesses improved online presence and reduced no-shows",
    "lawyer": "27 law firms used similar audits to increase case inquiries",
    "chiropractor": "19 chiropractic clinics grew patient acquisition by 35%",
    "hvac": "22 HVAC companies streamlined booking and increased service calls",
    "auto shop": "16 auto repair shops improved visibility and customer retention",
}

# ── Spam-Trigger Words ────────────────────────────────────────────────────────
SPAM_WORDS = [
    "free", "guaranteed", "click here", "buy now", "limited time",
    "act now", "earn money", "make money", "no obligation", "100%",
    "no risk", "winner", "congratulations", "cash", "bonus",
    "discount", "offer expires", "exclusive deal", "once in a lifetime",
    "double your", "amazing", "incredible", "unbelievable",
]


# ── Startup Validation ──────────────────────────────────────────────────────
def _load_or_generate_jwt_secret() -> str:
    """Load JWT secret from .env or generate + persist one (thread-safe via fcntl).

    Persists the generated secret to `.jwt_secret` in the working directory so
    it survives server restarts (prevents invalidating all issued tokens on
    every boot). This file should be added to .gitignore.
    """
    import fcntl  # moved here from top-level (crashes on Windows)
    env_val = os.getenv("JWT_SECRET", "").strip()
    if env_val and env_val != "generate-random-secret-on-startup":
        return env_val
    secret_path = os.path.join(os.path.dirname(os.path.abspath(__file__)), ".jwt_secret")
    lock_path = secret_path + ".lock"
    # Read with no lock first (fast path)
    try:
        if os.path.exists(secret_path):
            with open(secret_path, "r", encoding="utf-8") as f:
                val = f.read().strip()
                if val:
                    return val
    except Exception:
        pass
    # Acquire a file lock to prevent TOCTOU between read and write
    try:
        with open(lock_path, "w", encoding="utf-8") as lf:
            fcntl.flock(lf, fcntl.LOCK_EX)
            try:
                # Double-check after acquiring lock
                if os.path.exists(secret_path):
                    with open(secret_path, "r", encoding="utf-8") as f:
                        val = f.read().strip()
                        if val:
                            return val
                new_secret = secrets.token_urlsafe(64)
                with open(secret_path, "w", encoding="utf-8") as f:
                    f.write(new_secret)
                try:
                    os.chmod(secret_path, 0o600)
                except Exception:
                    pass
                return new_secret
            finally:
                fcntl.flock(lf, fcntl.LOCK_UN)
    except Exception as e:
        import logging
        _log = logging.getLogger("leadspro.config")
        _log.warning("Could not acquire JWT secret lock (%s). Using ephemeral token.", str(e))
    # Absolute fallback — generate ephemeral
    return secrets.token_urlsafe(64)


def validate_config():
    """Validate required configuration and load/generate secrets if missing.

    Safe to run multiple times. Does *not* regenerate JWT_SECRET once one is
    loaded from .env or persisted to `.jwt_secret`.
    """
    warnings.simplefilter("always", UserWarning)
    global JWT_SECRET, CACHE_TTL_SECONDS, RATE_LIMIT_PER_MINUTE

    # Required for core functionality
    required = [
        ("SERPER_API_KEY", SERPER_API_KEY),
        ("OPENROUTER_API_KEY", OPENROUTER_API_KEY),
    ]
    for name, value in required:
        if not value:
            warnings.warn(
                f"[WARN] Required config {name} is missing. Some features may fail.",
                UserWarning,
            )

    # Load or generate JWT secret (persistent across restarts)
    if JWT_SECRET in ("", "generate-random-secret-on-startup"):
        JWT_SECRET = _load_or_generate_jwt_secret()

    # Notify about optional integrations
    optional = [
        ("PHANTOMBUSTER_API_KEY", PHANTOMBUSTER_API_KEY, "LinkedIn outreach"),
        ("TWILIO_ACCOUNT_SID", TWILIO_ACCOUNT_SID, "SMS sending"),
        ("SLACK_WEBHOOK_URL", SLACK_WEBHOOK_URL, "Slack notifications"),
        ("DISCORD_WEBHOOK_URL", DISCORD_WEBHOOK_URL, "Discord notifications"),
    ]
    for name, value, feature in optional:
        if not value:
            warnings.warn(f"[SKIP] {feature} disabled: {name} not set.", UserWarning)

    # Validate numeric ranges
    if CACHE_TTL_SECONDS < 60:
        warnings.warn(
            f"[WARN] CACHE_TTL_SECONDS too low ({CACHE_TTL_SECONDS}s), using 60s.",
            UserWarning,
        )
        CACHE_TTL_SECONDS = 60

    if RATE_LIMIT_PER_MINUTE <= 0:
        warnings.warn(
            f"[WARN] RATE_LIMIT_PER_MINUTE invalid ({RATE_LIMIT_PER_MINUTE}), using 60.",
            UserWarning,
        )
        RATE_LIMIT_PER_MINUTE = 60


def reload_config():
    """Reload .env file and update configuration dynamically."""
    from dotenv import load_dotenv
    load_dotenv(override=True)
    _load_all()
    validate_config()
    warnings.warn("[OK] Configuration reloaded from .env", UserWarning)


# Run validation when module loads
validate_config()