"""
LeadPro v4 — Shared utilities.
"""
import re


# Country code map for phone normalization
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


def _parse_query(query: str) -> tuple[str, str]:
    """Extract niche and city from query like 'Dentist in London'."""
    match = re.match(r'^(.+?)\s+in\s+(.+)$', query, re.IGNORECASE)
    if match:
        return match.group(1).strip(), match.group(2).strip()
    return query.strip(), ""


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