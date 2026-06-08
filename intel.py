"""
LeadPro v3 — Intelligence Engine (Revised)
Full second-pass enrichment pipeline:
  1. SEO rank tracking (smarter keywords)
  2. Competitor discovery + deep audit
  3. Gap analysis (marketing + ops)
  4. Decision-maker enrichment via Serper
  5. ROI recalculation with competitive context
  6. Auto-generate personalized audit pages
  7. Skip leads with recent intel (< 7 days)
"""
import asyncio
import aiohttp
import json
from datetime import datetime, timedelta, timezone
from urllib.parse import urlparse

from config import SERPER_API_KEY, INTEL_COMPETITOR_COUNT, INTEL_KEYWORD_COUNT, BASE_URL, VERIFY_SSL
from database import get_conn
from audit import audit_lead
from audit import estimate_revenue_impact, get_currency_for_country


# ══════════════════════════════════════════════════════
#  1. KEYWORD GENERATION
# ══════════════════════════════════════════════════════

# Industry-specific keyword templates
NICHE_KEYWORDS = {
    "dentist":      ["dentist", "dental clinic", "teeth whitening", "dental implants", "emergency dentist"],
    "restaurant":   ["restaurant", "best food", "dinner near me", "takeaway", "place to eat"],
    "plumber":      ["plumber", "plumbing repair", "emergency plumber", "boiler repair", "leak repair"],
    "hvac":         ["hvac", "air conditioning repair", "heating repair", "ac installation", "furnace repair"],
    "lawyer":       ["lawyer", "solicitor", "legal advice", "law firm", "attorney"],
    "salon":        ["hair salon", "hairdresser", "haircut", "beauty salon", "nail salon"],
    "chiropractor": ["chiropractor", "back pain treatment", "spinal adjustment", "chiropractic clinic"],
    "vet":          ["vet", "veterinarian", "animal hospital", "pet clinic", "emergency vet"],
    "gym":          ["gym", "fitness center", "personal trainer", "gym membership", "crossfit"],
    "accountant":   ["accountant", "tax advisor", "bookkeeper", "cpa", "accounting firm"],
    "auto":         ["auto repair", "car mechanic", "auto shop", "car service", "mot garage"],
    "med spa":      ["med spa", "botox", "laser treatment", "aesthetic clinic", "skin care clinic"],
    "roofing":      ["roofer", "roof repair", "roofing contractor", "roof replacement", "roof leak repair"],
    "cleaning":     ["cleaning service", "house cleaning", "office cleaning", "carpet cleaning"],
    "pest":         ["pest control", "exterminator", "termite treatment", "rat removal", "bed bug treatment"],
}


def _build_keywords(niche: str, city: str, business_name: str) -> list[str]:
    """Generate industry-specific search keywords."""
    niche_lower = (niche or "").lower()
    city_clean = (city or "").strip()

    # Find matching niche keywords
    matched_terms = None
    for key, terms in NICHE_KEYWORDS.items():
        if key in niche_lower:
            matched_terms = terms
            break

    if matched_terms:
        kws = [f"{t} in {city_clean}" for t in matched_terms[:3]]
        kws.append(f"best {matched_terms[0]} {city_clean}")
        kws.append(f"{matched_terms[0]} near me {city_clean}")
    else:
        base = niche_lower.strip()
        kws = [
            f"{base} in {city_clean}",
            f"{base} {city_clean}",
            f"best {base} {city_clean}",
            f"{base} near me",
            f"{city_clean} {base} reviews",
        ]

    return kws[:INTEL_KEYWORD_COUNT]


# ══════════════════════════════════════════════════════
#  2. SEO RANK CHECKING
# ══════════════════════════════════════════════════════

async def _serper_search(session: aiohttp.ClientSession,
                          query: str, num: int = 30) -> dict:
    """Generic Serper search. Returns raw response dict."""
    try:
        async with session.post(
            "https://google.serper.dev/search",
            headers={"X-API-KEY": SERPER_API_KEY, "Content-Type": "application/json"},
            json={"q": query, "num": num},
            timeout=aiohttp.ClientTimeout(total=15),
        ) as resp:
            return await resp.json()
    except Exception:
        return {}


