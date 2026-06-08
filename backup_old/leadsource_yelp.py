"""
LeadPro v3 — Yelp Fusion API Source
Free tier: 500 requests/day.
Good for local service businesses with reviews.
"""
import re
from typing import List, Dict
import aiohttp
from leadsource_base import LeadSource, LeadData
from config import YELP_API_KEY


class YelpSource(LeadSource):
    """Yelp Fusion API source for local businesses."""
    
    def __init__(self):
        super().__init__(name="yelp", priority=85)
        self.max_calls_per_day = 500  # Free tier limit
    
    async def fetch_leads(self, session: aiohttp.ClientSession, 
                          query: str, country: str, limit: int = 20) -> List[LeadData]:
        """Fetch leads from Yelp Fusion API."""
        if not YELP_API_KEY:
            return []
        
        self.record_call()
        
        # Extract location from query or use country
        location = self._extract_location_from_query(query, country)
        
        # Extract search term (niche)
        search_term = self._extract_search_term(query)
        
        try:
            search_url = "https://api.yelp.com/v3/businesses/search"
            headers = {
                "Authorization": f"Bearer {YELP_API_KEY}",
            }
            params = {
                "term": search_term,
                "location": location,
                "limit": min(limit, 20),  # Yelp limit
                "sort_by": "rating",  # Get highest rated first
            }
            
            async with session.get(search_url, headers=headers, 
                                  params=params, timeout=15) as resp:
                if resp.status != 200:
                    return []
                
                data = await resp.json()
                businesses = data.get("businesses", [])
                
                leads = []
                for business in businesses:
                    lead = self._convert_business_to_lead(business, query, country)
                    if lead:
                        leads.append(lead)
                
                return leads
                
        except Exception as e:
            return []
    
    def _extract_location_from_query(self, query: str, country: str) -> str:
        """Extract location from query, fallback to country."""
        # Try to get city from "niche in city" pattern
        match = re.match(r'^(.+?)\s+in\s+(.+)$', query, re.IGNORECASE)
        if match:
            city = match.group(2).strip()
            return f"{city}, {country}"
        
        # Fallback to country capital or major city
        # This is simplistic - in production, you'd want better geocoding
        country_capitals = {
            "united states": "New York, NY",  # Using NYC as default
            "usa": "New York, NY",
            "us": "New York, NY",
            "united kingdom": "London",
            "uk": "London",
            "gb": "London",
            "canada": "Toronto, ON",
            "ca": "Toronto, ON",
            "australia": "Sydney, NSW",
            "au": "Sydney, NSW",
        }
        
        country_lower = country.lower()
        return country_capitals.get(country_lower, country)
    
    def _extract_search_term(self, query: str) -> str:
        """Extract search term (niche) from query."""
        # Remove location part from "niche in location"
        match = re.match(r'^(.+?)\s+in\s+(.+)$', query, re.IGNORECASE)
        if match:
            return match.group(1).strip()
        return query.strip()
    
    def _convert_business_to_lead(self, business: Dict, query: str, 
                                 country: str) -> LeadData:
        """Convert Yelp business to LeadData."""
        # Extract niche and city from query
        niche, city = self._parse_query(query)
        
        # Get business details
        name = business.get("name", "")
        rating = float(business.get("rating") or 0)
        review_count = int(business.get("review_count") or 0)
        phone = business.get("phone", "")
        website = business.get("url", "")  # Yelp page, not business website
        
        # Get actual website if available (Yelp sometimes has it)
        actual_website = ""
        if website and "yelp.com" in website:
            # This is a Yelp page, try to get business website
            # Note: Yelp API v3 doesn't always provide business website
            # We'd need additional calls or parsing
            pass
        
        # Get address
        location = business.get("location", {})
        address_parts = location.get("display_address", [])
        address = ", ".join(address_parts) if address_parts else ""
        
        # Extract city from Yelp data
        yelp_city = location.get("city", "")
        if yelp_city and not city:
            city = yelp_city
        
        # Get categories
        categories = business.get("categories", [])
        category_names = [cat.get("title", "") for cat in categories]
        category = ", ".join(category_names[:3])
        
        return LeadData(
            source_id=business.get("id", ""),
            source_name=self.name,
            business_name=name.strip(),
            website=actual_website,  # Note: may be empty
            email="",  # Yelp doesn't provide email
            phone=phone,
            address=address,
            city=city,
            country=country,
            niche=niche,
            category=category,
            rating=rating,
            review_count=review_count,
            raw_data=business,
            query=query,
        )
    
    def _parse_query(self, query: str) -> tuple[str, str]:
        """Extract niche and city from query."""
        match = re.match(r'^(.+?)\s+in\s+(.+)$', query, re.IGNORECASE)
        if match:
            return match.group(1).strip(), match.group(2).strip()
        return query.strip(), ""
    
    def can_handle_query(self, query: str, country: str) -> bool:
        """Yelp is best for local service business searches."""
        query_lower = query.lower()
        
        # Yelp is great for local services with reviews
        # Not ideal for e-commerce or online-only businesses
        ecommerce_terms = {"shopify", "ecommerce", "e-commerce", "online store", 
                          "online shop", "website"}
        
        if any(term in query_lower for term in ecommerce_terms):
            return False  # Not suitable for e-commerce
        
        # Good for service businesses
        service_terms = {"dentist", "plumber", "roofer", "lawyer", "chiropractor",
                        "hvac", "electrician", "restaurant", "cafe", "salon",
                        "spa", "gym", "contractor", "cleaner", "handyman"}
        
        if any(term in query_lower for term in service_terms):
            return True
        
        # Also handle general local business queries
        return " in " in query_lower  # Needs a location
    
    def get_cost(self) -> float:
        """Yelp cost: free for first 500 calls/day."""
        if self.calls_today < 500:
            return 0.0
        return 0.0  # Yelp free tier hard limit