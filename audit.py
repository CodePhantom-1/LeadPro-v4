"""
LeadPro v3 — Consolidated Audit Pipeline
Combines:
- auditor.py (core website auditing)
- ops_auditor.py (operations auditing)
- enrichment.py (decision-maker extraction)
- intent_signals.py (intent scoring)
- roi_calculator.py (revenue impact estimation)
"""
import re
import json
import asyncio
import aiohttp
import logging
import os
import threading
from urllib.parse import urlparse
from bs4 import BeautifulSoup
from config import PAGESPEED_API_KEY, SCORE_WEIGHTS, COMPETITOR_KEYWORDS, HUNTER_API_KEY, CLEARBIT_API_KEY, VERIFY_SSL

FX_CACHE_FILE = os.path.join(os.path.dirname(os.path.abspath(__file__)), ".fx_cache.json")
_log = logging.getLogger("audit.fx")

# ============================================================================
# ROI Calculator (from roi_calculator.py)
# ============================================================================

NICHE_REVENUE = {
    "dentist": 800_000, "restaurant": 500_000, "plumber": 300_000,
    "hvac": 450_000, "lawyer": 1_200_000, "salon": 250_000,
    "med spa": 600_000, "chiropractor": 350_000, "auto shop": 400_000,
    "gym": 500_000, "vet": 600_000, "accountant": 500_000,
    "pest control": 250_000, "cleaning": 200_000, "roofer": 350_000,
    "electrician": 300_000, "barber": 200_000, "physio": 350_000,
}

GAP_IMPACT = {
    "no website": 0.25, "no online booking": 0.15, "no online ordering": 0.20,
    "no tracking pixel": 0.08, "not mobile-friendly": 0.12,
    "no ssl": 0.05, "no social media": 0.06, "no email marketing": 0.10,
    "no live chat": 0.03, "no loyalty": 0.08, "no crm": 0.10,
    "poor rating": 0.15, "no online payment": 0.05, "no gift card": 0.03,
    "slow website": 0.06, "broken website": 0.20, "outdated website": 0.04,
    "limited platform": 0.04, "very few reviews": 0.05,
    "free email": 0.02, "ada non-compliant": 0.02,
}

INDUSTRY_ECON_SCALE = {
    "plumber": 1.0, "electrician": 1.0, "cleaning": 1.0, "barber": 1.0,
    "roofer": 0.95, "pest control": 0.95, "hvac": 0.90,
    "restaurant": 0.90, "salon": 0.90, "gym": 0.85, "auto shop": 0.85,
    "vet": 0.80, "chiropractor": 0.80, "physio": 0.80, "med spa": 0.75,
    "dentist": 0.70, "accountant": 0.75, "lawyer": 0.70,
}

DEFAULT_ECON_SCALE = 0.85

COUNTRY_DATA = {
    "us": {"gdp": 1.00, "lang": "en", "currency": "USD", "symbol": "$"},
    "usa": {"gdp": 1.00, "lang": "en", "currency": "USD", "symbol": "$"},
    "united states": {"gdp": 1.00, "lang": "en", "currency": "USD", "symbol": "$"},
    "uk": {"gdp": 0.76, "lang": "en", "currency": "GBP", "symbol": "£"},
    "united kingdom": {"gdp": 0.76, "lang": "en", "currency": "GBP", "symbol": "£"},
    "gb": {"gdp": 0.76, "lang": "en", "currency": "GBP", "symbol": "£"},
    "canada": {"gdp": 0.78, "lang": "en", "currency": "CAD", "symbol": "C$"},
    "australia": {"gdp": 0.83, "lang": "en", "currency": "AUD", "symbol": "A$"},
    "new zealand": {"gdp": 0.66, "lang": "en", "currency": "NZD", "symbol": "NZ$"},
    "ireland": {"gdp": 0.95, "lang": "en", "currency": "EUR", "symbol": "€"},
    "philippines": {"gdp": 0.13, "lang": "en", "currency": "PHP", "symbol": "₱"},
    "south africa": {"gdp": 0.18, "lang": "en", "currency": "ZAR", "symbol": "R"},
    "india": {"gdp": 0.11, "lang": "en", "currency": "INR", "symbol": "₹"},
    "malaysia": {"gdp": 0.32, "lang": "en", "currency": "MYR", "symbol": "RM"},
    "singapore": {"gdp": 1.32, "lang": "en", "currency": "SGD", "symbol": "S$"},
    "uae": {"gdp": 0.79, "lang": "en", "currency": "AED", "symbol": "AED"},
    "nigeria": {"gdp": 0.10, "lang": "en", "currency": "NGN", "symbol": "₦"},
    "germany": {"gdp": 0.83, "lang": "de", "currency": "EUR", "symbol": "€"},
    "austria": {"gdp": 0.79, "lang": "de", "currency": "EUR", "symbol": "€"},
    "switzerland": {"gdp": 0.95, "lang": "de", "currency": "CHF", "symbol": "CHF"},
    "france": {"gdp": 0.74, "lang": "fr", "currency": "EUR", "symbol": "€"},
    "belgium": {"gdp": 0.75, "lang": "fr", "currency": "EUR", "symbol": "€"},
    "spain": {"gdp": 0.55, "lang": "es", "currency": "EUR", "symbol": "€"},
    "mexico": {"gdp": 0.26, "lang": "es", "currency": "MXN", "symbol": "Mex$"},
    "colombia": {"gdp": 0.24, "lang": "es", "currency": "COP", "symbol": "COP$"},
    "argentina": {"gdp": 0.33, "lang": "es", "currency": "ARS", "symbol": "AR$"},
    "chile": {"gdp": 0.39, "lang": "es", "currency": "CLP", "symbol": "CLP$"},
    "peru": {"gdp": 0.20, "lang": "es", "currency": "PEN", "symbol": "S/"},
    "italy": {"gdp": 0.53, "lang": "it", "currency": "EUR", "symbol": "€"},
    "portugal": {"gdp": 0.53, "lang": "pt", "currency": "EUR", "symbol": "€"},
    "brazil": {"gdp": 0.24, "lang": "pt", "currency": "BRL", "symbol": "R$"},
    "netherlands": {"gdp": 0.87, "lang": "nl", "currency": "EUR", "symbol": "€"},
    "poland": {"gdp": 0.49, "lang": "pl", "currency": "PLN", "symbol": "zł"},
    "czech republic": {"gdp": 0.54, "lang": "cs", "currency": "CZK", "symbol": "Kč"},
    "romania": {"gdp": 0.39, "lang": "ro", "currency": "RON", "symbol": "lei"},
    "hungary": {"gdp": 0.46, "lang": "hu", "currency": "HUF", "symbol": "Ft"},
    "croatia": {"gdp": 0.49, "lang": "hr", "currency": "EUR", "symbol": "€"},
    "greece": {"gdp": 0.47, "lang": "el", "currency": "EUR", "symbol": "€"},
    "finland": {"gdp": 0.76, "lang": "fi", "currency": "EUR", "symbol": "€"},
    "denmark": {"gdp": 0.86, "lang": "da", "currency": "DKK", "symbol": "kr"},
    "sweden": {"gdp": 0.79, "lang": "sv", "currency": "SEK", "symbol": "kr"},
    "norway": {"gdp": 0.92, "lang": "no", "currency": "NOK", "symbol": "kr"},
    "japan": {"gdp": 0.66, "lang": "ja", "currency": "JPY", "symbol": "¥"},
    "south korea": {"gdp": 0.70, "lang": "ko", "currency": "KRW", "symbol": "₩"},
    "saudi arabia": {"gdp": 0.58, "lang": "ar", "currency": "SAR", "symbol": "SAR"},
    "israel": {"gdp": 0.64, "lang": "he", "currency": "ILS", "symbol": "₪"},
    "turkey": {"gdp": 0.18, "lang": "tr", "currency": "TRY", "symbol": "₺"},
    "thailand": {"gdp": 0.26, "lang": "th", "currency": "THB", "symbol": "฿"},
    "indonesia": {"gdp": 0.20, "lang": "id", "currency": "IDR", "symbol": "Rp"},
    "vietnam": {"gdp": 0.18, "lang": "vi", "currency": "VND", "symbol": "₫"},
    "ukraine": {"gdp": 0.14, "lang": "uk", "currency": "UAH", "symbol": "₴"},
    "russia": {"gdp": 0.26, "lang": "ru", "currency": "RUB", "symbol": "₽"},
    "china": {"gdp": 0.29, "lang": "zh", "currency": "CNY", "symbol": "¥"},
    "taiwan": {"gdp": 0.70, "lang": "zh", "currency": "TWD", "symbol": "NT$"},
}

