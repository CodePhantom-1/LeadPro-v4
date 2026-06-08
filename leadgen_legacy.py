"""
LeadPro v3 — Lead Generation Engine
Serper Maps scraping → deep audit → DB storage.
- Passes ALL Maps fields (email, phone, website) to auditor
- Extracts & normalizes phone numbers for WhatsApp/SMS fallback
- Deduplicates by place_id + email
- Tracks niche/city/country/query for every lead
- Contactability-aware: flags leads by best contact method
"""
import asyncio
import aiohttp
import math
import re
import json
from datetime import datetime
from typing import Optional, List

from config import (
    SERPER_API_KEY, SCRAPE_THREADS, AVG_LEADS_PER_QUERY,
    BATCH_COMMIT_SIZE, RATE_LIMIT_PAUSE_SEC, VERIFY_SSL,
)
from database import get_existing_place_ids, get_existing_emails, upsert_leads, init_db, get_conn
from ai_engine import build_search_queries
from audit import audit_lead
from utils import COUNTRY_PHONE_CODES, _parse_query, _normalize_phone


# ══════════════════════════════════════════════════════
#  PHONE NUMBER UTILITIES
# ══════════════════════════════════════════════════════







def _extract_phone_from_maps(place: dict) -> str:
    """Extract phone from Serper Maps result (tries multiple keys)."""
    for key in ("phoneNumber", "phone", "telephone", "primaryPhone"):
        val = place.get(key, "")
        if val and len(str(val).strip()) >= 7:
            return str(val).strip()
    return ""


def _extract_email_from_maps(place: dict) -> str:
    """Extract email from Serper Maps data if available."""
    for key in ("email", "emailAddress", "mail"):
        val = place.get(key, "")
        if val and "@" in str(val):
            return str(val).strip()
    return ""


# ══════════════════════════════════════════════════════
#  SERPER MAPS API
# ══════════════════════════════════════════════════════

async def _serper_maps_search(session: aiohttp.ClientSession,
                               query: str) -> list[dict]:
    """Hit Serper Maps API. Returns list of place dicts."""
    try:
        async with session.post(
            "https://google.serper.dev/maps",
            headers={
                "X-API-KEY": SERPER_API_KEY,
                "Content-Type": "application/json",
            },
            json={"q": query, "num": 20},
            timeout=aiohttp.ClientTimeout(total=15),
        ) as resp:
            if resp.status == 429:
                return []  # Rate limited
            data = await resp.json()
            return data.get("places", [])
    except Exception:
        return []


# ══════════════════════════════════════════════════════
#  QUERY EXTRACTION (niche + city from query string)
# ══════════════════════════════════════════════════════




# ══════════════════════════════════════════════════════
#  BATCH PROCESSOR
# ══════════════════════════════════════════════════════

async def _process_batch(session: aiohttp.ClientSession,
                          places: list[dict],
                          existing_ids: set,
                          country: str,
                          query: str,
                          exclude_competitors: bool = False,
                          include_clean_leads: bool = False) -> list[dict]:
    """Audit a batch of places concurrently. Returns list of lead dicts."""
    niche, city = _parse_query(query)

    tasks = []
    for place in places:
        pid = place.get("placeId", "")
        if not pid or pid in existing_ids:
            continue
        existing_ids.add(pid)

        # ── Build raw dict with ALL Maps data ──
        phone_raw = _extract_phone_from_maps(place)
        phone_clean = _normalize_phone(phone_raw, country)
        maps_email = _extract_email_from_maps(place)

        raw = {
            # Core fields from Serper
            "title": place.get("title", ""),
            "placeId": pid,
            "website": place.get("website", ""),
            "phoneNumber": phone_raw,
            "rating": place.get("rating", 0),
            "userRatingCount": (
                place.get("userRatingCount")
                or place.get("reviewCount")
                or place.get("reviews", 0)
            ),
            "address": place.get("address", ""),
            "category": place.get("category", ""),
            "cid": place.get("cid", ""),
            # Email from Maps (if available)
            "email": maps_email,
            # Context for auditor
            "_niche": niche,
            "_city": city,
            "_country": country,
            "_query": query,
            "_phone_normalized": phone_clean,
        }
        tasks.append(audit_lead(raw, session,
                                skip_competitor_filter=not exclude_competitors,
                                skip_if_clean=not include_clean_leads))

    if not tasks:
        return []

    results = await asyncio.gather(*tasks, return_exceptions=True)
    leads = []
    for r in results:
        if isinstance(r, dict) and r is not None:
            # Inject normalized phone if auditor didn't find one
            if r.get("phone") in (None, "", "N/A"):
                for place in places:
                    if place.get("placeId") == r.get("place_id"):
                        ph = _normalize_phone(
                            _extract_phone_from_maps(place), country)
                        if ph:
                            r["phone"] = ph
                        break
            leads.append(r)
    return leads


