"""
LeadPro v3 — Multi-Source Lead Generation Orchestrator
Replaces leadgen.py with plugin-based multi-source architecture.
"""
import asyncio
import math
import json
from datetime import datetime
from typing import List, Dict, Set
import aiohttp

from config import (
    SCRAPE_THREADS, AVG_LEADS_PER_QUERY, BATCH_COMMIT_SIZE,
    RATE_LIMIT_PAUSE_SEC,
)
from database import get_existing_place_ids, get_existing_emails, upsert_leads, get_conn
from ai_engine import build_search_queries
from audit import audit_lead

# Import source plugins
from leadsource_base import SourceRegistry, DeduplicationEngine, LeadData
from leadsource_serper import SerperSource
from leadsource_googleplaces import GooglePlacesSource
from leadsource_serperweb import SerperWebSource
from leadsource_yelp import YelpSource

# Phone normalization utilities (copied from leadgen.py for compatibility)
import re

COUNTRY_PHONE_CODES = {
    "united kingdom": "+44", "uk": "+44", "gb": "+44",
    "united states": "+1", "usa": "+1", "us": "+1",
    "netherlands": "+31", "nl": "+31",
    "germany": "+49", "de": "+49",
    "france": "+33", "fr": "+33",
    "spain": "+34", "es": "+34",
    "italy": "+39", "it": "+39",
    "australia": "+61", "au": "+61",
    "canada": "+1", "ca": "+1",
    "ireland": "+353", "ie": "+353",
    "belgium": "+32", "be": "+32",
    "portugal": "+351", "pt": "+351",
    "sweden": "+46", "se": "+46",
    "norway": "+47", "no": "+47",
    "denmark": "+45", "dk": "+45",
    "switzerland": "+41", "ch": "+41",
    "austria": "+43", "at": "+43",
    "poland": "+48", "pl": "+48",
    "new zealand": "+64", "nz": "+64",
    "south africa": "+27", "za": "+27",
    "india": "+91", "in": "+91",
    "brazil": "+55", "br": "+55",
    "mexico": "+52", "mx": "+52",
    "singapore": "+65", "sg": "+65",
    "uae": "+971", "united arab emirates": "+971",
}

def _normalize_phone(raw_phone: str, country: str = "") -> str:
    """Normalize phone number for WhatsApp compatibility."""
    if not raw_phone or raw_phone == "N/A":
        return ""
    
    digits = re.sub(r'[^\d+]', '', raw_phone)
    if not digits or len(digits) < 7:
        return ""
    
    if digits.startswith('+'):
        return digits
    
    country_lower = (country or "").lower().strip()
    code = COUNTRY_PHONE_CODES.get(country_lower, "")
    
    if code and digits.startswith('0'):
        return code + digits[1:]
    elif code:
        return code + digits
    else:
        return digits


class MultiSourceEngine:
    """Main orchestrator for multi-source lead generation."""
    
    def __init__(self):
        self.registry = SourceRegistry()
        self.deduplicator = DeduplicationEngine()
        self._register_sources()
        
    def _register_sources(self):
        """Register all available lead sources."""
        # Register Serper Maps (existing system)
        self.registry.register(SerperSource())
        
        # Register Google Places (alternative maps)
        self.registry.register(GooglePlacesSource())
        
        # Register Serper Web (for e-commerce)
        self.registry.register(SerperWebSource())
        
        # Register Yelp (for local services with reviews)
        self.registry.register(YelpSource())
        
        # Note: More sources can be added here
        # Example: self.registry.register(CrunchbaseSource())
        # Example: self.registry.register(ShopifySource())
    
    async def fetch_from_sources(self, session: aiohttp.ClientSession,
                                query: str, country: str, 
                                target_per_source: int = 10) -> List[LeadData]:
        """
        Fetch leads from multiple sources for a single query.
        Returns deduplicated leads.
        """
        # Get sources that can handle this query
        sources = self.registry.get_sources_for_query(query, country)
        
        if not sources:
            return []
        
        # Fetch from all suitable sources in parallel
        tasks = []
        for source in sources[:3]:  # Limit to top 3 sources to avoid overloading
            task = source.fetch_leads(session, query, country, target_per_source)
            tasks.append(task)
        
        # Wait for all sources
        all_results = await asyncio.gather(*tasks, return_exceptions=True)
        
        # Process results
        all_leads: List[LeadData] = []
        for i, result in enumerate(all_results):
            if isinstance(result, Exception):
                # Source failed - skip
                continue
            
            source_name = sources[i].name if i < len(sources) else "unknown"
            
            for lead in result:
                # Set query context
                lead.query = query
                lead.country = country
                
                # Normalize phone
                if lead.phone:
                    lead.phone = _normalize_phone(lead.phone, country)
                
                # Check if this is a duplicate
                if self.deduplicator.add_lead(lead):
                    all_leads.append(lead)
                # else: duplicate skipped
        
        return all_leads
    
    async def _process_batch(self, session: aiohttp.ClientSession,
                            leads: List[LeadData],
                            existing_ids: Set[str],
                            country: str,
                            query: str) -> List[Dict]:
        """
        Audit a batch of leads concurrently.
        Returns list of lead dicts ready for DB.
        """
        if not leads:
            return []
        
        tasks = []
        for lead in leads:
            # Skip if already in DB (by source_id)
            if lead.source_id and lead.source_id in existing_ids:
                continue
            
            # Convert to auditor input format
            raw = lead.get_auditor_input()
            
            # Add phone normalization context
            if lead.phone:
                raw["_phone_normalized"] = lead.phone
            
            tasks.append(audit_lead(raw, session))
        
        if not tasks:
            return []
        
        results = await asyncio.gather(*tasks, return_exceptions=True)
        
        audited_leads = []
        for r in results:
            if isinstance(r, dict) and r is not None:
                audited_leads.append(r)
        
        return audited_leads


