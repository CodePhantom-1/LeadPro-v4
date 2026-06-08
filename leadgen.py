"""
LeadPro v3 — Consolidated Lead Generation Engine
Combines multi-source orchestrator with all lead source plugins.
Use config flag USE_LEGACY_LEADGEN to fall back to legacy single-source engine.
"""
import asyncio
import math
import json
import re
import hashlib
from datetime import datetime
from typing import List, Dict, Set, Optional
from abc import ABC, abstractmethod
from dataclasses import dataclass
import aiohttp

from config import (
    SCRAPE_THREADS, AVG_LEADS_PER_QUERY, BATCH_COMMIT_SIZE,
    RATE_LIMIT_PAUSE_SEC, SERPER_API_KEY, GOOGLE_PLACES_API_KEY,
    YELP_API_KEY, VERIFY_SSL
)
from database import get_existing_place_ids, get_existing_emails, upsert_leads, get_conn, init_db
from ai_engine import build_search_queries
from audit import audit_lead, get_currency_for_country
from utils import COUNTRY_PHONE_CODES, _parse_query, _normalize_phone

# ============================================================================
# Phone normalization utilities (from leadgen_orchestrator.py)
# ============================================================================



# ============================================================================
# Lead Data Structure (from leadsource_base.py)
# ============================================================================

@dataclass
class LeadData:
    """Standardized lead data structure across all sources."""
    # Core identification
    source_id: str  # Unique ID from source (e.g., place_id for Google Maps)
    source_name: str  # Name of the source plugin
    business_name: str
    website: str
    
    # Contact information
    email: str = ""
    phone: str = ""
    
    # Business details
    address: str = ""
    city: str = ""
    country: str = ""
    niche: str = ""
    category: str = ""
    
    # Ratings/reviews
    rating: float = 0.0
    review_count: int = 0
    
    # Source-specific metadata
    raw_data: Dict = None  # Original data from source
    
    # Context for auditing
    query: str = ""  # Original search query
    
    def __post_init__(self):
        if self.raw_data is None:
            self.raw_data = {}
    
    def get_auditor_input(self) -> Dict:
        """Convert to format expected by auditor.audit_lead()"""
        # Map our fields to what auditor expects
        return {
            "title": self.business_name,
            "placeId": self.source_id or f"{self.source_name}_{hashlib.md5(self.website.encode()).hexdigest()[:10]}",
            "website": self.website,
            "phoneNumber": self.phone,
            "email": self.email,  # Will be used if provided
            "rating": self.rating,
            "userRatingCount": self.review_count,
            "address": self.address,
            "category": self.category,
            # Context fields for auditor
            "_niche": self.niche,
            "_city": self.city,
            "_country": self.country,
            "_query": self.query,
            # Source tracking
            "_source": self.source_name,
        }
    
    @property
    def is_contactable(self) -> bool:
        """Check if lead has at least one contact method."""
        has_email = self.email and "@" in self.email and self.email.lower() != "n/a"
        has_phone = self.phone and len(self.phone.strip()) >= 7 and self.phone.lower() != "n/a"
        return has_email or has_phone
    
    @property
    def domain(self) -> Optional[str]:
        """Extract domain from website."""
        if not self.website:
            return None
        # Simple domain extraction
        if "://" in self.website:
            from urllib.parse import urlparse
            parsed = urlparse(self.website)
            return parsed.netloc
        return self.website

# ============================================================================
# Lead Source Base Class (from leadsource_base.py)
# ============================================================================