# ══════════════════════════════════════════════════════
#  MAIN ENGINE (Web/SSE)
# ══════════════════════════════════════════════════════

async def run_engine_web(country: str, target: int,
                           queue: asyncio.Queue,
                           industry: Optional[str] = None,
                           business_type: Optional[str] = None,
                           min_lead_score: Optional[int] = None,
                           min_ops_score: Optional[int] = None,
                           min_intent_score: Optional[int] = None,
                           exclude_competitors: bool = False,
                           include_clean_leads: bool = False,
                           tech_stack_filters: Optional[List[str]] = None,
                           source_selection: Optional[List[str]] = None):
    """
    Main lead generation pipeline with SSE progress.
    1. AI generates niche × city queries
    2. Serper Maps API for each query
    3. Audit each result (website, ops, enrichment)
    4. Save to DB
    5. Report progress via queue
    """
    init_db()

    await queue.put({
        "type": "info",
        "message": f"🌍 Target: {target} leads in {country}",
    })

    # ── 1. Generate queries ──
    await queue.put({"type": "info", "message": "🤖 Generating search queries…"})
    loop = asyncio.get_running_loop()
    queries = await loop.run_in_executor(
        None, build_search_queries, country, target)

    if not queries:
        await queue.put({
            "type": "error",
            "message": "Failed to generate queries. Check OpenRouter API key.",
        })
        return

    # Estimate how many queries we need
    needed = math.ceil(target / AVG_LEADS_PER_QUERY * 1.5)  # 1.5x buffer
    queries = queries[:needed]

    await queue.put({
        "type": "info",
        "message": f"📋 {len(queries)} queries generated",
    })

    # ── 2. Scrape + audit ──
    existing_ids = get_existing_place_ids()
    seen_emails: set[str] = get_existing_emails()  # Deduplicate by email across runs
    all_leads: list[dict] = []
    total_scraped = 0
    total_saved = 0
    total_skipped = 0
    total_no_contact = 0
    queries_done = 0
    batch_buffer: list[dict] = []

    connector = aiohttp.TCPConnector(
        limit=SCRAPE_THREADS, ssl=VERIFY_SSL, ttl_dns_cache=300)
    timeout = aiohttp.ClientTimeout(total=30)

    async with aiohttp.ClientSession(
        connector=connector, timeout=timeout
    ) as session:

        # Helper to filter leads based on advanced criteria
        def passes_filters(lead: dict) -> bool:
            # Industry/Niche filter
            if industry:
                lead_niche = (lead.get("niche") or "").lower()
                lead_category = (lead.get("category") or "").lower()
                lead_ideal_service = (lead.get("ideal_service") or "").lower()
                industry_lower = industry.lower()
                if (industry_lower not in lead_niche and 
                    industry_lower not in lead_category and
                    industry_lower not in lead_ideal_service):
                    return False
            
            # Business type filter (simplified - legacy lacks tech stack)
            # Skip business_type filter for legacy
            
            # Score thresholds
            if min_lead_score is not None:
                if lead.get("lead_score", 0) < min_lead_score:
                    return False
            if min_ops_score is not None:
                if lead.get("ops_score", 0) < min_ops_score:
                    return False
            if min_intent_score is not None:
                if lead.get("intent_score", 0) < min_intent_score:
                    return False
            
            # Tech stack filters not supported in legacy
            # source_selection not applicable (only serper maps)
            
            return True
        
        for i, query in enumerate(queries):
            if total_saved >= target:
                break

            queries_done = i + 1

            await queue.put({
                "type": "progress",
                "message": f"🔍 [{queries_done}/{len(queries)}] {query}",
                "count": total_saved,
            })

            # Hit Serper Maps
            places = await _serper_maps_search(session, query)

            if not places:
                await queue.put({
                    "type": "skip",
                    "message": f"   ↳ 0 results",
                })
                # Rate limit backoff
                if queries_done > 3:
                    await asyncio.sleep(2)
                continue

            total_scraped += len(places)

            # Audit batch
            leads = await _process_batch(
                session, places, existing_ids, country, query,
                exclude_competitors, include_clean_leads)

            # Deduplicate by email
            for lead in leads:
                # Apply advanced filters
                if not passes_filters(lead):
                    total_skipped += 1
                    continue
                
                email = (lead.get("email") or "").lower().strip()
                if email and email != "n/a" and email in seen_emails:
                    total_skipped += 1
                    continue
                if email and email != "n/a":
                    seen_emails.add(email)

                # Track contactability
                has_email = (
                    email and email != "n/a" and "@" in email)
                has_phone = bool(
                    lead.get("phone") and lead["phone"] != "N/A"
                    and len(lead["phone"]) >= 7)

                if not has_email and not has_phone:
                    total_no_contact += 1

                # Add contact method flag to pain points for visibility
                if not has_email and has_phone:
                    pains = json.loads(lead.get("pain_points", "[]") or "[]")
                    pains.append("📱 Phone Only (No Email)")
                    lead["pain_points"] = json.dumps(pains)

                batch_buffer.append(lead)
                total_saved += 1

            # Commit in batches
            if len(batch_buffer) >= BATCH_COMMIT_SIZE:
                upsert_leads(batch_buffer)
                batch_buffer = []

            # Progress report for this query
            email_count = sum(
                1 for l in leads
                if l.get("email") and l["email"] != "N/A"
                and "@" in l["email"]
            )
            phone_count = sum(
                1 for l in leads
                if l.get("phone") and l["phone"] != "N/A"
                and len(l["phone"]) >= 7
            )

            contact_str = f"📧{email_count} 📱{phone_count}"

            await queue.put({
                "type": "success",
                "message": (
                    f"   ↳ {len(leads)} leads "
                    f"({contact_str}) "
                    f"[Total: {total_saved}]"
                ),
                "count": total_saved,
            })

            # Small delay between queries to avoid rate limits
            await asyncio.sleep(1)

        # ── Flush remaining buffer ──
        if batch_buffer:
            upsert_leads(batch_buffer)

    # ── 3. Post-processing stats ──
    with get_conn() as conn:
        stats = conn.execute("""
            SELECT
                COUNT(*) as total,
                SUM(CASE WHEN email IS NOT NULL AND email != ''
                    AND email != 'N/A' THEN 1 ELSE 0 END) as with_email,
                SUM(CASE WHEN phone IS NOT NULL AND phone != ''
                    AND phone != 'N/A' AND LENGTH(phone) >= 7
                    THEN 1 ELSE 0 END) as with_phone,
                SUM(CASE WHEN (email IS NULL OR email = '' OR email = 'N/A')
                    AND (phone IS NULL OR phone = '' OR phone = 'N/A'
                         OR LENGTH(phone) < 7)
                    THEN 1 ELSE 0 END) as no_contact,
                AVG(lead_score) as avg_score,
                SUM(estimated_monthly_loss) as total_loss
            FROM leads
            WHERE country = ?
        """, (country,)).fetchone()

    await queue.put({
        "type": "summary",
        "message": (
            f"✅ DONE — {total_saved} leads saved\n"
            f"   📧 With email: {stats['with_email'] or 0}\n"
            f"   📱 With phone: {stats['with_phone'] or 0}\n"
            f"   🚫 No contact: {stats['no_contact'] or 0}\n"
            f"   📊 Avg score: {stats['avg_score']:.0f}\n"
            f"   💰 Total revenue gap: ${stats['total_loss'] or 0:,}/mo\n"
            f"   🔍 Queries used: {queries_done} | "
            f"Maps results: {total_scraped} | "
            f"Dupes skipped: {total_skipped}"
        ),
        "count": total_saved,
    })


# ══════════════════════════════════════════════════════
#  CLI RUNNER (optional)
# ══════════════════════════════════════════════════════

async def run_engine_cli(country: str, target: int):
    """Run from command line with print output."""
    queue = asyncio.Queue()

    async def printer():
        while True:
            msg = await queue.get()
            if msg is None:
                break
            t = msg.get("type", "info")
            m = msg.get("message", "")
            prefix = {
                "info": "ℹ", "success": "✓", "error": "✗",
                "warning": "⚠", "progress": "◈", "skip": "–",
                "summary": "★",
            }.get(t, "·")
            print(f"  {prefix}  {m}")

    printer_task = asyncio.create_task(printer())

    try:
        await run_engine_web(country, target, queue)
    finally:
        await queue.put(None)
        await printer_task


if __name__ == "__main__":
    import sys
    country = sys.argv[1] if len(sys.argv) > 1 else "United Kingdom"
    target = int(sys.argv[2]) if len(sys.argv) > 2 else 100
    print(f"\nLeadPro v4 (legacy engine) — Generating {target} leads in {country}\n")
    asyncio.run(run_engine_cli(country, target))