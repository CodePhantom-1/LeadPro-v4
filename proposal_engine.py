"""
LeadPro v3 — Proposal Generator
Now includes ROI data, tech stack, and operations pain points.
"""
import json, os
from datetime import datetime
from fpdf import FPDF
from database import get_conn
from ai_engine import generate_executive_summary, generate_recommendations
from audit import estimate_revenue_impact, get_currency_for_country, get_language_for_country


class ProposalPDF(FPDF):
    ACCENT=(0,200,255);DARK=(10,15,30);GRAY=(120,140,160);WHITE=(255,255,255);RED=(255,80,80);GREEN=(0,200,120)
    def header(self):
        self.set_fill_color(*self.DARK);self.rect(0,0,210,28,'F')
        self.set_font("Helvetica","B",15);self.set_text_color(*self.ACCENT);self.set_xy(10,7);self.cell(0,12,"DIGITAL PRESENCE AUDIT",ln=True)
        self.set_font("Helvetica","",9);self.set_text_color(*self.GRAY);self.set_xy(10,16);self.cell(0,8,f"Generated {datetime.now().strftime('%B %d, %Y')}");self.ln(18)
    def footer(self):
        self.set_y(-15);self.set_font("Helvetica","I",8);self.set_text_color(*self.GRAY);self.cell(0,10,f"Page {self.page_no()}/{{nb}} | Confidential",align="C")
    def section_title(self,t):
        self.set_font("Helvetica","B",13);self.set_text_color(*self.ACCENT);self.cell(0,10,t,ln=True);self.set_draw_color(*self.ACCENT);self.line(10,self.get_y(),200,self.get_y());self.ln(4)
    def metric_row(self,label,value,status="neutral"):
        self.set_font("Helvetica","",10);self.set_text_color(60,60,60);self.cell(90,7,label)
        c=self.GREEN if status=="good" else self.RED if status=="bad" else self.GRAY
        ic="PASS" if status=="good" else "FAIL" if status=="bad" else "--"
        self.set_text_color(*c);self.set_font("Helvetica","B",10);self.cell(20,7,ic,align="C")
        self.set_font("Helvetica","",10);self.set_text_color(60,60,60);self.cell(0,7,value,ln=True)