DEFAULT_COUNTRY = {"gdp": 0.30, "lang": "en", "currency": "USD", "symbol": "$"}

DEFAULT_CURRENCY = {"code": "USD", "symbol": "$", "rate": 1.0}

_FX_RATES = {"USD": 1.0}
_FX_LOADED = False
_fx_lock = threading.Lock()


def _load_fx_cache() -> dict:
    try:
        if os.path.exists(FX_CACHE_FILE):
            with open(FX_CACHE_FILE, "r", encoding="utf-8") as f:
                data = json.load(f)
            if isinstance(data, dict) and "rates" in data:
                return data
    except Exception as e:
        _log.warning("failed to load FX cache: %s", e)
    return {}


def _save_fx_cache(rates: dict):
    try:
        with open(FX_CACHE_FILE, "w", encoding="utf-8") as f:
            json.dump(rates, f)
    except Exception as e:
        _log.warning("failed to save FX cache: %s", e)


def _apply_fx_rates(rates: dict):
    global _FX_RATES, _FX_LOADED
    with _fx_lock:
        _FX_RATES = rates.get("rates", rates) if isinstance(rates, dict) else {}
        if "USD" not in _FX_RATES:
            _FX_RATES["USD"] = 1.0
        _FX_LOADED = True
    _log.info("FX rates updated — %d currencies loaded", len(_FX_RATES))


def refresh_fx_rates():
    """Fetch live FX rates from open.er-api.com (free, no key). Sync wrapper."""
    try:
        import urllib.request
        url = "https://open.er-api.com/v6/latest/USD"
        with urllib.request.urlopen(url, timeout=15) as resp:
            data = json.loads(resp.read().decode())
        if data.get("result") == "success" and "rates" in data:
            cache = {"rates": data["rates"], "updated": data.get("time_last_update_utc", "")}
            _apply_fx_rates(cache)
            _save_fx_cache(cache)
            return True
    except Exception as e:
        _log.warning("FX rate fetch failed: %s", e)
    return False


async def refresh_fx_rates_async():
    """Async version of refresh_fx_rates."""
    try:
        async with aiohttp.ClientSession() as session:
            async with session.get(
                "https://open.er-api.com/v6/latest/USD",
                timeout=aiohttp.ClientTimeout(total=15),
            ) as resp:
                data = await resp.json(content_type=None)
        if data.get("result") == "success" and "rates" in data:
            cache = {"rates": data["rates"], "updated": data.get("time_last_update_utc", "")}
            _apply_fx_rates(cache)
            _save_fx_cache(cache)
            return True
    except Exception as e:
        _log.warning("FX rate fetch failed: %s", e)
    return False


def init_fx_rates():
    """Load FX rates on startup: cache file first, then live fetch in background."""
    global _FX_LOADED
    cached = _load_fx_cache()
    if cached and "rates" in cached:
        _apply_fx_rates(cached)
        _log.info("Loaded FX rates from cache")
    else:
        with _fx_lock:
            _FX_RATES["USD"] = 1.0
            _FX_LOADED = True

    def _bg_fetch():
        if refresh_fx_rates():
            _log.info("Live FX rates fetched on startup")
        else:
            _log.warning("Live FX fetch failed — using cache/defaults")

    t = threading.Thread(target=_bg_fetch, daemon=True)
    t.start()