class LeadSource(ABC):
    """Abstract base class for all lead sources."""
    
    def __init__(self, name: str, priority: int = 50):
        self.name = name
        self.priority = priority  # Higher priority = used first
        self.rate_limit_remaining = 1000  # Default
        self.last_call_time = None
        self.calls_today = 0
        self.max_calls_per_day = 1000  # Default limit
    
    @abstractmethod
    async def fetch_leads(self, session: aiohttp.ClientSession, 
                          query: str, country: str, limit: int = 20) -> List[LeadData]:
        """Fetch leads from this source. Must be implemented by subclass."""
        pass
    
    def can_handle_query(self, query: str, country: str) -> bool:
        """
        Determine if this source can handle the given query.
        Override in subclasses for intelligent routing.
        """
        return True
    
    def get_cost(self) -> float:
        """Estimated cost per call in USD. Override for paid sources."""
        return 0.0
    
    def should_use(self) -> bool:
        """Check if source should be used based on rate limits and costs."""
        if self.calls_today >= self.max_calls_per_day:
            return False
        # Add cooldown logic if needed
        return True
    
    def record_call(self):
        """Record API call for rate limiting."""
        self.calls_today += 1
        self.last_call_time = datetime.now()
    
    def reset_daily_counts(self):
        """Reset daily call counts (call this daily)."""
        self.calls_today = 0

# ============================================================================
# Source Registry & Deduplication (from leadsource_base.py)
# ============================================================================

class SourceRegistry:
    """Registry for managing lead sources."""
    
    def __init__(self):
        self.sources: Dict[str, LeadSource] = {}
        self.enabled_sources: Set[str] = set()
    
    def register(self, source: LeadSource):
        """Register a lead source."""
        self.sources[source.name] = source
        self.enabled_sources.add(source.name)
    
    def unregister(self, source_name: str):
        """Unregister a lead source."""
        if source_name in self.sources:
            del self.sources[source_name]
            self.enabled_sources.discard(source_name)
    
    def get_source(self, source_name: str) -> Optional[LeadSource]:
        """Get a source by name."""
        return self.sources.get(source_name)
    
    def get_sources_for_query(self, query: str, country: str) -> List[LeadSource]:
        """
        Get sources that can handle a query, sorted by priority.
        """
        suitable = []
        for name in self.enabled_sources:
            source = self.sources[name]
            if source.should_use() and source.can_handle_query(query, country):
                suitable.append(source)
        
        # Sort by priority (highest first), then by cost (lowest first)
        suitable.sort(key=lambda s: (-s.priority, s.get_cost()))
        return suitable
    
    def reset_daily_counts(self):
        """Reset daily call counts for all sources."""
        for source in self.sources.values():
            source.reset_daily_counts()


class DeduplicationEngine:
    """Deduplicate leads across multiple sources."""
    
    def __init__(self):
        self.seen_domains: Set[str] = set()
        self.seen_emails: Set[str] = set()
        self.seen_phones: Set[str] = set()
    
    def add_lead(self, lead: LeadData) -> bool:
        """
        Add a lead to deduplication tracking.
        Returns True if lead is new (not a duplicate).
        """
        domain = lead.domain
        email_key = (lead.email or "").lower().strip()
        if email_key == "n/a":
            email_key = ""
        phone = (lead.phone or "").strip()
        if phone == "n/a":
            phone = ""
        phone_digits = "".join(c for c in phone if c.isdigit())
        if len(phone_digits) >= 10:
            phone_digits = phone_digits[-10:]

        if (domain and domain in self.seen_domains) or \
           (email_key and "@" in email_key and email_key in self.seen_emails) or \
           (len(phone_digits) >= 10 and phone_digits in self.seen_phones):
            return False

        if domain:
            self.seen_domains.add(domain)
        if email_key and "@" in email_key:
            self.seen_emails.add(email_key)
        if len(phone_digits) >= 10:
            self.seen_phones.add(phone_digits)
        return True
    
    def clear(self):
        """Clear all seen data (e.g., at start of new session)."""
        self.seen_domains.clear()
        self.seen_emails.clear()
        self.seen_phones.clear()