def generate_proposal(lead_id):
    with get_conn() as conn:
        lead = conn.execute("SELECT * FROM leads WHERE id=?",(lead_id,)).fetchone()
        if not lead: raise ValueError(f"Lead {lead_id} not found")
        lead = dict(lead)
        seo = [dict(r) for r in conn.execute("SELECT * FROM seo_rankings WHERE lead_id=? ORDER BY checked_at DESC LIMIT 10",(lead_id,)).fetchall()]
        competitors = [dict(r) for r in conn.execute("SELECT * FROM competitors WHERE lead_id=?",(lead_id,)).fetchall()]

    pains = json.loads(lead.get("pain_points","[]") or "[]")
    ops_pains = json.loads(lead.get("ops_pain_points","[]") or "[]")
    tech_stack = json.loads(lead.get("tech_stack_json","{}") or "{}")

    # ROI calc
    roi = estimate_revenue_impact(lead.get("niche",""), pains, ops_pains, lead.get("country",""))
    monthly_loss = roi["total_monthly_loss"]
    cs = roi.get("currency_symbol", "$")

    # Gaps
    gaps = []
    for c in competitors:
        for key, label in [("has_ssl","SSL"),("is_mobile_friendly","Mobile"),("has_tracking_pixel","Tracking"),("has_facebook","Facebook"),("has_instagram","Instagram")]:
            if c.get(key) and not lead.get(key):
                gaps.append({"what":label,"competitor":c.get("name","?")})

    pdf = ProposalPDF(); pdf.alias_nb_pages(); pdf.add_page()

    # Title
    pdf.set_font("Helvetica","B",18);pdf.set_text_color(0,0,0);    pdf.cell(0,12,lead.get("business_name", "")[:60],ln=True)
    pdf.set_font("Helvetica","",10);pdf.set_text_color(*ProposalPDF.GRAY)
    dm = lead.get("decision_maker")
    dm_line = f"Prepared for {dm} | " if dm else ""
    pdf.cell(0,6,f"{dm_line}{lead.get('niche','')} | {lead.get('city','')}, {lead.get('country','')}",ln=True)
    pdf.cell(0,6,f"Score: {lead.get('lead_score', 0)}/100 | Rating: {lead.get('rating',0)} ({lead.get('review_count',0)} reviews)",ln=True)
    pdf.ln(4)

    # Revenue impact callout
    if monthly_loss > 0:
        pdf.set_fill_color(254,242,242);pdf.set_draw_color(220,38,38)
        pdf.rect(10,pdf.get_y(),190,22,'DF')
        pdf.set_font("Helvetica","B",14);pdf.set_text_color(220,38,38)
        pdf.set_xy(15,pdf.get_y()+3)
        pdf.cell(0,7,f"Estimated Revenue Impact: {cs}{monthly_loss:,}/month ({cs}{monthly_loss*12:,}/year)",ln=True)
        pdf.set_text_color(0,0,0);pdf.ln(12)

    # Executive summary
    pdf.section_title("EXECUTIVE SUMMARY")
    summary = generate_executive_summary(lead, seo, competitors, gaps, currency_symbol=cs, language=get_language_for_country(lead.get("country","")))
    pdf.set_font("Helvetica","",10);pdf.set_text_color(40,40,40)
    for line in summary.split("\n"): pdf.multi_cell(0,5.5,line.strip());pdf.ln(1)
    pdf.ln(4)

    # Revenue breakdown
    if roi["impacts"]:
        pdf.section_title("REVENUE IMPACT BREAKDOWN")
        for imp in roi["impacts"][:8]:
            pdf.set_font("Helvetica","",10);pdf.set_text_color(40,40,40)
            pdf.cell(120,7,imp["issue"][:55])
            pdf.set_text_color(*ProposalPDF.RED);pdf.set_font("Helvetica","B",10)
            pdf.cell(0,7,f"-{cs}{imp['monthly_loss']:,}/mo",ln=True,align="R")
        pdf.ln(4)

    # Tech stack
    if tech_stack:
        pdf.section_title("TECHNOLOGY STACK DETECTED")
        for cat, tools in tech_stack.items():
            pdf.set_font("Helvetica","",10)
            if tools == ["none"]:
                pdf.set_text_color(*ProposalPDF.RED)
                pdf.cell(0,6,f"[X] {cat.replace('_',' ').title()}: Not detected",ln=True)
            else:
                pdf.set_text_color(*ProposalPDF.GREEN)
                pdf.cell(0,6,f"[OK] {cat.replace('_',' ').title()}: {', '.join(t.replace('_',' ').title() for t in tools)}",ln=True)
        pdf.ln(4)

    # Website audit
    pdf.section_title("WEBSITE AUDIT")
    pdf.metric_row("SSL Certificate","Secure" if lead.get("has_ssl") else "NOT SECURE","good" if lead.get("has_ssl") else "bad")
    pdf.metric_row("Mobile-Friendly","Yes" if lead.get("is_mobile_friendly") else "No","good" if lead.get("is_mobile_friendly") else "bad")
    pdf.metric_row("Ad Tracking","Installed" if lead.get("has_tracking_pixel") else "Missing","good" if lead.get("has_tracking_pixel") else "bad")
    ps=lead.get("pagespeed_score",-1)
    pdf.metric_row("PageSpeed",f"{ps}/100" if ps>=0 else "N/A","good" if ps>=50 else "bad" if ps>=0 else "neutral")
    pdf.ln(4)

    # SEO
    if seo:
        pdf.section_title("SEARCH VISIBILITY")
        for r in seo[:5]:
            p=r.get("position");st="good" if p and p<=3 else "neutral" if p and p<=10 else "bad"
            pdf.metric_row(f'"{r["keyword"]}"',f"#{p}" if p else "Not found",st)
        pdf.ln(4)

    # Competitors
    if competitors:
        pdf.add_page();pdf.section_title("COMPETITOR COMPARISON")
        pdf.set_font("Helvetica","B",8);pdf.set_fill_color(*ProposalPDF.DARK);pdf.set_text_color(*ProposalPDF.WHITE)
        ws=[52,16,18,16,16,16,22]
        for i,col in enumerate(["Business","SSL","Mob","Track","Soc","Spd","Rate"]): pdf.cell(ws[i],7,col,border=1,fill=True,align="C")
        pdf.ln()
        rows=[{"n":lead["business_name"]+" (YOU)","l":True,**lead}]+[{"n":c.get("name","?"),"l":False,**c} for c in competitors]
        for row in rows:
            pdf.set_font("Helvetica","B" if row["l"] else "",7);pdf.set_text_color(0,0,0);pdf.cell(ws[0],6,row["n"][:28],border=1)
            for j,k in enumerate(["has_ssl","is_mobile_friendly","has_tracking_pixel"]):
                v=bool(row.get(k));pdf.set_text_color(*(ProposalPDF.GREEN if v else ProposalPDF.RED));pdf.cell(ws[j+1],6,"Y" if v else "N",border=1,align="C")
            hs=any([row.get("has_facebook"),row.get("has_instagram"),row.get("has_linkedin")]);pdf.set_text_color(*(ProposalPDF.GREEN if hs else ProposalPDF.RED));pdf.cell(ws[4],6,"Y" if hs else "N",border=1,align="C")
            spd=row.get("pagespeed_score",-1);pdf.set_text_color(*(ProposalPDF.GREEN if spd>=50 else ProposalPDF.RED if spd>=0 else ProposalPDF.GRAY));pdf.cell(ws[5],6,str(spd) if spd>=0 else "--",border=1,align="C")
            rt=row.get("rating",0);pdf.set_text_color(*(ProposalPDF.GREEN if rt>=4 else ProposalPDF.RED if rt>0 else ProposalPDF.GRAY));pdf.cell(ws[6],6,f"{rt}" if rt else "--",border=1,align="C");pdf.ln()
        pdf.set_text_color(0,0,0);pdf.ln(4)

    # Recommendations
    pdf.add_page();pdf.section_title("RECOMMENDATIONS")
    recs = generate_recommendations(lead, gaps, currency_symbol=cs, language=get_language_for_country(lead.get("country","")))
    pdf.set_font("Helvetica","",10);pdf.set_text_color(40,40,40)
    for line in recs.split("\n"): pdf.multi_cell(0,5.5,line.strip());pdf.ln(1)

    os.makedirs("reports",exist_ok=True)
    safe="".join(c if c.isalnum() or c in " -_" else "" for c in lead["business_name"]).strip().replace(" ","_")[:40]
    filename=f"reports/{safe}_{lead_id}_{datetime.now():%Y%m%d}.pdf"
    pdf.output(filename)
    with get_conn() as conn:
        try:
            conn.execute(
                """INSERT INTO proposals (lead_id, pdf_path, kind) VALUES (?,?, 'pdf')
                   ON CONFLICT(lead_id, kind) DO UPDATE SET
                       pdf_path=excluded.pdf_path,
                       created_at=datetime('now')""",
                (lead_id, filename),
            )
        except Exception:
            existing = conn.execute(
                "SELECT id FROM proposals WHERE lead_id=? AND kind='pdf'", (lead_id,)
            ).fetchone()
            if existing:
                conn.execute(
                    "UPDATE proposals SET pdf_path=?, created_at=datetime('now') WHERE id=?",
                    (filename, existing["id"]),
                )
            else:
                conn.execute(
                    "INSERT INTO proposals (lead_id, pdf_path, kind) VALUES (?,?,?)",
                    (lead_id, filename, "pdf"),
                )
    return filename