def _get_country_info(country: str) -> dict:
    if not country:
        return DEFAULT_COUNTRY.copy()
    return COUNTRY_DATA.get(country.lower().strip(), DEFAULT_COUNTRY).copy()


def get_currency_for_country(country: str) -> dict:
    info = _get_country_info(country)
    code = info.get("currency", "USD")
    with _fx_lock:
        rate = _FX_RATES.get(code, 1.0)
    return {"code": code, "symbol": info.get("symbol", "$"), "rate": rate}


def get_language_for_country(country: str) -> str:
    return _get_country_info(country).get("lang", "en")


def get_econ_multiplier(niche: str, country: str) -> float:
    info = _get_country_info(country)
    gdp_ratio = info.get("gdp", 0.30)
    niche_lower = (niche or "").lower()
    elasticity = DEFAULT_ECON_SCALE
    for key, val in INDUSTRY_ECON_SCALE.items():
        if key in niche_lower:
            elasticity = val
            break
    return gdp_ratio ** elasticity


def estimate_revenue_impact(niche: str, pain_points: list[str],
                             ops_pains: list[dict] = None,
                             country: str = "") -> dict:
    niche_lower = (niche or "").lower()
    base = 300_000
    for key, rev in NICHE_REVENUE.items():
        if key in niche_lower:
            base = rev
            break

    currency = get_currency_for_country(country)
    fx = currency["rate"]

    mult = get_econ_multiplier(niche, country)
    est_annual = base * mult * fx
    est_monthly = est_annual / 12

    impacts = []
    total_lost = 0

    # Marketing pain points
    for pain in pain_points:
        pain_lower = pain.lower()
        for gap_key, pct in GAP_IMPACT.items():
            if gap_key in pain_lower:
                loss = round(est_monthly * pct)
                impacts.append({"issue": pain, "monthly_loss": loss,
                                "annual_loss": loss * 12})
                total_lost += loss
                break

    # Ops pain points (these carry their own estimates in USD)
    if ops_pains:
        for op in ops_pains:
            ml = op.get("monthly_loss", 0)
            if ml > 0:
                adj = round(ml * mult * fx)
                impacts.append({"issue": op["pain"], "monthly_loss": adj,
                                "annual_loss": adj * 12})
                total_lost += adj

    return {
        "estimated_monthly_revenue": round(est_monthly),
        "impacts": impacts,
        "total_monthly_loss": total_lost,
        "total_annual_loss": total_lost * 12,
        "currency_code": currency["code"],
        "currency_symbol": currency["symbol"],
    }

# ============================================================================
# Intent Signals (from intent_signals.py)
# ============================================================================

def calculate_intent_score(lead: dict, tech_stack: dict,
                            signals: dict) -> dict:
    """Returns {intent_score (0-100), reasons, readiness}."""
    score = 0
    reasons = []

    reviews = lead.get("review_count", 0)
    rating = lead.get("rating", 0)

    if reviews > 50 and rating >= 4.0:
        score += 15
        reasons.append("Active business — strong review volume")
    if reviews > 10 and rating < 3.5:
        score += 20
        reasons.append("Bad reviews + volume — owner likely aware of problems")

    # Hiring = investing in growth
    hiring = tech_stack.get("hiring", ["none"])
    if hiring != ["none"]:
        score += 25
        reasons.append("Actively hiring — growth mode")

    # Has some tech but gaps = upgrade mindset
    has_some = any(tech_stack.get(c, ["none"]) != ["none"]
                   for c in ["cms", "payments"])
    has_gaps = any(tech_stack.get(c, ["none"]) == ["none"]
                   for c in ["booking", "crm", "email_marketing", "chat"])
    if has_some and has_gaps:
        score += 20
        reasons.append("Already uses some tech — gaps are upgrade opportunities")

    # Basic CMS = outgrowing platform
    cms = tech_stack.get("cms", ["none"])
    if any(c in cms for c in ["wix", "godaddy", "weebly"]):
        score += 10
        reasons.append(f"On {cms[0]} — likely hitting limits")

    # Broken/slow site = knows there's a problem
    if lead.get("has_website") and (lead.get("site_dead") or
           (0 <= lead.get("pagespeed_score", -1) < 30)):
        score += 15
        reasons.append("Website broken or extremely slow — pain felt daily")

    # Established but zero digital
    if reviews > 30 and not lead.get("has_tracking_pixel") and not lead.get("has_website"):
        score += 15
        reasons.append("Established business with zero digital presence")

    if signals.get("multi_location"):
        score += 10
        reasons.append("Multi-location — has budget")

    if signals.get("has_contact_form"):
        score += 5
        reasons.append("Has contact form — open to inquiries")

    readiness = (
        "HOT — actively investing, clear gaps" if score >= 60
        else "WARM — aware of problems" if score >= 35
        else "COLD — may not be ready"
    )
    return {"intent_score": min(score, 100), "reasons": reasons,
            "readiness": readiness}

# ============================================================================
# Decision-Maker Enrichment (from enrichment.py)
# ============================================================================

