"""
LeadPro v3 — Multi-Source Lead Generation Base Classes
Plugin architecture for multiple lead sources.
"""
import asyncio
import hashlib
from abc import ABC, abstractmethod
from typing import List, Dict, Optional, Set
import aiohttp
from dataclasses import dataclass
from datetime import datetime


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
        is_new = True
        
        # Check domain
        domain = lead.domain
        if domain and domain in self.seen_domains:
            is_new = False
        elif domain:
            self.seen_domains.add(domain)
        
        # Check email
        email = lead.email.lower().strip() if lead.email else ""
        if email and email != "n/a" and "@" in email:
            if email in self.seen_emails:
                is_new = False
            else:
                self.seen_emails.add(email)
        
        # Check phone (simple check)
        phone = lead.phone.strip() if lead.phone else ""
        if phone and phone != "n/a" and len(phone) >= 7:
            # Normalize phone a bit
            digits = ''.join(c for c in phone if c.isdigit())
            if len(digits) >= 10:  # Reasonable phone length
                last_10 = digits[-10:]  # Last 10 digits for matching
                if last_10 in self.seen_phones:
                    is_new = False
                else:
                    self.seen_phones.add(last_10)
        
        return is_new
    
    def clear(self):
        """Clear all seen data (e.g., at start of new session)."""
        self.seen_domains.clear()
        self.seen_emails.clear()
        self.seen_phones.clear()


def normalize_query_for_source(query: str, source_type: str) -> str:
    """
    Normalize queries for specific sources.
    Some sources work better with certain query formats.
    """
    query_lower = query.lower()
    
    if source_type == "ecommerce":
        # Enhance e-commerce queries
        if "shop" not in query_lower and "store" not in query_lower:
            # Try to add e-commerce context
            parts = query.split(" in ")
            if len(parts) == 2:
                niche, location = parts
                return f"{niche} online store in {location}"
    
    elif source_type == "maps":
        # Ensure maps queries have location
        if " in " not in query:
            # This is a problem - maps queries need location
            # We'll rely on the country parameter instead
            pass
    
    return query