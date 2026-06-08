"""
LeadPro v3 — Decision-Maker Enrichment
Extracts owner/founder names from website HTML.
"""
import re
import json
from bs4 import BeautifulSoup


def extract_owner_from_html(html: str, soup: BeautifulSoup) -> dict:
    """Find owner/founder name from website content. Returns {name, title, source}."""
    result = {"name": None, "title": None, "source": None}
    # Clean text by removing script/style tags to avoid JS/CSS noise
    soup_copy = BeautifulSoup(str(soup), 'html.parser')
    for tag in soup_copy(['script', 'style']):
        tag.decompose()
    text = soup_copy.get_text(strip=True, separator=' ')

    # 1. Schema.org structured data
    for schema_tag in soup.find_all("script", type="application/ld+json"):
        try:
            data = json.loads(schema_tag.string)
            if isinstance(data, dict):
                for key in ["founder", "author", "employee"]:
                    person = data.get(key)
                    if isinstance(person, dict) and person.get("name"):
                        result["name"] = person["name"]
                        result["title"] = key.title()
                        result["source"] = "schema.org"
                        return result
                    if isinstance(person, list) and person:
                        p = person[0]
                        if isinstance(p, dict) and p.get("name"):
                            result["name"] = p["name"]
                            result["title"] = key.title()
                            result["source"] = "schema.org"
                            return result
        except Exception:
            pass

    # 2. Text patterns
    patterns = [
        r'(?:owner|founder|ceo|principal|director|proprietor)[:\s,–—-]*([A-Z][a-z]+ [A-Z][a-z]+)',
        r'([A-Z][a-z]+ [A-Z][a-z]+)[,\s]*(?:owner|founder|ceo|principal|director)',
        r'(?:meet|about)\s+([A-Z][a-z]+ [A-Z][a-z]+)[,\s]*(?:the )?\s*(?:owner|founder)',
        r'(?:Dr\.|Dr)\s+([A-Z][a-z]+ [A-Z][a-z]+)',
    ]
    ignore = {"the owner", "our team", "the founder", "read more",
              "learn more", "click here", "our staff", "our doctors"}
    for pattern in patterns:
        match = re.search(pattern, text)
        if match:
            name = match.group(1).strip()
            if name.lower() not in ignore and len(name) > 4:
                result["name"] = name
                result["title"] = "Owner"
                result["source"] = "page_content"
                return result

    # 3. Meta author tag
    for meta in soup.find_all("meta"):
        prop = (meta.get("property", "") or meta.get("name", "")).lower()
        if "author" in prop:
            name = (meta.get("content") or "").strip()
            if name and len(name.split()) >= 2:
                result["name"] = name
                result["title"] = "Author"
                result["source"] = "meta_tag"
                return result

    return result