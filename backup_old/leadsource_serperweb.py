"""
LeadPro v3 — Serper Web Search Source
For finding e-commerce/DTC stores and online businesses.
"""
import re
import hashlib
from typing import List, Dict
import aiohttp
from leadsource_base import LeadSource, LeadData
from config import SERPER_API_KEY


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
        title = result.get("title", "")
        snippet = result.get("snippet", "")
        url = result.get("link", "")
        
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