async def _serper_maps(session: aiohttp.ClientSession,
                        query: str, num: int = 10) -> dict:
    """Serper Maps search."""
    try:
        async with session.post(
            "https://google.serper.dev/maps",
            headers={"X-API-KEY": SERPER_API_KEY, "Content-Type": "application/json"},
            json={"q": query, "num": num},
            timeout=aiohttp.ClientTimeout(total=15),
        ) as resp:
            return await resp.json()
    except Exception:
        return {}


async def check_serp_position(session, keyword: str,
                               target_domain: str) -> dict:
    """Check where target_domain ranks for a keyword."""
    data = await _serper_search(session, keyword, num=50)

    clean = urlparse(
        target_domain if "://" in target_domain else f"https://{target_domain}"
    ).netloc.replace("www.", "").lower()

    result = {"keyword": keyword, "position": None, "url": None,
              "map_pack": False, "featured": False}

    # Check featured snippet
    featured = data.get("answerBox", {})
    if featured:
        fl = (featured.get("link") or "").lower()
        if clean in fl:
            result["position"] = 0
            result["url"] = featured.get("link", "")
            result["featured"] = True
            return result

    # Check organic results
    for i, r in enumerate(data.get("organic", []), 1):
        rd = urlparse(r.get("link", "")).netloc.replace("www.", "").lower()
        if clean == rd:
            result["position"] = i
            result["url"] = r.get("link", "")
            return result

    # Check map pack
    for i, r in enumerate(data.get("places", []), 1):
        ws = (r.get("website", "") or "").lower()
        if clean in ws:
            result["position"] = i
            result["url"] = r.get("website", "")
            result["map_pack"] = True
            return result

    return result


async def rank_check_lead(session, lead_id: int, website: str,
                           niche: str, city: str,
                           business_name: str) -> list[dict]:
    """Check SEO rankings for a lead. Returns results list."""
    keywords = _build_keywords(niche, city, business_name)
    domain = urlparse(
        website if "://" in website else f"https://{website}"
    ).netloc

    if not domain:
        return []

    tasks = [check_serp_position(session, kw, domain) for kw in keywords]
    results = await asyncio.gather(*tasks, return_exceptions=True)

    # Keep only successful dict results — don't blow away history on API failure
    valid = [r for r in results if isinstance(r, dict)]
    if not valid:
        # All requests failed: preserve previous rankings rather than wiping them
        return []

    with get_conn() as conn:
        # Insert fresh rankings first (transaction ensures atomicity)
        for r in valid:
            conn.execute(
                "INSERT INTO seo_rankings (lead_id, keyword, position, serp_url) "
                "VALUES (?,?,?,?)",
                (lead_id, r["keyword"], r.get("position"), r.get("url")),
            )
        # Now that new data is safely stored, clean up superseded old entries
        conn.execute(
            "DELETE FROM seo_rankings WHERE lead_id=? AND id NOT IN "
            "(SELECT id FROM seo_rankings WHERE lead_id=? ORDER BY id DESC LIMIT ?)",
            (lead_id, lead_id, len(valid)))
        positions = [r["position"] for r in valid if r.get("position") is not None]
        best = min(positions) if positions else None
        conn.execute("UPDATE leads SET best_keyword_pos=? WHERE id=?",
                     (best, lead_id))
    return valid


# ══════════════════════════════════════════════════════
#  3. COMPETITOR DISCOVERY
# ══════════════════════════════════════════════════════