# ============================================================================
# Individual Lead Source Implementations
# ============================================================================

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
                json={"q": query, "location": country, "num": limit},
                timeout=aiohttp.ClientTimeout(total=15),
            ) as resp:
                if resp.status == 429:
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
            return []
    
    def _convert_place_to_lead(self, place: Dict, query: str, country: str) -> LeadData:
        """Convert Serper Maps place to LeadData."""
        # Extract niche and city from query
        niche, city = self._parse_query(query)
        
        # Get email from place data
        email = place.get("email", "")
        
        # Extract phone
        phone = place.get("phone", "")
        if phone:
            phone = _normalize_phone(phone, country)
        
        # Prefer city from query parse; fall back to first address segment
        address = place.get("address", "") or ""
        fallback_city = address.split(",")[0].strip() if address else ""
        effective_city = city or fallback_city

        return LeadData(
            source_id=place.get("placeId", ""),
            source_name=self.name,
            business_name=place.get("title", "").strip(),
            website=place.get("website", ""),
            email=email,
            phone=phone,
            address=address,
            city=effective_city,
            country=country,
            niche=niche,
            category=place.get("category", ""),
            rating=float(place.get("rating") or 0),
            review_count=int(place.get("ratingCount") or place.get("userRatingCount") or 0),
            raw_data=place,
            query=query,
        )
    
    def _parse_query(self, query: str) -> tuple[str, str]:
        """Extract niche and city from query."""
        match = re.match(r'^(.+?)\s+in\s+(.+)$', query, re.IGNORECASE)
        if match:
            return match.group(1).strip(), match.group(2).strip()
        return query.strip(), ""
    
    def can_handle_query(self, query: str, country: str) -> bool:
        """Serper is good for all queries, but especially local ones."""
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


