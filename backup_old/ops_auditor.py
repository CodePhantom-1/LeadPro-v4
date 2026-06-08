"""
LeadPro v3 — Operations Auditor (Model B)
Detects tech stack, booking systems, CRM, payment processing,
ADA compliance from the SAME HTML the marketing auditor fetches.
Zero extra API calls.
"""
import re
from bs4 import BeautifulSoup


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