def generate_html_proposal(lead_id: int) -> str:
    """Generate interactive HTML proposal with pricing packages."""
    with get_conn() as conn:
        lead = conn.execute("SELECT * FROM leads WHERE id=?", (lead_id,)).fetchone()
        if not lead:
            raise ValueError(f"Lead {lead_id} not found")
        lead = dict(lead)
        pains = json.loads(lead.get("pain_points", "[]") or "[]")
        ops_pains = json.loads(lead.get("ops_pain_points", "[]") or "[]")
        roi = estimate_revenue_impact(lead.get("niche", ""), pains, ops_pains, lead.get("country", ""))
        monthly_loss = roi["total_monthly_loss"]
        currency = get_currency_for_country(lead.get("country", ""))
        cs = currency["symbol"]
        fx = currency["rate"]
    
    packages = [
        {
            "name": "Essential Fix",
            "price": round(1997 * fx),
            "description": "Address the most critical revenue leak with a focused 30-day sprint.",
            "features": ["1‑priority fix (e.g., mobile‑friendly site)", "Basic tracking setup", "30‑day support", "Weekly check‑ins"],
            "roi_months": max(1, round(monthly_loss / round(1997 * fx))) if monthly_loss > 0 else "N/A",
            "color": "#3b82f6"
        },
        {
            "name": "Growth Accelerator",
            "price": round(4997 * fx),
            "description": "Comprehensive overhaul targeting 3‑5 high‑impact gaps.",
            "features": ["Full website audit & redesign", "Ad tracking + pixel setup", "SEO optimization (5 keywords)", "CRM/booking integration", "90‑day support"],
            "roi_months": max(1, round(monthly_loss / round(4997 * fx))) if monthly_loss > 0 else "N/A",
            "color": "#8b5cf6"
        },
        {
            "name": "Enterprise Transformation",
            "price": round(14997 * fx),
            "description": "End‑to‑end digital transformation with dedicated team.",
            "features": ["Everything in Growth Accelerator", "Custom software integration", "Monthly performance reviews", "Unlimited revisions", "12‑month support"],
            "roi_months": max(1, round(monthly_loss / round(14997 * fx))) if monthly_loss > 0 else "N/A",
            "color": "#10b981"
        }
    ]
    
    # Build package cards outside the main f-string to avoid nested-quote issues
    import urllib.parse as _u
    business_name_safe = lead['business_name']
    business_name_q = _u.quote(business_name_safe)
    packages_html_parts = []
    for pkg in packages:
        color = pkg['color']
        features_html = "".join(
            f'<li><i class="fas fa-check" style="color: {color}"></i>{feat}</li>'
            for feat in pkg['features']
        )
        roi = pkg['roi_months']
        if isinstance(roi, str):
            roi_text = roi
        else:
            roi_text = f"{roi} month" + ("s" if roi > 1 else "")
        pkg_name_q = _u.quote(pkg['name'])
        mailto = (
            f"mailto:?subject=Proposal%20{business_name_q}%20{pkg_name_q}"
            f"&body=Hi,%20I%27m%20interested%20in%20the%20{pkg_name_q}%20package."
        )
        packages_html_parts.append(f'''
            <div class="package" style="border-color: {color}">
                <h3 style="color: {color}">{pkg['name']}</h3>
                <p>{pkg['description']}</p>
                <div class="price" style="color: {color}">{cs}{pkg['price']:,}</div>
                <ul>{features_html}</ul>
                <p><strong>ROI timeline:</strong> {roi_text}</p>
                <a class="cta" href="{mailto}" style="background: {color}">Select Plan</a>
            </div>''')
    packages_html = "".join(packages_html_parts)

    html = f'''<!DOCTYPE html>
<html lang="en">
<head>
    <meta charset="UTF-8">
    <meta name="viewport" content="width=device-width, initial-scale=1.0">
    <title>Proposal — {html.escape(lead['business_name'])}</title>
    <link rel="stylesheet" href="https://cdnjs.cloudflare.com/ajax/libs/font-awesome/6.4.0/css/all.min.css">
    <style>
        * {{ margin: 0; padding: 0; box-sizing: border-box; }}
        body {{ font-family: -apple-system, BlinkMacSystemFont, 'Segoe UI', Roboto, sans-serif; background: #f8fafc; color: #1e293b; line-height: 1.6; }}
        .container {{ max-width: 1000px; margin: 0 auto; padding: 24px; }}
        .header {{ background: linear-gradient(135deg, #0f172a, #1e3a5f); color: white; padding: 40px 32px; border-radius: 16px; margin-bottom: 32px; }}
        h1 {{ font-size: 28px; margin-bottom: 8px; }}
        .subtitle {{ color: #94a3b8; }}
        .roi-badge {{ background: rgba(255,255,255,0.1); border: 1px solid #fbbf24; color: #fbbf24; padding: 8px 16px; border-radius: 20px; display: inline-block; margin-top: 16px; }}
        .packages {{ display: flex; flex-wrap: wrap; gap: 24px; margin: 32px 0; }}
        .package {{ flex: 1; min-width: 250px; background: white; border-radius: 12px; padding: 28px; box-shadow: 0 4px 12px rgba(0,0,0,0.05); border: 2px solid #e2e8f0; transition: transform 0.2s, border-color 0.2s; }}
        .package:hover {{ transform: translateY(-4px); border-color: #3b82f6; }}
        .package h3 {{ font-size: 20px; margin-bottom: 8px; }}
        .package .price {{ font-size: 32px; font-weight: 800; margin: 16px 0; }}
        .package ul {{ list-style: none; }}
        .package li {{ padding: 8px 0; color: #475569; }}
        .package li i {{ margin-right: 8px; }}
        .cta {{ display: inline-block; background: #2563eb; color: white; text-decoration: none; padding: 14px 28px; border-radius: 10px; font-weight: 600; font-size: 16px; margin-top: 20px; }}
        .footer {{ text-align: center; color: #94a3b8; font-size: 14px; margin-top: 48px; }}
    </style>
</head>
<body>
    <div class="container">
        <div class="header">
            <h1>Digital Transformation Proposal</h1>
            <p class="subtitle">Prepared for {lead['business_name']} • {lead.get('city', '')}, {lead.get('country', '')}</p>
            <div class="roi-badge">
                <i class="fas fa-chart-line"></i> Estimated monthly revenue loss: <strong>{cs}{monthly_loss:,}</strong>
            </div>
        </div>
        <p style="font-size: 18px; color: #374151; margin-bottom: 24px;">
            The following packages are tailored to close the gaps identified in your digital audit.
            Each includes a clear ROI timeline based on your estimated revenue loss.
        </p>
        <div class="packages">{packages_html}</div>
        <div style="text-align: center; margin-top: 40px;">
            <a class="cta" href="/proposal/{lead_id}/pdf"><i class="fas fa-file-pdf"></i> Download PDF Proposal</a>
        </div>
        <div class="footer">
            Generated by LeadPro v4 • {datetime.now().strftime('%B %d, %Y')}
        </div>
    </div>
</body>
</html>'''
    
    os.makedirs("reports", exist_ok=True)
    safe = "".join(c if c.isalnum() or c in " -_" else "" for c in lead["business_name"]).strip().replace(" ", "_")[:40]
    html_path = f"reports/{safe}_{lead_id}_{datetime.now():%Y%m%d}.html"
    with open(html_path, "w", encoding="utf-8") as f:
        f.write(html)

    # Upsert into proposals keyed by (lead_id, kind='html_proposal')
    with get_conn() as conn:
        try:
            conn.execute(
                """INSERT INTO proposals (lead_id, page_path, kind) VALUES (?,?, 'html_proposal')
                   ON CONFLICT(lead_id, kind) DO UPDATE SET
                       page_path=excluded.page_path,
                       created_at=datetime('now')""",
                (lead_id, html_path),
            )
        except Exception:
            # Fallback manual upsert
            cur = conn.execute(
                "UPDATE proposals SET page_path=? WHERE lead_id=? AND kind='html_proposal'",
                (html_path, lead_id),
            )
            if cur.rowcount == 0:
                conn.execute(
                    "INSERT INTO proposals (lead_id, page_path, kind) VALUES (?,?,?)",
                    (lead_id, html_path, "html_proposal"),
                )

    return html