class SerperWebSource(LeadSource):
    """Serper Web Search API for e-commerce/DTC discovery."""
    
    def __init__(self):
        super().__init__(name="serper_web", priority=80)
        self.max_calls_per_day = 100
        
    async def fetch_leads(self, session: aiohttp.ClientSession, 
                          query: str, country: str, limit: int = 20) -> List[LeadData]:
        """Fetch leads from Serper Web Search API."""
        if not SERPER_API_KEY:
            return []
        
        self.record_call()
        
        # Enhance query for e-commerce search
        enhanced_query = self._enhance_query_for_ecommerce(query)
        
        try:
            async with session.post(
                "https://google.serper.dev/search",
                headers={
                    "X-API-KEY": SERPER_API_KEY,
                    "Content-Type": "application/json",
                },
                json={"q": enhanced_query, "num": limit},
                timeout=aiohttp.ClientTimeout(total=15),
            ) as resp:
                if resp.status == 429:
                    return []
                
                data = await resp.json()
                organic = data.get("organic", [])
                
                leads = []
                for result in organic:
                    lead = self._convert_web_result_to_lead(result, query, country)
                    if lead:
                        leads.append(lead)
                
                return leads
                
        except Exception as e:
            return []
    
    def _enhance_query_for_ecommerce(self, query: str) -> str:
        """Add e-commerce context to query."""
        query_lower = query.lower()
        
        # Check if already has e-commerce terms
        ecommerce_terms = {"shopify", "ecommerce", "e-commerce", "online store", 
                          "online shop", "website", "store"}
        
        has_ecommerce_term = any(term in query_lower for term in ecommerce_terms)
        
        if has_ecommerce_term:
            return query
        
        # Add e-commerce context
        # Parse "niche in location"
        match = re.match(r'^(.+?)\s+in\s+(.+)$', query, re.IGNORECASE)
        if match:
            niche, location = match.groups()
            return f'"{niche}" online store "{location}" website'
        
        # Just add online store context
        return f'{query} online store website'
    
    def _convert_web_result_to_lead(self, result: Dict, query: str, 
                                   country: str) -> LeadData:
        """Convert web search result to LeadData."""
        title = result.get("title") or ""
        snippet = result.get("snippet") or ""
        url = result.get("link") or ""
        
        # Extract business name from title (remove common suffixes)
        business_name = title
        if " - " in title:
            business_name = title.split(" - ")[0]
        elif " | " in title:
            business_name = title.split(" | ")[0]
        
        # Clean up business name
        business_name = re.sub(r'^https?://', '', business_name)
        business_name = re.sub(r'^www\.', '', business_name)
        business_name = business_name.split('/')[0]
        business_name = business_name.replace('...', '').strip()
        
        # Try to extract domain from URL
        domain = ""
        if url:
            # Simple domain extraction
            if "://" in url:
                from urllib.parse import urlparse
                parsed = urlparse(url)
                domain = parsed.netloc
            else:
                domain = url.split('/')[0]
        
        # Check if this looks like an e-commerce site
        # Look for Shopify indicators
        is_shopify = False
        if domain:
            is_shopify = ".myshopify.com" in domain.lower()
        
        # Look for e-commerce indicators in snippet
        ecommerce_indicators = {"buy", "shop", "cart", "checkout", "product", 
                               "price", "$", "£", "€", "sale", "store"}
        snippet_lower = snippet.lower()
        has_ecommerce_indicators = any(indicator in snippet_lower 
                                      for indicator in ecommerce_indicators)
        
        # Extract niche and city from query
        niche, city = self._parse_query(query)
        
        # Determine if this is likely an e-commerce business
        is_ecommerce = is_shopify or has_ecommerce_indicators or "shop" in query.lower()
        
        # Set appropriate niche if e-commerce
        if is_ecommerce and niche:
            niche = f"E-commerce: {niche}"
        
        return LeadData(
            source_id=url or f"web_{hashlib.md5(url.encode()).hexdigest()[:10]}",
            source_name=self.name,
            business_name=business_name[:100],  # Limit length
            website=url,
            email="",  # Web search doesn't provide email
            phone="",  # Web search doesn't provide phone
            address="",
            city=city,
            country=country,
            niche=niche,
            category="E-commerce" if is_ecommerce else "Website",
            rating=0.0,
            review_count=0,
            raw_data=result,
            query=query,
        )
    
    def _parse_query(self, query: str) -> tuple[str, str]:
        """Extract niche and city from query."""
        match = re.match(r'^(.+?)\s+in\s+(.+)$', query, re.IGNORECASE)
        if match:
            return match.group(1).strip(), match.group(2).strip()
        return query.strip(), ""
    
    def can_handle_query(self, query: str, country: str) -> bool:
        """Serper Web is best for e-commerce and online business searches."""
        query_lower = query.lower()
        
        # Check if this looks like an e-commerce query
        ecommerce_terms = {"shopify", "ecommerce", "e-commerce", "online store", 
                          "online shop", "website", "web", "online"}
        
        if any(term in query_lower for term in ecommerce_terms):
            return True
        
        # Also handle queries that might be for online businesses
        # For example, "custom t-shirts" is likely e-commerce
        online_product_terms = {"t-shirt", "tshirt", "print", "custom", "personalized",
                               "merch", "apparel", "clothing", "jewelry", "accessories"}
        
        if any(term in query_lower for term in online_product_terms):
            return True
        
        # For mixed queries, let other sources handle them first
        return False  # Lower priority for general queries
    
    def get_cost(self) -> float:
        """Serper web search cost."""
        return 0.01


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
        
        # If still empty, attempt to guess domain from business name
        if not actual_website and name:
            guessed = self._guess_website_from_business(name)
            if guessed:
                actual_website = guessed
        
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
    
    def _guess_website_from_business(self, name: str) -> str:
        """Guess likely website domain from business name."""
        # Remove punctuation and lowercase
        clean = re.sub(r'[^\w\s]', '', name).lower()
        # Remove common business suffixes
        suffixes = [' llc', ' inc', ' ltd', ' corp', ' company', ' &', ' and']
        for suf in suffixes:
            clean = clean.replace(suf, '')
        # Replace spaces with nothing
        domain = clean.replace(' ', '')
        if domain:
            return f"http://{domain}.com"
        return ""
    
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


