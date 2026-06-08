"""
LeadPro v3 — Intent Score Calculator
Estimates how likely a lead is to actually buy.
"""


def calculate_intent_score(lead: dict, tech_stack: dict,
                            signals: dict) -> dict:
    """Returns {intent_score (0-100), reasons, readiness}."""
    score = 0
    reasons = []

    reviews = lead.get("review_count", 0)
    rating = lead.get("rating", 0)

    if reviews > 50 and rating >= 4.0:
        score += 15
        reasons.append("Active business — strong review volume")
    if reviews > 10 and rating < 3.5:
        score += 20
        reasons.append("Bad reviews + volume — owner likely aware of problems")

    # Hiring = investing in growth
    hiring = tech_stack.get("hiring", ["none"])
    if hiring != ["none"]:
        score += 25
        reasons.append("Actively hiring — growth mode")

    # Has some tech but gaps = upgrade mindset
    has_some = any(tech_stack.get(c, ["none"]) != ["none"]
                   for c in ["cms", "payments"])
    has_gaps = any(tech_stack.get(c, ["none"]) == ["none"]
                   for c in ["booking", "crm", "email_marketing", "chat"])
    if has_some and has_gaps:
        score += 20
        reasons.append("Already uses some tech — gaps are upgrade opportunities")

    # Basic CMS = outgrowing platform
    cms = tech_stack.get("cms", ["none"])
    if any(c in cms for c in ["wix", "godaddy", "weebly"]):
        score += 10
        reasons.append(f"On {cms[0]} — likely hitting limits")

    # Broken/slow site = knows there's a problem
    if lead.get("has_website") and (lead.get("site_dead") or
           (0 <= lead.get("pagespeed_score", -1) < 30)):
        score += 15
        reasons.append("Website broken or extremely slow — pain felt daily")

    # Established but zero digital
    if reviews > 30 and not lead.get("has_tracking_pixel") and not lead.get("has_website"):
        score += 15
        reasons.append("Established business with zero digital presence")

    if signals.get("multi_location"):
        score += 10
        reasons.append("Multi-location — has budget")

    if signals.get("has_contact_form"):
        score += 5
        reasons.append("Has contact form — open to inquiries")

    readiness = (
        "HOT — actively investing, clear gaps" if score >= 60
        else "WARM — aware of problems" if score >= 35
        else "COLD — may not be ready"
    )
    return {"intent_score": min(score, 100), "reasons": reasons,
            "readiness": readiness}