async def find_competitors(session, lead_id: int, niche: str,
                             city: str, country: str, source_query: str,
                             exclude_place_id: str,
                             limit: int = None) -> list[dict]:
    """Find and audit competitors via Serper Maps."""
    limit = limit or INTEL_COMPETITOR_COUNT
    data = await _serper_maps(session, f"{niche} in {city}", num=10)

    places = [p for p in data.get("places", [])
              if p.get("placeId") and p.get("placeId") != exclude_place_id]

    competitors = []
    for place in places[:limit]:
        place_with_context = {**place, "_niche": niche, "_city": city,
                              "_country": country, "_query": source_query}
        audited = await audit_lead(place_with_context, session,
                                   skip_competitor_filter=True,
                                   skip_if_clean=False)
        if audited is None:
            audited = {
                "place_id": place.get("placeId", ""),
                "business_name": place.get("title", "Unknown"),
                "website": place.get("website", ""),
                "rating": float(place.get("rating") or 0),
                "review_count": int(place.get("userRatingCount") or 0),
                "has_ssl": True, "is_mobile_friendly": True, "has_tracking_pixel": True,
                "has_facebook": True, "has_instagram": True, "has_linkedin": True,
                "pagespeed_score": 80, "pain_points": "[]",
                "tech_stack_json": "{}",
            }
        competitors.append(audited)

    # Only clear if we actually got fresh competitor data to replace the old set
    if not competitors:
        return []

    with get_conn() as conn:
        # Insert new competitor data first (transaction ensures atomicity)
        for c in competitors:
            conn.execute(
                """INSERT OR IGNORE INTO competitors
                   (lead_id, place_id, name, website, rating, review_count,
                    has_ssl, is_mobile_friendly, has_tracking_pixel,
                    has_facebook, has_instagram, has_linkedin,
                    pagespeed_score, pain_points)
                   VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
                (lead_id, c.get("place_id", ""), c["business_name"],
                 c.get("website", ""), c.get("rating", 0),
                 c.get("review_count", 0), c.get("has_ssl", 0),
                 c.get("is_mobile_friendly", 0), c.get("has_tracking_pixel", 0),
                 c.get("has_facebook", 0), c.get("has_instagram", 0),
                 c.get("has_linkedin", 0), c.get("pagespeed_score", -1),
                 c.get("pain_points", "[]")),
            )
        # Now that new data is safely stored, clean up superseded old entries
        conn.execute(
            "DELETE FROM competitors WHERE lead_id=? AND id NOT IN "
            "(SELECT id FROM competitors WHERE lead_id=? ORDER BY id DESC LIMIT ?)",
            (lead_id, lead_id, len(competitors)))
        # Update lead with competitor stats
        avg_features = []
        for c in competitors:
            s = sum([bool(c.get("has_ssl")), bool(c.get("is_mobile_friendly")),
                     bool(c.get("has_tracking_pixel")),
                     bool(c.get("has_facebook")), bool(c.get("has_instagram"))])
            avg_features.append(s)
        avg = sum(avg_features) / len(avg_features) if avg_features else 0
        conn.execute(
            "UPDATE leads SET competitor_count=?, avg_competitor_score=? WHERE id=?",
            (len(competitors), round(avg, 2), lead_id))

    return competitors


# ══════════════════════════════════════════════════════
#  4. GAP ANALYSIS
# ══════════════════════════════════════════════════════

def gap_analysis(lead: dict, competitors: list[dict]) -> tuple[list, list]:
    """Compare lead against competitors on all flags. Returns (gaps, advantages)."""
    if not competitors:
        return [], []
    flags = [
        ("has_ssl", "SSL Certificate"),
        ("is_mobile_friendly", "Mobile-Friendly Site"),
        ("has_tracking_pixel", "Ad Tracking / Analytics"),
        ("has_facebook", "Facebook Page"),
        ("has_instagram", "Instagram Presence"),
        ("has_linkedin", "LinkedIn Profile"),
    ]
    gaps, advantages = [], []

    for c in competitors:
        cn = c.get("name", c.get("business_name", "?"))
        for key, label in flags:
            if c.get(key) and not lead.get(key):
                gaps.append({"what": label, "competitor": cn, "key": key})
            elif lead.get(key) and not c.get(key):
                advantages.append({"what": label, "over": cn, "key": key})

        # Speed comparison
        c_speed = c.get("pagespeed_score", -1)
        l_speed = lead.get("pagespeed_score", -1)
        if c_speed >= 0 and l_speed >= 0 and c_speed > l_speed + 15:
            gaps.append({
                "what": f"Faster Website ({c_speed} vs your {l_speed})",
                "competitor": cn, "key": "speed",
            })

        # Rating comparison
        c_rating = c.get("rating", 0)
        l_rating = lead.get("rating", 0)
        if c_rating > 0 and l_rating > 0 and c_rating >= l_rating + 0.5:
            gaps.append({
                "what": f"Better Rating ({c_rating}★ vs your {l_rating}★)",
                "competitor": cn, "key": "rating",
            })

        # Review count comparison
        c_reviews = c.get("review_count", 0)
        l_reviews = lead.get("review_count", 0)
        if c_reviews > l_reviews * 2 and c_reviews > 20:
            gaps.append({
                "what": f"More Reviews ({c_reviews} vs your {l_reviews})",
                "competitor": cn, "key": "reviews",
            })

    # Deduplicate gaps by key
    seen = set()
    unique_gaps = []
    for g in gaps:
        if g["key"] not in seen:
            seen.add(g["key"])
            unique_gaps.append(g)

    return unique_gaps, advantages


# ══════════════════════════════════════════════════════
#  5. DECISION-MAKER ENRICHMENT VIA SERPER
# ══════════════════════════════════════════════════════

async def enrich_decision_maker(session, lead: dict) -> dict | None:
    """Search Google for the business owner's name."""
    name = lead.get("business_name", "")
    city = lead.get("city", "")

    if not name or not SERPER_API_KEY:
        return None

    import re

    for suffix in ["owner", "founder", "CEO"]:
        data = await _serper_search(session, f'"{name}" {city} {suffix}', num=5)

        # Check knowledge graph
        kg = data.get("knowledgeGraph", {})
        if kg.get("description"):
            desc = kg["description"]
            match = re.search(
                r'(?:by|owner|founder|ceo)[:\s]+([A-Z][a-z]+ [A-Z][a-z]+)', desc)
            if match:
                dm_name = match.group(1).strip()
                _save_dm(lead["id"], dm_name, suffix.title(), "serper_kg")
                return {"name": dm_name, "title": suffix.title(),
                        "source": "serper_kg"}

        # Check organic results for LinkedIn
        for org in data.get("organic", []):
            link = org.get("link", "")
            if "linkedin.com/in/" in link:
                title_text = org.get("title", "")
                if "-" in title_text:
                    person = title_text.split("-")[0].strip()
                    if len(person.split()) >= 2 and len(person) < 40:
                        _save_dm(lead["id"], person, suffix.title(),
                                 "serper_linkedin", link)
                        return {"name": person, "title": suffix.title(),
                                "source": "serper_linkedin", "linkedin": link}

        # Check snippets
        for org in data.get("organic", []):
            snippet = org.get("snippet", "")
            match = re.search(
                r'(?:owner|founder|ceo|proprietor)[:\s,]*([A-Z][a-z]+ [A-Z][a-z]+)',
                snippet)
            if match:
                dm_name = match.group(1).strip()
                if dm_name.lower() not in ("read more", "learn more", "click here"):
                    _save_dm(lead["id"], dm_name, suffix.title(), "serper_snippet")
                    return {"name": dm_name, "title": suffix.title(),
                            "source": "serper_snippet"}

    return None


def _save_dm(lead_id, name, title, source, linkedin=None):
    """Save decision-maker to database."""
    with get_conn() as conn:
        conn.execute(
            "UPDATE leads SET decision_maker=?, decision_maker_title=?, "
            "dm_source=?, dm_linkedin=? WHERE id=?",
            (name, title, source, linkedin, lead_id))


# ══════════════════════════════════════════════════════
#  6. AUDIT PAGE GENERATION
# ══════════════════════════════════════════════════════

def _generate_audit_page_for_lead(lead_id: int, lead: dict,
                                    seo: list, competitors: list,
                                    roi: dict):
    """Generate audit page. Import here to avoid circular deps."""
    try:
        from audit_pages import generate_audit_page
        return generate_audit_page(lead_id, lead, seo, competitors, roi)
    except Exception:
        return None


# ══════════════════════════════════════════════════════
#  7. FULL PIPELINE FOR ONE LEAD
# ══════════════════════════════════════════════════════

async def run_intel_for_lead(session, lead: dict,
                              skip_dm: bool = False) -> dict:
    """Run complete intelligence pipeline for one lead."""
    lid = lead["id"]
    website = lead.get("website", "")
    niche = lead.get("niche", "")
    city = lead.get("city", "")
    country = lead.get("country", "")
    source_query = lead.get("source_query", "")
    bname = lead.get("business_name", "")

    result = {
        "lead_id": lid,
        "seo": [],
        "competitors": [],
        "gaps": [],
        "advantages": [],
        "dm_found": False,
        "audit_page": None,
        "roi": {},
    }

    # ── 1. SEO Rankings ──
    if website:
        result["seo"] = await rank_check_lead(
            session, lid, website, niche, city, bname)

    # ── 2. Competitors ──
    if niche and city:
        result["competitors"] = await find_competitors(
            session, lid, niche, city, country, source_query, lead.get("place_id", ""))

    # ── 3. Gap Analysis ──
    result["gaps"], result["advantages"] = gap_analysis(
        lead, result["competitors"])

    # ── 4. Decision-Maker Enrichment ──
    if not skip_dm and not lead.get("decision_maker"):
        dm = await enrich_decision_maker(session, lead)
        if dm:
            result["dm_found"] = True
            lead["decision_maker"] = dm["name"]

    # ── 5. ROI Recalculation ──
    try:
        pains = json.loads(lead.get("pain_points") or "[]")
    except (json.JSONDecodeError, TypeError):
        pains = []
    try:
        ops_pains = json.loads(lead.get("ops_pain_points") or "[]")
    except (json.JSONDecodeError, TypeError):
        ops_pains = []

    # Add competitive gap pains
    gap_pains = []
    GAP_KEY_TO_PAIN = {
        "has_ssl": "no ssl",
        "is_mobile_friendly": "not mobile-friendly",
        "has_tracking_pixel": "no tracking pixel",
        "has_facebook": "no social media",
        "has_instagram": "no social media",
        "has_linkedin": "no social media",
        "speed": "slow website",
        "rating": "poor rating",
        "reviews": "very few reviews",
    }
    for g in result["gaps"]:
        pain = GAP_KEY_TO_PAIN.get(g["key"])
        if pain:
            gap_pains.append(pain)

    all_pains = pains + gap_pains
    roi = estimate_revenue_impact(
        niche, all_pains, ops_pains, lead.get("country", ""))
    result["roi"] = roi

    # Update lead with new loss estimate
    with get_conn() as conn:
        conn.execute(
            "UPDATE leads SET estimated_monthly_loss=?, last_updated=datetime('now') WHERE id=?",
            (roi["total_monthly_loss"], lid))

    # ── 6. Generate Audit Page ──
    # Re-read lead to get latest data
    with get_conn() as conn:
        fresh = conn.execute("SELECT * FROM leads WHERE id=?", (lid,)).fetchone()
        if fresh:
            lead = dict(fresh)
        seo_rows = [dict(r) for r in conn.execute(
            "SELECT * FROM seo_rankings WHERE lead_id=? ORDER BY checked_at DESC LIMIT 10",
            (lid,)).fetchall()]
        comp_rows = [dict(r) for r in conn.execute(
            "SELECT * FROM competitors WHERE lead_id=?",
            (lid,)).fetchall()]

    audit_path = _generate_audit_page_for_lead(
        lid, lead, seo_rows, comp_rows, roi)
    result["audit_page"] = audit_path

    return result


# ══════════════════════════════════════════════════════
#  8. BATCH RUNNER
# ══════════════════════════════════════════════════════

def _has_recent_intel(lead_id: int, days: int = 7) -> bool:
    """Check if lead already has intel less than N days old."""
    cutoff = (datetime.now(timezone.utc) - timedelta(days=days)).isoformat()
    with get_conn() as conn:
        row = conn.execute(
            "SELECT COUNT(*) FROM seo_rankings WHERE lead_id=? AND checked_at>?",
            (lead_id, cutoff)).fetchone()
    return row[0] > 0


async def run_intel_batch(lead_ids: list[int] | None = None,
                            min_score: int = 40,
                            country: str | None = None,
                            service: str | None = None,
                            skip_recent: bool = True,
                            queue: asyncio.Queue = None):
    """Run intelligence on a batch of leads with SSE progress."""

    async def log(t, m, **kw):
        if queue:
            await queue.put({"type": t, "message": m, **kw})

    # Fetch leads
    with get_conn() as conn:
        if lead_ids:
            ph = ",".join("?" * len(lead_ids))
            leads = conn.execute(
                f"SELECT * FROM leads WHERE id IN ({ph})", lead_ids
            ).fetchall()
        else:
            query = "SELECT * FROM leads WHERE lead_score >= ?"
            params = [min_score]
            if country:
                query += " AND country = ?"
                params.append(country)
            if service:
                query += " AND ideal_service = ?"
                params.append(service)
            query += " AND email IS NOT NULL AND email != '' AND email != 'N/A' ORDER BY lead_score DESC LIMIT 50"
            leads = conn.execute(query, params).fetchall()

    leads = [dict(l) for l in leads]

    # Filter out recent
    if skip_recent:
        before = len(leads)
        leads = [l for l in leads if not _has_recent_intel(l["id"])]
        skipped = before - len(leads)
        if skipped:
            await log("info", f"Skipped {skipped} leads with recent intel (< 7 days)")

    if not leads:
        await log("warning", "No leads to process.")
        await log("done", "Done")
        return

    await log("info", f"Running intel on {len(leads)} leads")

    # Counters
    total = len(leads)
    done = 0
    total_kw = 0
    total_comp = 0
    total_gaps = 0
    total_dm = 0
    total_pages = 0

    connector = aiohttp.TCPConnector(limit=8, ssl=VERIFY_SSL)
    async with aiohttp.ClientSession(connector=connector) as session:
        for i, lead in enumerate(leads):
            done = i + 1
            pct = round(done / total * 100)
            await log("generating",
                      f"[{done}/{total}] ({pct}%) {lead['business_name']}…",
                      progress=pct, count=done, total=total)

            try:
                intel = await run_intel_for_lead(session, lead)

                kw_count = len(intel["seo"])
                comp_count = len(intel["competitors"])
                gap_count = len(intel["gaps"])
                ranked = sum(1 for r in intel["seo"]
                             if r.get("position") is not None)
                dm_label = ""
                if intel.get("dm_found"):
                    dm_label = " | Owner found"
                    total_dm += 1
                page_label = ""
                if intel.get("audit_page"):
                    page_label = " | Audit page"
                    total_pages += 1

                loss = intel.get("roi", {}).get("total_monthly_loss", 0)
                cs = get_currency_for_country(lead.get("country","")).get("symbol", "$")

                total_kw += kw_count
                total_comp += comp_count
                total_gaps += gap_count

                await log("success",
                          f"[OK] {lead['business_name']}: "
                          f"{kw_count}kw ({ranked} ranking), "
                          f"{comp_count} competitors, "
                          f"{gap_count} gaps, "
                          f"{cs}{loss:,}/mo loss"
                          f"{dm_label}{page_label}",
                          progress=pct)

            except Exception as e:
                await log("error", f"[FAIL] {lead['business_name']}: {e}")

            # Small delay between leads to avoid rate limits
            if done < total:
                await asyncio.sleep(1)

    # Summary
    await log("summary",
              f"Intelligence complete: {total} leads | "
              f"{total_kw} keywords checked | "
              f"{total_comp} competitors audited | "
              f"{total_gaps} gaps found | "
              f"{total_dm} owners discovered | "
              f"{total_pages} audit pages generated")
    await log("done", "Done")