def extract_owner_from_html(html: str, soup: BeautifulSoup) -> dict:
    """Find owner/founder name from website content. Returns {name, title, source}."""
    result = {"name": None, "title": None, "source": None}
    # Clean text by removing script/style tags to avoid JS/CSS noise
    soup_copy = BeautifulSoup(str(soup), 'html.parser')
    for tag in soup_copy(['script', 'style']):
        tag.decompose()
    text = soup_copy.get_text(strip=True, separator=' ')

    # 1. Schema.org structured data
    for schema_tag in soup.find_all("script", type="application/ld+json"):
        try:
            data = json.loads(schema_tag.string)
            if isinstance(data, dict):
                for key in ["founder", "author", "employee"]:
                    person = data.get(key)
                    if isinstance(person, dict) and person.get("name"):
                        result["name"] = person["name"]
                        result["title"] = key.title()
                        result["source"] = "schema.org"
                        return result
                    if isinstance(person, list) and person:
                        p = person[0]
                        if isinstance(p, dict) and p.get("name"):
                            result["name"] = p["name"]
                            result["title"] = key.title()
                            result["source"] = "schema.org"
                            return result
        except Exception:
            _log.warning("schema extraction failed", exc_info=True)
            pass

    # 2. Text patterns
    patterns = [
        r'(?:owner|founder|ceo|principal|director|proprietor)[:\s,–—-]*([A-Z][a-z]+ [A-Z][a-z]+)',
        r'([A-Z][a-z]+ [A-Z][a-z]+)[,\s]*(?:owner|founder|ceo|principal|director)',
        r'(?:meet|about)\s+([A-Z][a-z]+ [A-Z][a-z]+)[,\s]*(?:the )?\s*(?:owner|founder)',
        r'(?:Dr\.|Dr)\s+([A-Z][a-z]+ [A-Z][a-z]+)',
    ]
    ignore = {"the owner", "our team", "the founder", "read more",
              "learn more", "click here", "our staff", "our doctors"}
    for pattern in patterns:
        match = re.search(pattern, text)
        if match:
            name = match.group(1).strip()
            if name.lower() not in ignore and len(name) > 4:
                result["name"] = name
                result["title"] = "Owner"
                result["source"] = "page_content"
                return result

    # 3. Meta author tag
    for meta in soup.find_all("meta"):
        prop = (meta.get("property", "") or meta.get("name", "")).lower()
        if "author" in prop:
            name = (meta.get("content") or "").strip()
            if name and len(name.split()) >= 2:
                result["name"] = name
                result["title"] = "Author"
                result["source"] = "meta_tag"
                return result

    return result

# ============================================================================
# Operations Auditor (from ops_auditor.py)
# ============================================================================

TECH_SIGNATURES = {
    "cms": {
        "wordpress": [r'wp-content/', r'wp-includes/'],
        "wix": [r'wix\.com', r'wixsite\.com'],
        "squarespace": [r'squarespace\.com', r'sqsp\.com'],
        "shopify": [r'cdn\.shopify\.com', r'myshopify\.com'],
        "webflow": [r'webflow\.com'],
        "godaddy": [r'godaddy\.com', r'secureserver\.net'],
        "weebly": [r'weebly\.com'],
    },
    "booking": {
        "calendly": [r'calendly\.com'],
        "acuity": [r'acuityscheduling\.com'],
        "booksy": [r'booksy\.com'],
        "fresha": [r'fresha\.com'],
        "mindbody": [r'mindbodyonline\.com', r'healcode\.com'],
        "jane_app": [r'jane\.app', r'janeapp\.com'],
        "setmore": [r'setmore\.com'],
        "square_appts": [r'squareup\.com/appointments'],
    },
    "payments": {
        "stripe": [r'stripe\.com', r'js\.stripe\.com'],
        "square": [r'squareup\.com', r'square\.com'],
        "paypal": [r'paypal\.com', r'paypalobjects\.com'],
        "clover": [r'clover\.com'],
        "toast": [r'toasttab\.com'],
    },
    "chat": {
        "intercom": [r'intercom\.io', r'intercomcdn\.com'],
        "drift": [r'drift\.com', r'js\.driftt\.com'],
        "zendesk": [r'zendesk\.com', r'zdassets\.com'],
        "livechat": [r'livechatinc\.com'],
        "tawk": [r'tawk\.to'],
        "tidio": [r'tidio\.co'],
        "hubspot_chat": [r'js\.hs-scripts\.com'],
    },
    "email_marketing": {
        "mailchimp": [r'mailchimp\.com', r'list-manage\.com'],
        "constant_contact": [r'constantcontact\.com'],
        "klaviyo": [r'klaviyo\.com'],
        "brevo": [r'sendinblue\.com', r'brevo\.com'],
        "activecampaign": [r'activecampaign\.com'],
    },
    "crm": {
        "hubspot": [r'hubspot\.com', r'hs-scripts\.com', r'hbspt\.com'],
        "salesforce": [r'salesforce\.com', r'force\.com'],
        "zoho": [r'zoho\.com'],
    },
    "accessibility": {
        "accessibe": [r'accessibe\.com', r'acsbapp\.com'],
        "userway": [r'userway\.org'],
    },
    "hiring": {
        "greenhouse": [r'greenhouse\.io'],
        "lever": [r'lever\.co'],
        "careers_page": [r'/careers', r'/jobs', r'/hiring', r'/join-us'],
    },
}


def detect_tech_stack(html: str) -> dict:
    """Scan HTML for technology signatures. Returns {category: [tools]}."""
    html_lower = html.lower()
    stack = {}
    for category, tools in TECH_SIGNATURES.items():
        detected = []
        for tool_name, patterns in tools.items():
            for pattern in patterns:
                if re.search(pattern, html_lower):
                    detected.append(tool_name)
                    break
        stack[category] = detected if detected else ["none"]
    return stack