async def run_engine_web(country: str, target: int,
                        queue: asyncio.Queue):
    """
    Main lead generation pipeline with multi-source support.
    Maintains same interface as original leadgen.py for backward compatibility.
    """
    from database import init_db
    init_db()
    
    await queue.put({
        "type": "info",
        "message": f"🌍 Multi-source engine: Target {target} leads in {country}",
    })
    
    # ── 1. Generate queries ──
    await queue.put({"type": "info", "message": "🤖 Generating search queries…"})
    loop = asyncio.get_event_loop()
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
    
    # ── 2. Initialize engine ──
    engine = MultiSourceEngine()
    
    # Track existing leads to avoid duplicates
    existing_ids = get_existing_place_ids()
    seen_emails: Set[str] = get_existing_emails()
    
    all_leads: List[Dict] = []
    total_scraped = 0
    total_saved = 0
    total_skipped = 0
    total_no_contact = 0
    queries_done = 0
    batch_buffer: List[Dict] = []
    
    connector = aiohttp.TCPConnector(
        limit=SCRAPE_THREADS, ssl=False, ttl_dns_cache=300)
    timeout = aiohttp.ClientTimeout(total=30)
    
    async with aiohttp.ClientSession(
        connector=connector, timeout=timeout
    ) as session:
        
        for i, query in enumerate(queries):
            if total_saved >= target:
                break
            
            queries_done = i + 1
            
            await queue.put({
                "type": "progress",
                "message": f"🔍 [{queries_done}/{len(queries)}] {query}",
                "count": total_saved,
            })
            
            # ── Fetch from multiple sources ──
            source_leads = await engine.fetch_from_sources(session, query, country, 15)
            total_scraped += len(source_leads)
            
            if not source_leads:
                await queue.put({
                    "type": "skip",
                    "message": f"   ↳ 0 results from all sources",
                })
                await asyncio.sleep(1)  # Small delay
                continue
            
            # ── Audit batch ──
            audited_leads = await engine._process_batch(
                session, source_leads, existing_ids, country, query)
            
            # ── Process audited leads ──
            for lead in audited_leads:
                # Email deduplication
                email = (lead.get("email") or "").lower().strip()
                if email and email != "n/a" and "@" in email and email in seen_emails:
                    total_skipped += 1
                    continue
                
                if email and email != "n/a" and "@" in email:
                    seen_emails.add(email)
                
                # Track contactability
                has_email = email and email != "n/a" and "@" in email
                has_phone = bool(
                    lead.get("phone") and lead["phone"] != "N/A"
                    and len(lead["phone"]) >= 7)
                
                if not has_email and not has_phone:
                    total_no_contact += 1
                
                # Add contact method flag to pain points
                if not has_email and has_phone:
                    pains = json.loads(lead.get("pain_points", "[]") or "[]")
                    pains.append("📱 Phone Only (No Email)")
                    lead["pain_points"] = json.dumps(pains)
                
                # Add to batch buffer
                batch_buffer.append(lead)
                total_saved += 1
            
            # ── Commit in batches ──
            if len(batch_buffer) >= BATCH_COMMIT_SIZE:
                upsert_leads(batch_buffer)
                batch_buffer = []
            
            # ── Progress report ──
            email_count = sum(
                1 for l in audited_leads
                if l.get("email") and l["email"] != "N/A"
                and "@" in l["email"]
            )
            phone_count = sum(
                1 for l in audited_leads
                if l.get("phone") and l["phone"] != "N/A"
                and len(l["phone"]) >= 7
            )
            
            contact_str = f"📧{email_count} 📱{phone_count}"
            source_str = f"[Sources: {len(source_leads)} leads]"
            
            await queue.put({
                "type": "success",
                "message": (
                    f"   ↳ {len(audited_leads)} audited leads "
                    f"({contact_str}) {source_str} "
                    f"[Total: {total_saved}]"
                ),
                "count": total_saved,
            })
            
            # Small delay between queries
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
    
    # Source usage report
    source_report = "Sources used: "
    for source_name, source in engine.registry.sources.items():
        if source.calls_today > 0:
            source_report += f"{source_name}({source.calls_today}) "
    
    await queue.put({
        "type": "summary",
        "message": (
            f"✅ MULTI-SOURCE DONE — {total_saved} leads saved\n"
            f"   📧 With email: {stats['with_email'] or 0}\n"
            f"   📱 With phone: {stats['with_phone'] or 0}\n"
            f"   🚫 No contact: {stats['no_contact'] or 0}\n"
            f"   📊 Avg score: {stats['avg_score']:.0f}\n"
            f"   💰 Total revenue gap: ${stats['total_loss'] or 0:,}/mo\n"
            f"   🔍 Queries used: {queries_done} | "
            f"Raw results: {total_scraped} | "
            f"Dupes skipped: {total_skipped}\n"
            f"   {source_report}"
        ),
        "count": total_saved,
    })


# ── CLI runner for backward compatibility ──
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
    print(f"\n🚀 LeadPro v3 — Multi-Source Engine — Generating {target} leads in {country}\n")
    asyncio.run(run_engine_cli(country, target))