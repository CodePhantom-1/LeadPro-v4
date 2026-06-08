"""
LeadPro v3 — ROI Calculator
Translates pain points into dollar estimates.
"""

NICHE_REVENUE = {
    "dentist": 800_000, "restaurant": 500_000, "plumber": 300_000,
    "hvac": 450_000, "lawyer": 1_200_000, "salon": 250_000,
    "med spa": 600_000, "chiropractor": 350_000, "auto shop": 400_000,
    "gym": 500_000, "vet": 600_000, "accountant": 500_000,
    "pest control": 250_000, "cleaning": 200_000, "roofer": 350_000,
    "electrician": 300_000, "barber": 200_000, "physio": 350_000,
}

GAP_IMPACT = {
    "no website": 0.25, "no online booking": 0.15, "no online ordering": 0.20,
    "no tracking pixel": 0.08, "not mobile-friendly": 0.12,
    "no ssl": 0.05, "no social media": 0.06, "no email marketing": 0.10,
    "no live chat": 0.03, "no loyalty": 0.08, "no crm": 0.10,
    "poor rating": 0.15, "no online payment": 0.05, "no gift card": 0.03,
    "slow website": 0.06, "broken site": 0.20, "outdated website": 0.04,
    "limited platform": 0.04, "very few reviews": 0.05,
    "free email": 0.02, "ada non-compliant": 0.02,
}

COUNTRY_MULT = {
    "us": 1.0, "usa": 1.0, "united states": 1.0,
    "uk": 0.85, "united kingdom": 0.85,
    "netherlands": 0.80, "germany": 0.80, "france": 0.75,
    "australia": 0.90, "canada": 0.90, "spain": 0.65,
}


def estimate_revenue_impact(niche: str, pain_points: list[str],
                             ops_pains: list[dict] = None,
                             country: str = "") -> dict:
    niche_lower = (niche or "").lower()
    base = 300_000
    for key, rev in NICHE_REVENUE.items():
        if key in niche_lower:
            base = rev
            break

    mult = COUNTRY_MULT.get(country.lower(), 0.75) if country else 0.80
    est_annual = base * mult
    est_monthly = est_annual / 12

    impacts = []
    total_lost = 0

    # Marketing pain points
    for pain in pain_points:
        pain_lower = pain.lower()
        for gap_key, pct in GAP_IMPACT.items():
            if gap_key in pain_lower:
                loss = round(est_monthly * pct)
                impacts.append({"issue": pain, "monthly_loss": loss,
                                "annual_loss": loss * 12})
                total_lost += loss
                break

    # Ops pain points (these carry their own estimates)
    if ops_pains:
        for op in ops_pains:
            ml = op.get("monthly_loss", 0)
            if ml > 0:
                # Adjust by country multiplier
                adj = round(ml * mult)
                impacts.append({"issue": op["pain"], "monthly_loss": adj,
                                "annual_loss": adj * 12})
                total_lost += adj

    return {
        "estimated_monthly_revenue": round(est_monthly),
        "impacts": impacts,
        "total_monthly_loss": total_lost,
        "total_annual_loss": total_lost * 12,
    }