def detect_content_signals(html: str, soup: BeautifulSoup) -> dict:
    """Detect operational signals from page content."""
    html_lower = html.lower()
    signals = {
        "has_ecommerce": bool(re.search(
            r'add.to.cart|buy.now|shop.now|checkout|shopping.bag', html_lower)),
        "has_online_booking": bool(re.search(
            r'book.now|book.online|schedule.appointment|book.a.call|reserve', html_lower)),
        "has_online_menu": bool(re.search(
            r'our.menu|view.menu|food.menu|menu-item', html_lower)),
        "has_online_ordering": bool(re.search(
            r'order.online|order.now|delivery|takeout|place.order', html_lower)),
        "has_gift_cards": bool(re.search(
            r'gift.card|gift.certificate|e-gift', html_lower)),
        "has_loyalty_program": bool(re.search(
            r'loyalty|rewards.program|earn.points', html_lower)),
        "offers_financing": bool(re.search(
            r'financing.available|payment.plan|affirm|klarna|afterpay', html_lower)),
        "multi_location": bool(re.search(
            r'locations|our.offices|find.a.location|branches', html_lower)),
        "has_blog": bool(re.search(r'/blog|/news|/articles', html_lower)),
        "has_testimonials": bool(re.search(
            r'testimonial|what.our.customers.say|client.reviews', html_lower)),
        "has_contact_form": bool(soup.find("form")),
        "copyright_year": None,
        "images_without_alt": 0,
        "total_images": 0,
    }
    year_match = re.search(r'©\s*(\d{4})', html)
    if year_match:
        signals["copyright_year"] = int(year_match.group(1))
    images = soup.find_all("img")
    signals["total_images"] = len(images)
    signals["images_without_alt"] = sum(1 for img in images if not img.get("alt"))
    return signals


def generate_ops_pain_points(tech_stack: dict, signals: dict,
                              niche: str, rating: float,
                              reviews: int) -> list[dict]:
    """Generate operations pain points with revenue impact estimates."""
    pains = []
    niche_lower = (niche or "").lower()

    is_service = any(k in niche_lower for k in [
        "dentist","doctor","clinic","salon","spa","barber","chiropract",
        "physio","vet","lawyer","accountant","plumber","hvac","cleaning",
        "auto","mechanic","gym","fitness","yoga",
    ])
    is_restaurant = any(k in niche_lower for k in [
        "restaurant","pizza","cafe","bakery","bar","grill","sushi",
        "burger","taco","diner","food","catering","bistro",
    ])

    if is_service and "none" in tech_stack.get("booking", []):
        pains.append({
            "pain": "No Online Booking System",
            "impact": "Losing 30-40% of potential bookings",
            "monthly_loss": 2000, "sell_to": "booking_saas",
        })
    if is_restaurant and not signals.get("has_online_ordering"):
        pains.append({
            "pain": "No Online Ordering",
            "impact": "Missing 20-35% of delivery/takeout revenue",
            "monthly_loss": 4000, "sell_to": "ordering_platforms",
        })
    if is_restaurant and not signals.get("has_online_menu"):
        pains.append({
            "pain": "No Online Menu",
            "impact": "62% of diners check menu online before visiting",
            "monthly_loss": 1500, "sell_to": "web_designers",
        })
    if "none" in tech_stack.get("payments", []):
        pains.append({
            "pain": "No Online Payments",
            "impact": "Can't collect deposits or sell gift cards online",
            "monthly_loss": 1000, "sell_to": "payment_processors",
        })
    if "none" in tech_stack.get("crm", []):
        pains.append({
            "pain": "No CRM System",
            "impact": "No systematic follow-up — leads fall through cracks",
            "monthly_loss": 3000, "sell_to": "crm_vendors",
        })
    if "none" in tech_stack.get("email_marketing", []):
        pains.append({
            "pain": "No Email Marketing",
            "impact": "Not nurturing existing customers",
            "monthly_loss": 2000, "sell_to": "email_platforms",
        })
    if "none" in tech_stack.get("chat", []):
        pains.append({
            "pain": "No Live Chat",
            "impact": "Visitors with questions leave instead of converting",
            "monthly_loss": 800, "sell_to": "chat_saas",
        })
    if is_service and not signals.get("has_gift_cards"):
        pains.append({
            "pain": "No Gift Card Program",
            "impact": "Average gift card adds 20-40% extra spend",
            "monthly_loss": 500, "sell_to": "pos_systems",
        })
    hiring = tech_stack.get("hiring", ["none"])
    if hiring != ["none"]:
        pains.append({
            "pain": "Actively Hiring (Growth Signal)",
            "impact": "POSITIVE — growing business likely to invest",
            "monthly_loss": 0, "sell_to": "growth_signal",
        })
    no_alt = signals.get("images_without_alt", 0)
    if "none" in tech_stack.get("accessibility", []) and no_alt > 5:
        pains.append({
            "pain": f"ADA Non-Compliant ({no_alt} images lack alt text)",
            "impact": "Risk of ADA lawsuit ($25K-75K average settlement)",
            "monthly_loss": 0, "sell_to": "accessibility_saas",
        })
    cy = signals.get("copyright_year")
    if cy and cy < 2023:
        pains.append({
            "pain": f"Outdated Website (Copyright {cy})",
            "impact": "Signals neglect — reduces trust",
            "monthly_loss": 500, "sell_to": "web_designers",
        })
    cms = tech_stack.get("cms", ["none"])
    if any(c in cms for c in ["wix", "godaddy", "weebly"]):
        pains.append({
            "pain": f"Using Limited Platform ({cms[0].replace('_',' ').title()})",
            "impact": "Platform limits restricting growth",
            "monthly_loss": 500, "sell_to": "web_developers",
        })

    return pains


def calculate_ops_score(pains: list[dict]) -> int:
    """Higher = more operational gaps = hotter lead for ops services."""
    score = 0
    for p in pains:
        loss = p.get("monthly_loss", 0)
        if loss >= 3000:
            score += 20
        elif loss >= 1500:
            score += 12
        elif loss >= 500:
            score += 8
        elif p.get("sell_to") == "growth_signal":
            score += 15
        else:
            score += 5
    return min(score, 100)

# ============================================================================
# Core Auditor (from auditor.py)
# ============================================================================

FREE_EMAIL_DOMAINS = {
    "gmail.com", "hotmail.com", "yahoo.com", "outlook.com",
    "aol.com", "icloud.com", "live.com", "mail.com",
}
JUNK_EMAIL_PARTS = [
    "wix", "sentry", "example", ".png", ".jpg", "domain",
    "bootstrap", "noreply", "no-reply", "cloudflare",
    "@sentry", "webpack", "localhost",
]

