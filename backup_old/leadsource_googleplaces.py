"""
LeadPro v3 — Google Places API Source
Free tier: 1,000 requests/day, $2/1000 after.
"""
import re
from typing import List, Dict
import aiohttp
from leadsource_base import LeadSource, LeadData
from config import GOOGLE_PLACES_API_KEY


class GooglePlacesSource(LeadSource):
    """Google Places API source (alternative to Serper)."""
    
    def __init__(self):
        super().__init__(name="google_places", priority=90)
        self.max_calls_per_day = 1000  # Free tier limit
    
    async def fetch_leads(self, session: aiohttp.ClientSession, 
                          query: str, country: str, limit: int = 20) -> List[LeadData]:
        """Fetch leads from Google Places API."""
        if not GOOGLE_PLACES_API_KEY:
            return []
        
        self.record_call()
        
        try:
            # First, search for places
            search_url = "https://maps.googleapis.com/maps/api/place/textsearch/json"
            params = {
                "query": query,
                "key": GOOGLE_PLACES_API_KEY,
                "language": "en",
            }
            
            async with session.get(search_url, params=params, timeout=15) as resp:
                if resp.status != 200:
                    return []
                
                data = await resp.json()
                if data.get("status") != "OK":
                    return []
                
                places = data.get("results", [])
                leads = []
                
                # Get details for each place (limited to avoid too many API calls)
                for i, place in enumerate(places[:min(limit, 5)]):  # Limit details calls
                    place_id = place.get("place_id")
                    if place_id:
                        details = await self._get_place_details(session, place_id)
                        if details:
                            lead = self._convert_place_to_lead(place, details, query, country)
                            if lead:
                                leads.append(lead)
                
                return leads
                
        except Exception as e:
            return []
    
    async def _get_place_details(self, session: aiohttp.ClientSession, place_id: str) -> Dict:
        """Get detailed information for a place."""
        try:
            details_url = "https://maps.googleapis.com/maps/api/place/details/json"
            params = {
                "place_id": place_id,
                "key": GOOGLE_PLACES_API_KEY,
                "fields": "name,formatted_address,formatted_phone_number,website,rating,user_ratings_total,types",
            }
            
            async with session.get(details_url, params=params, timeout=10) as resp:
                if resp.status != 200:
                    return None
                
                data = await resp.json()
                if data.get("status") != "OK":
                    return None
                
                return data.get("result", {})
        except:
            return None
    
    def _convert_place_to_lead(self, search_result: Dict, details: Dict, 
                              query: str, country: str) -> LeadData:
        """Convert Google Places data to LeadData."""
        # Extract niche and city from query
        niche, city = self._parse_query(query)
        
        # Use details if available, otherwise search result
        name = details.get("name") or search_result.get("name", "")
        address = details.get("formatted_address") or ""
        phone = details.get("formatted_phone_number") or ""
        website = details.get("website") or ""
        rating = float(details.get("rating") or 0)
        review_count = int(details.get("user_ratings_total") or 0)
        categories = details.get("types") or search_result.get("types") or []
        category = ", ".join(categories[:3]) if categories else ""
        
        # Extract city from address if not from query
        if not city and address:
            # Simple extraction: look for city pattern
            parts = address.split(",")
            if len(parts) > 1:
                city = parts[-2].strip()  # Usually city is second to last
        
        return LeadData(
            source_id=search_result.get("place_id", ""),
            source_name=self.name,
            business_name=name.strip(),
            website=website,
            email="",  # Google Places doesn't provide email
            phone=phone,
            address=address,
            city=city,
            country=country,
            niche=niche,
            category=category,
            rating=rating,
            review_count=review_count,
            raw_data={"search": search_result, "details": details},
            query=query,
        )
    
    def _parse_query(self, query: str) -> tuple[str, str]:
        """Extract niche and city from query."""
        match = re.match(r'^(.+?)\s+in\s+(.+)$', query, re.IGNORECASE)
        if match:
            return match.group(1).strip(), match.group(2).strip()
        return query.strip(), ""
    
    def can_handle_query(self, query: str, country: str) -> bool:
        """Google Places is best for local business searches."""
        query_lower = query.lower()
        
        # Google Places is excellent for local searches
        # It can handle e-commerce queries too, but might not find online-only stores
        return True
    
    def get_cost(self) -> float:
        """Google Places cost: free for first 1k, then $2/1000."""
        if self.calls_today < 1000:
            return 0.0
        return 0.002  # $2/1000 = $0.002 per call