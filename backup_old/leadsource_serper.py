"""
LeadPro v3 — Serper Maps Source
Converts existing Serper Maps integration to plugin.
"""
import re
import json
from typing import List, Dict
import aiohttp
from leadsource_base import LeadSource, LeadData
from config import SERPER_API_KEY


class SerperSource(LeadSource):
    """Serper Maps API source (existing system as plugin)."""
    
    def __init__(self):
        super().__init__(name="serper", priority=100)  # High priority - reliable
        self.max_calls_per_day = 100  # Serper typically has limits
        
    async def fetch_leads(self, session: aiohttp.ClientSession, 
                          query: str, country: str, limit: int = 20) -> List[LeadData]:
        """Fetch leads from Serper Maps API."""
        if not SERPER_API_KEY:
            return []
        
        self.record_call()
        
        try:
            async with session.post(
                "https://google.serper.dev/maps",
                headers={
                    "X-API-KEY": SERPER_API_KEY,
                    "Content-Type": "application/json",
                },
                json={"q": query, "num": limit},
                timeout=aiohttp.ClientTimeout(total=15),
            ) as resp:
                if resp.status == 429:
                    # Rate limited
                    self.rate_limit_remaining = 0
                    return []
                
                data = await resp.json()
                places = data.get("places", [])
                
                leads = []
                for place in places:
                    lead = self._convert_place_to_lead(place, query, country)
                    if lead:
                        leads.append(lead)
                
                return leads
                
        except Exception as e:
            # Log error
            return []
    
    def _convert_place_to_lead(self, place: Dict, query: str, country: str) -> LeadData:
        """Convert Serper Maps place to LeadData."""
        # Extract niche and city from query
        niche, city = self._parse_query(query)
        
        # Extract phone
        phone = ""
        for key in ("phoneNumber", "phone", "telephone", "primaryPhone"):
            val = place.get(key, "")
            if val and len(str(val).strip()) >= 7:
                phone = str(val).strip()
                break
        
        # Extract email
        email = ""
        for key in ("email", "emailAddress", "mail"):
            val = place.get(key, "")
            if val and "@" in str(val):
                email = str(val).strip()
                break
        
        # Get rating and reviews
        rating = float(place.get("rating") or 0)
        review_count = int(place.get("userRatingCount") or 
                          place.get("reviewCount") or 
                          place.get("reviews") or 0)
        
        # Get address
        address = place.get("address", "")
        
        # Determine city from address if not from query
        if not city and address:
            # Simple extraction: take first part of address
            city = address.split(",")[0].strip() if "," in address else ""
        
        return LeadData(
            source_id=place.get("placeId", ""),
            source_name=self.name,
            business_name=place.get("title", "").strip(),
            website=place.get("website", ""),
            email=email,
            phone=phone,
            address=address,
            city=city,
            country=country,
            niche=niche,
            category=place.get("category", ""),
            rating=rating,
            review_count=review_count,
            raw_data=place,
            query=query,
        )
    
    def _parse_query(self, query: str) -> tuple[str, str]:
        """Extract niche and city from query like 'Dentist in London'."""
        match = re.match(r'^(.+?)\s+in\s+(.+)$', query, re.IGNORECASE)
        if match:
            return match.group(1).strip(), match.group(2).strip()
        return query.strip(), ""
    
    def can_handle_query(self, query: str, country: str) -> bool:
        """Serper can handle most queries, but excels at local business searches."""
        # Serper is good for all queries, but especially local ones
        query_lower = query.lower()
        
        # Check if this looks like an e-commerce query
        ecommerce_terms = {"shopify", "ecommerce", "e-commerce", "online store", "online shop"}
        if any(term in query_lower for term in ecommerce_terms):
            # Serper might still work, but other sources might be better
            return True  # But with lower priority
        
        return True
    
    def get_cost(self) -> float:
        """Serper typically charges per request."""
        return 0.01  # Approximate cost in USD