MOBILE_UA = (
    "Mozilla/5.0 (iPhone; CPU iPhone OS 17_0 like Mac OS X) "
    "AppleWebKit/605.1.15 Mobile/15E148 Safari/604.1"
)
DESKTOP_UA = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
    "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36"
)


def _extract_emails(html: str) -> list[str]:
    found = set(re.findall(
        r"[a-zA-Z0-9._%+\-]+@[a-zA-Z0-9.\-]+\.[a-zA-Z]{2,}", html))
    clean = [e for e in found
             if not any(j in e.lower() for j in JUNK_EMAIL_PARTS)
             and len(e) < 60]
    # Prioritize business-looking emails
    priority = ["info@", "contact@", "hello@", "admin@", "support@",
                "sales@", "office@", "enquiries@", "mail@", "team@"]
    for prefix in priority:
        for e in clean:
            if e.lower().startswith(prefix):
                return [e] + [x for x in clean if x != e]
    return clean


def _guess_emails_from_domain(domain: str) -> list[str]:
    """Construct likely contact emails from a website domain."""
    if not domain:
        return []
    # Strip www
    d = domain.lower().replace("www.", "")
    # Skip if it's a platform domain
    platform_domains = [
        "wix.com", "squarespace.com", "wordpress.com", "godaddy.com",
        "weebly.com", "shopify.com", "webflow.io", "carrd.co",
        "google.com", "facebook.com", "instagram.com",
    ]
    if any(d.endswith(pd) for pd in platform_domains):
        return []
    return [f"info@{d}", f"contact@{d}", f"hello@{d}"]


def _extract_email_from_maps(raw: dict) -> str | None:
    """Try to get email from Google Maps/Serper raw data."""
    # Some Serper responses include email directly
    for key in ("email", "emailAddress", "mail"):
        val = raw.get(key, "")
        if val and "@" in val:
            return val
    return None


async def _fetch_hunter_emails(session: aiohttp.ClientSession, domain: str) -> list[str]:
    """Query Hunter.io API for company emails."""
    if not HUNTER_API_KEY or not domain:
        return []
    url = f"https://api.hunter.io/v2/domain-search?domain={domain}&api_key={HUNTER_API_KEY}"
    try:
        async with session.get(url, timeout=10) as resp:
            if resp.status == 200:
                data = await resp.json()
                emails = [e["value"] for e in data.get("data", {}).get("emails", []) if e.get("value")]
                return emails[:5]  # limit
    except Exception:
        pass
    return []


async def _fetch_clearbit_emails(session: aiohttp.ClientSession, domain: str) -> list[str]:
    """Query Clearbit Company API and synthesize probable emails from site data.

    Clearbit's /v2/companies/find returns company profile info (not emails
    directly). We extract people / contact references where available and fall
    back to common-prefix guesses on the canonical domain.
    """
    if not CLEARBIT_API_KEY or not domain:
        return []
    url = f"https://company.clearbit.com/v2/companies/find?domain={domain}"
    headers = {"Authorization": f"Bearer {CLEARBIT_API_KEY}"}
    try:
        async with session.get(url, headers=headers, timeout=10) as resp:
            if resp.status != 200:
                return []
            data = await resp.json()
    except Exception:
        return []

    # Prefer canonical domain (Clearbit normalizes www/alias)
    canonical = (data.get("domain") or domain).lower().strip()
    if not canonical:
        return []
    emails: list[str] = []
    # Direct emails in the payload (rare but possible)
    site = (data.get("site") or {})
    for key in ("emailAddresses", "emails"):
        vals = site.get(key) or []
        for v in vals:
            if isinstance(v, str) and "@" in v:
                emails.append(v)
    # Fallback: construct likely contact emails from the canonical domain
    if not emails:
        for prefix in ("info", "contact", "hello"):
            emails.append(f"{prefix}@{canonical}")
    # Dedup preserving order
    seen = set()
    out = []
    for e in emails:
        el = e.lower()
        if el in seen:
            continue
        seen.add(el)
        out.append(e)
    return out[:5]


def _extract_domain(url: str) -> str:
    """Extract clean domain from URL."""
    if not url:
        return ""
    if not url.startswith(("http://", "https://")):
        url = "https://" + url
    try:
        return urlparse(url).netloc.replace("www.", "").lower()
    except Exception:
        return ""


def _ensure_scheme(url):
    return url if url.startswith(("http://", "https://")) else "https://" + url


def _social_signals(html):
    return {
        "has_facebook": bool(re.search(r'facebook\.com/[A-Za-z0-9]', html)),
        "has_instagram": bool(re.search(r'instagram\.com/[A-Za-z0-9]', html)),
        "has_linkedin": bool(re.search(
            r'linkedin\.com/(company|in)/[A-Za-z0-9]', html)),
    }


def _is_competitor(name):
    low = name.lower()
    return any(k in low for k in COMPETITOR_KEYWORDS)


async def _pagespeed_score(session, url):
    if not PAGESPEED_API_KEY:
        return -1
    try:
        params = {"url": url, "key": PAGESPEED_API_KEY,
                  "strategy": "mobile", "category": "performance"}
        async with session.get(
            "https://www.googleapis.com/pagespeedonline/v5/runPagespeed",
            params=params,
            timeout=aiohttp.ClientTimeout(total=25),
        ) as resp:
            data = await resp.json()
            return int(
                data["lighthouseResult"]["categories"]["performance"]["score"]
                * 100
            )
    except Exception:
        return -1