class MultiSourceEngine:
    """Main orchestrator for multi-source lead generation."""
    
    def __init__(self, source_selection: Optional[List[str]] = None):
        self.registry = SourceRegistry()
        self.deduplicator = DeduplicationEngine()
        self.source_selection = source_selection
        self._register_sources()
        
    def _register_sources(self):
        """Register all available lead sources."""
        # Map source selection strings to source classes
        source_map = {
            "serper_maps": SerperSource,
            "google_places": GooglePlacesSource,
            "serper_web": SerperWebSource,
            "yelp": YelpSource,
        }
        
        if self.source_selection:
            for key in self.source_selection:
                if key in source_map:
                    self.registry.register(source_map[key]())
                else:
                    # Log unknown source selection
                    pass
        else:
            # Register all sources
            self.registry.register(SerperSource())
            self.registry.register(GooglePlacesSource())
            self.registry.register(SerperWebSource())
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
                            query: str,
                            exclude_competitors: bool = False,
                            include_clean_leads: bool = False) -> List[Dict]:
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
            
            tasks.append(audit_lead(raw, session,
                                    skip_competitor_filter=not exclude_competitors,
                                    skip_if_clean=not include_clean_leads))
        
        if not tasks:
            return []
        
        results = await asyncio.gather(*tasks, return_exceptions=True)
        
        audited_leads = []
        for r in results:
            if isinstance(r, dict) and r is not None:
                audited_leads.append(r)
        
        return audited_leads


