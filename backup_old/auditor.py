"""
LeadPro v3 — Deep Auditor (Fixed)
- 20s timeout + 1 retry with desktop UA
- Distinguishes "timeout" (our problem) from "truly broken" (their problem)
- Extracts email from Google Maps data as fallback
- Constructs likely emails from domain when HTML extraction fails
- Scoring penalizes "no email" and "timeout" correctly
"""
import re
import json
import asyncio
import aiohttp
from urllib.parse import urlparse
from bs4 import BeautifulSoup
from config import PAGESPEED_API_KEY, SCORE_WEIGHTS, COMPETITOR_KEYWORDS
from ops_auditor import (detect_tech_stack, detect_content_signals,
                         generate_ops_pain_points, calculate_ops_score)
from enrichment import extract_owner_from_html
from intent_signals import calculate_intent_score
from roi_calculator import estimate_revenue_impact

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
                allow_redirects=True, ssl=False,
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
        flags, rating, reviews, pain_points, best_email, site_was_timeout)
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
                      email, was_timeout):
    """
    Score = how actionable is this lead?
    A lead with problems BUT no way to contact them = LOW score.
    A lead with problems AND an email = HIGH score.
    A timeout = OUR failure, not their problem = minimal score.
    """
    score = 0
    has_email = (email and email != "N/A" and "@" in str(email))
    has_phone = False  # Phone handled elsewhere

    # ═══ CONTACTABILITY (most important) ═══
    if has_email:
        score += 30  # Base: we can reach them
    else:
        score += 5   # Almost useless without email

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
    if not flags["has_website"] and not has_email and reviews == 0:
        score = 5

    return max(min(score, 100), 0)