async def _fetch_website(session, url, timeout_sec=20):
    """
    Fetch website HTML with retry logic.
    Try 1: Mobile UA, 20s timeout
    Try 2: Desktop UA, 25s timeout (some sites block mobile bots)
    Try 3: HTTP instead of HTTPS (some sites have broken SSL)
    Returns (final_url, status_code, html, error_type)
    """
    attempts = [
        (url, MOBILE_UA, timeout_sec),
        (url, DESKTOP_UA, timeout_sec + 5),
    ]
    # If URL is HTTPS, also try HTTP as fallback
    if url.startswith("https://"):
        http_url = "http://" + url[8:]
        attempts.append((http_url, DESKTOP_UA, timeout_sec))

    last_error = None
    for attempt_url, ua, tout in attempts:
        try:
            headers = {"User-Agent": ua}
            timeout = aiohttp.ClientTimeout(total=tout)
            async with session.get(
                attempt_url, headers=headers, timeout=timeout,
                allow_redirects=True, ssl=VERIFY_SSL,
            ) as resp:
                final_url = str(resp.url)
                status = resp.status
                html = await resp.text(errors="replace")
                return final_url, status, html, None
        except asyncio.TimeoutError:
            last_error = "timeout"
        except aiohttp.ClientConnectorError:
            last_error = "connection_refused"
        except Exception as exc:
            last_error = type(exc).__name__

    return None, 0, "", last_error


async def audit_lead(raw: dict, session: aiohttp.ClientSession,
                     skip_competitor_filter: bool = False,
                     skip_if_clean: bool = True) -> dict | None:
    """
    Full audit of a single lead.
    Returns dict ready for DB insertion, or None if filtered out.
    """
    name = raw.get("title", "").strip()
    place_id = raw.get("placeId", "")
    website_raw = raw.get("website", "")
    phone = raw.get("phoneNumber", raw.get("phone", "N/A"))
    rating = float(raw.get("rating") or 0)
    reviews = int(raw.get("userRatingCount") or raw.get("reviewCount") or 0)
    address = raw.get("address", "")
    niche = raw.get("_niche", "")

    if not place_id or not name:
        return None
    if not skip_competitor_filter and _is_competitor(name):
        return None

    pain_points: list[str] = []
    flags = {
        "has_website": False, "has_ssl": False, "is_mobile_friendly": False,
        "has_tracking_pixel": False, "has_facebook": False,
        "has_instagram": False, "has_linkedin": False,
        "site_dead": False, "uses_free_email": False,
        "pagespeed_score": -1,
    }
    emails: list[str] = []
    site_was_timeout = False  # OUR problem, not theirs

    # Ops data
    tech_stack = {}
    content_signals = {}
    ops_pains = []
    owner_info = {"name": None, "title": None, "source": None}

    # ── Try to get email from Maps data first ──
    maps_email = _extract_email_from_maps(raw)

    if not website_raw:
        pain_points.append("No Website")
    else:
        flags["has_website"] = True
        url = _ensure_scheme(website_raw)
        domain = _extract_domain(website_raw)

        final_url, status_code, html, error_type = await _fetch_website(
            session, url)

        if error_type:
            # We couldn't reach the site
            site_was_timeout = True
            if error_type == "timeout":
                pain_points.append("Slow/Protected Website (Timeout)")
            else:
                pain_points.append(f"Site Unreachable ({error_type})")
            # Don't mark as site_dead — it's probably Cloudflare/protection
            # Try to guess emails from domain
            if domain:
                emails = _guess_emails_from_domain(domain)
                # Try external APIs if still no emails
                if not emails:
                    hunter_emails = await _fetch_hunter_emails(session, domain)
                    emails.extend(hunter_emails)
                if not emails:
                    clearbit_emails = await _fetch_clearbit_emails(session, domain)
                    emails.extend(e for e in clearbit_emails if e not in emails)

        elif status_code and status_code >= 400:
            # Actually broken (4xx/5xx)
            pain_points.append(f"Broken Website ({status_code})")
            flags["site_dead"] = True
            if domain:
                emails = _guess_emails_from_domain(domain)

        elif html:
            # Successfully fetched — full audit
            soup = BeautifulSoup(html, "html.parser")
            html_low = html.lower()
            emails = _extract_emails(html)
            # Augment with external APIs
            if domain:
                hunter_emails = await _fetch_hunter_emails(session, domain)
                emails.extend(e for e in hunter_emails if e not in emails)
                if not emails:
                    clearbit_emails = await _fetch_clearbit_emails(session, domain)
                    emails.extend(e for e in clearbit_emails if e not in emails)

            flags["has_ssl"] = final_url.startswith("https://")
            if not flags["has_ssl"]:
                pain_points.append("No SSL (HTTP Only)")

            flags["is_mobile_friendly"] = bool(
                soup.find("meta", attrs={
                    "name": re.compile("viewport", re.I)}))
            if not flags["is_mobile_friendly"]:
                pain_points.append("Not Mobile-Friendly")

            has_pixel = any([
                "fbq(" in html_low, "fbevents.js" in html_low,
                "gtag(" in html_low, "ga(" in html_low,
                "googletagmanager.com" in html_low,
                "_linkedin_partner" in html_low,
                ("tiktok" in html_low and "pixel" in html_low),
            ])
            flags["has_tracking_pixel"] = has_pixel
            if not has_pixel:
                pain_points.append("No Tracking Pixel")

            social = _social_signals(html_low)
            flags.update(social)
            if not any(social.values()):
                pain_points.append("No Social Media Presence")

            flags["pagespeed_score"] = await _pagespeed_score(
                session, final_url)
            if 0 <= flags.get("pagespeed_score", -1) < 50:
                pain_points.append(
                    f"Slow Website (PageSpeed {flags.get('pagespeed_score', -1)}/100)")

            # ── Model B: Operations Audit ──
            tech_stack = detect_tech_stack(html)
            content_signals = detect_content_signals(html, soup)
            ops_pains = generate_ops_pain_points(
                tech_stack, content_signals, niche, rating, reviews)

            # ── Enrichment: Owner name ──
            owner_info = extract_owner_from_html(html, soup)

    # ── Rating & review pain points ──
    if 0 < rating < 4.0:
        pain_points.append(f"Poor Rating ({rating}★)")
    if reviews < 10 and reviews > 0:
        pain_points.append(f"Very Few Reviews ({reviews})")
    elif reviews == 0:
        pain_points.append("No Reviews")

    # ── Resolve best email: HTML > Maps > Domain guess ──
    if maps_email and not emails:
        emails = [maps_email]
    elif maps_email:
        # Add maps email if not already found
        if maps_email.lower() not in [e.lower() for e in emails]:
            emails.append(maps_email)

    best_email = emails[0] if emails else "N/A"

    if best_email != "N/A":
        domain = best_email.split("@")[-1].lower()
        if domain in FREE_EMAIL_DOMAINS:
            pain_points.append(f"Using Free Email ({domain})")
            flags["uses_free_email"] = True

    ideal_service = _pick_ideal_service(pain_points, flags, site_was_timeout)

    # ── Skip if no problems at all ──
    if skip_if_clean and not pain_points and not ops_pains:
        return None

    # ── Calculate scores ──
    score = _calculate_score(
        flags, rating, reviews, pain_points, best_email, site_was_timeout, phone)
    ops_score = calculate_ops_score(ops_pains)

    lead_stub = {
        **flags,
        "has_website": bool(website_raw),
        "review_count": reviews,
        "rating": rating,
    }
    intent = calculate_intent_score(lead_stub, tech_stack, content_signals)

    country = raw.get("_country", "")
    roi = estimate_revenue_impact(niche, pain_points, ops_pains, country)

    return {
        "place_id": place_id, "business_name": name, "phone": phone,
        "email": best_email, "website": website_raw, "rating": rating,
        "review_count": reviews, "address": address,
        "niche": niche, "city": raw.get("_city", ""),
        "country": country,
        "pain_points": json.dumps(pain_points),
        "ideal_service": ideal_service,
        "lead_score": score,
        **flags,
        # Model B
        "ops_score": ops_score,
        "ops_pain_points": json.dumps([
            {"pain": p["pain"], "monthly_loss": p["monthly_loss"]}
            for p in ops_pains]),
        "tech_stack_json": json.dumps(tech_stack),
        "estimated_monthly_loss": roi["total_monthly_loss"],
        # Intent
        "intent_score": intent["intent_score"],
        "intent_reasons": json.dumps(intent["reasons"]),
        # Enrichment
        "decision_maker": owner_info.get("name"),
        "decision_maker_title": owner_info.get("title"),
        "dm_source": owner_info.get("source"),
        # Source
        "source_query": raw.get("_query", ""),
    }