async def run_engine_web(country: str, target: int,
                        queue: asyncio.Queue,
                        industry: Optional[str] = None,
                        business_type: Optional[str] = None,
                        city: Optional[str] = None,
                        min_lead_score: Optional[int] = None,
                        min_ops_score: Optional[int] = None,
                        min_intent_score: Optional[int] = None,
                        exclude_competitors: bool = False,
                        include_clean_leads: bool = False,
                        tech_stack_filters: Optional[List[str]] = None,
                        source_selection: Optional[List[str]] = None):
    """
    Main lead generation pipeline with multi-source support.

    Filters:
    - industry: restrict to a specific niche (e.g. "dentist", "roofer")
    - business_type: "ecommerce", "service", "restaurant", "saas", "clinic", "all"
    - city: override city-level targeting (skip AI city generation, use this one city)
    - min_*_score: minimum score thresholds for post-scrape filtering
    - exclude_competitors: skip marketing/agency type businesses
    - include_clean_leads: include leads with no detected pain points
    - tech_stack_filters: list of tech categories that must have gaps
    - source_selection: list of sources (serper_maps, google_places, serper_web, yelp)
    """
    init_db()

    location_label = f"{city}, {country}" if city else country
    await queue.put({
        "type": "info",
        "message": f"Multi-source engine: Target {target} leads in {location_label}",
    })

    # ── 1. Generate queries ──
    await queue.put({"type": "info", "message": "Generating search queries…"})
    loop = asyncio.get_running_loop()

    if city:
        # City override: generate niche queries for this one city only
        if industry:
            # Use the user-specified niche
            niches = [industry]
        else:
            # AI-generate niches but pin them to the single city
            from ai_engine import _call_ai
            niches = _call_ai(
                f"List 15 high-ticket LOCAL service business niches in {country} that need marketing. "
                "OUTPUT: JSON list of 15 strings ONLY."
            ) or ["Dentist","Roofer","Plumber","HVAC","Lawyer","Chiropractor","Med Spa","Pest Control","Auto Shop","Accountant"]
        queries = [f"{n} in {city}" for n in niches]
        import random as _random; _random.shuffle(queries)
        needed = math.ceil(target / AVG_LEADS_PER_QUERY * 1.5)
        queries = queries[:needed]
    else:
        queries = await loop.run_in_executor(None, build_search_queries, country, target)
        if not queries:
            await queue.put({"type": "error", "message": "Failed to generate queries. Check OpenRouter API key."})
            return
        # If user specified a niche, rebuild all queries using that niche × generated cities
        if industry:
            # Extract city names from the AI-generated queries
            cities_from_queries = []
            for q in queries:
                if ' in ' in q:
                    city_part = q.split(' in ', 1)[-1].strip()
                    if city_part and city_part not in cities_from_queries:
                        cities_from_queries.append(city_part)
            if cities_from_queries:
                # Build niche × city grid with the user's industry
                queries = [f"{industry} in {c}" for c in cities_from_queries]
                import random as _rand; _rand.shuffle(queries)
            else:
                # Fallback: single query per country
                queries = [f"{industry} in {country}"]
        needed = math.ceil(target / AVG_LEADS_PER_QUERY * 1.5)
        queries = queries[:needed]

    await queue.put({
        "type": "info",
        "message": f"{len(queries)} queries generated" + (f" for niche: {industry}" if industry else ""),
    })
    
    # ── 2. Initialize engine ──
    engine = MultiSourceEngine(source_selection=source_selection)
    
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
        limit=SCRAPE_THREADS, ssl=VERIFY_SSL, ttl_dns_cache=300)
    timeout = aiohttp.ClientTimeout(total=30)
    
    async with aiohttp.ClientSession(
        connector=connector, timeout=timeout
    ) as session:
        
        # Helper to filter leads based on advanced criteria
        def passes_filters(lead: Dict) -> bool:
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
            
            # Business type filter
            if business_type and business_type != "all":
                tech_stack = lead.get("tech_stack_json")
                if tech_stack:
                    try:
                        ts = json.loads(tech_stack)
                    except:
                        ts = {}
                else:
                    ts = {}
                has_ecommerce = ts.get("ecommerce", ["none"])[0] != "none"
                has_online_booking = ts.get("booking", ["none"])[0] != "none"
                has_online_ordering = ts.get("ordering", ["none"])[0] != "none"
                # Determine business type based on tech stack
                if business_type == "ecommerce" and not has_ecommerce:
                    return False
                elif business_type == "service" and (has_ecommerce or has_online_booking):
                    # Service businesses typically don't have ecommerce or booking
                    return False
                elif business_type == "restaurant" and not (has_online_ordering or has_online_booking):
                    # Restaurants typically have ordering or booking
                    return False
            
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
            
            # Tech stack filters — user wants leads that LACK each listed category.
            # A category "has a gap" iff it is either absent from the detected
            # tech_stack OR its value is ["none"].
            if tech_stack_filters:
                raw_ts = lead.get("tech_stack_json")
                try:
                    ts = json.loads(raw_ts) if raw_ts else {}
                except (json.JSONDecodeError, TypeError):
                    ts = {}
                for category in tech_stack_filters:
                    tools = ts.get(category)
                    has_gap = (not tools) or tools == ["none"]
                    if not has_gap:
                        # Lead has this tool — user wanted leads without it
                        return False

            return True
        
        for i, query in enumerate(queries):
            if total_saved >= target:
                break
            
            queries_done = i + 1
            
            await queue.put({
                "type": "progress",
                "message": f"[{queries_done}/{len(queries)}] {query}",
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
                session, source_leads, existing_ids, country, query,
                exclude_competitors, include_clean_leads)
            
            # ── Process audited leads ──
            for lead in audited_leads:
                # Apply advanced filters
                if not passes_filters(lead):
                    total_skipped += 1
                    continue
                
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
                    pains.append("Phone Only (No Email)")
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
            
            contact_str = f"email:{email_count} phone:{phone_count}"
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
        stats = conn.execute('''
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
        ''', (country,)).fetchone()
    
    # Source usage report
    source_report = "Sources used: "
    for source_name, source in engine.registry.sources.items():
        if source.calls_today > 0:
            source_report += f"{source_name}({source.calls_today}) "
    
    avg_score = stats['avg_score'] if stats['avg_score'] is not None else 0
    cs = get_currency_for_country(country).get("symbol", "$")
    await queue.put({
        "type": "summary",
        "message": (
            f"[DONE] Multi-source engine — {total_saved} leads saved\n"
            f"   With email: {stats['with_email'] or 0}\n"
            f"   With phone: {stats['with_phone'] or 0}\n"
            f"   No contact: {stats['no_contact'] or 0}\n"
            f"   Avg score: {avg_score:.0f}\n"
            f"   Total revenue gap: {cs}{stats['total_loss'] or 0:,}/mo\n"
            f"   Queries used: {queries_done} | "
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
                "info": "[i]", "success": "[+]", "error": "[x]",
                "warning": "[!]", "progress": "[>]", "skip": "[-]",
                "summary": "[*]",
            }.get(t, "[.]")
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
    print(f"\nLeadPro v4 — Multi-Source Engine — Generating {target} leads in {country}\n")
    asyncio.run(run_engine_cli(country, target))