def _pick_ideal_service(pain_points, flags, was_timeout):
    pain_str = " ".join(pain_points).lower()
    if "no website" in pain_str:
        return "Web Design"
    if "broken website" in pain_str or flags.get("site_dead"):
        return "Web Developer"
    if was_timeout:
        return "Web Performance / Hosting"
    if "no ssl" in pain_str:
        return "IT / Security"
    if "not mobile" in pain_str:
        return "Web Designer"
    if "no tracking pixel" in pain_str:
        return "Paid Ads Agency"
    if "poor rating" in pain_str:
        return "Reputation Management"
    if "no social" in pain_str:
        return "Social Media Agency"
    if "slow website" in pain_str:
        return "Web Performance / SEO"
    if "free email" in pain_str:
        return "Branding / IT"
    if "few reviews" in pain_str or "no reviews" in pain_str:
        return "Review Generation"
    return "General Digital Marketing"


def _calculate_score(flags, rating, reviews, pain_points,
                      email, was_timeout, phone=None):
    """
    Score = how actionable is this lead?
    A lead with problems BUT no way to contact them = LOW score.
    A lead with problems AND an email = HIGH score.
    A timeout = OUR failure, not their problem = minimal score.
    """
    score = 0
    has_email = (email and email != "N/A" and "@" in str(email))
    has_phone = bool(phone and phone != "N/A" and len(str(phone).strip()) > 5)

    # ═══ CONTACTABILITY (most important) ═══
    if has_email:
        score += 30  # Base: we can reach them
    elif has_phone:
        score += 15  # Phone-only lead
    else:
        score += 5   # Almost useless without contact

    # ═══ ACTUAL PROBLEMS WE FULLY AUDITED ═══
    if not flags["has_website"]:
        score += 15  # Real gap
    elif flags["site_dead"]:
        score += 18  # Confirmed broken (4xx/5xx)
    elif was_timeout:
        score += 3   # OUR problem, not theirs — barely counts

    if not flags["has_ssl"] and flags["has_website"] and not was_timeout:
        score += 8
    if not flags["is_mobile_friendly"] and flags["has_website"] and not was_timeout:
        score += 10
    if not flags["has_tracking_pixel"] and flags["has_website"] and not was_timeout:
        score += 12
    if flags["uses_free_email"]:
        score += 6

    if (not any([flags["has_facebook"], flags["has_instagram"],
                 flags["has_linkedin"]]) and not was_timeout):
        score += 8

    # ═══ REVIEW / RATING SIGNALS ═══
    if 0 < rating < 4.0:
        score += 8
    if reviews == 0:
        score -= 5  # No reviews = probably not a real/active business
    elif reviews < 10:
        score += 4

    # ═══ PAGESPEED ═══
    ps = flags.get("pagespeed_score", -1)
    if 0 <= ps < 50 and not was_timeout:
        score += 6

    # ═══ PENALTIES ═══
    # Timed-out site with no email = absolutely useless lead
    if was_timeout and not has_email:
        score = max(score - 20, 5)

    # No website, no email, no reviews = garbage
    if not flags["has_website"] and not has_email and not has_phone and reviews == 0:
        score = 5

    return max(min(score, 100), 0)