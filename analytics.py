"""
LeadPro v3 — Analytics Engine
Deep SQL queries for funnel, attribution, A/B, and ROI metrics.
"""
import json, csv, io
from datetime import datetime, timedelta
from database import get_conn
import config


def get_full_analytics() -> dict:
    with get_conn() as conn:
        # Funnel
        funnel = {
            "total": conn.execute("SELECT COUNT(*) FROM leads").fetchone()[0],
            "emailed": conn.execute("SELECT COUNT(DISTINCT lead_id) FROM outreach WHERE status='sent'").fetchone()[0],
            "opened": conn.execute("SELECT COUNT(DISTINCT lead_id) FROM outreach WHERE open_tracked=1").fetchone()[0],
            "replied": conn.execute("SELECT COUNT(DISTINCT lead_id) FROM outreach WHERE reply_detected=1").fetchone()[0],
            "proposal_sent": conn.execute("SELECT COUNT(DISTINCT lead_id) FROM proposals").fetchone()[0],
        }

        # Rates
        total_sent = conn.execute("SELECT COUNT(*) FROM outreach WHERE status='sent'").fetchone()[0] or 1
        rates = {
            "total_sent": total_sent,
            "open_rate": round((conn.execute("SELECT SUM(open_tracked) FROM outreach WHERE status='sent'").fetchone()[0] or 0) / total_sent * 100, 1),
            "reply_rate": round((conn.execute("SELECT SUM(reply_detected) FROM outreach WHERE status='sent'").fetchone()[0] or 0) / total_sent * 100, 1),
        }

        # Campaign performance
        campaign_perf = [dict(r) for r in conn.execute("""
            SELECT c.id, c.name as campaign_name, c.country,
                COUNT(DISTINCT CASE WHEN o.status='sent' THEN o.lead_id END) as contacted,
                SUM(CASE WHEN o.status='sent' THEN o.open_tracked ELSE 0 END) as opens,
                SUM(CASE WHEN o.status='sent' THEN o.reply_detected ELSE 0 END) as replies,
                COUNT(DISTINCT p.lead_id) as proposals
            FROM campaigns c
            LEFT JOIN outreach o ON o.campaign_id = c.id
            LEFT JOIN proposals p ON p.lead_id = o.lead_id
            GROUP BY c.id ORDER BY replies DESC
            LIMIT 50
        """).fetchall()]

        # Pain point / service performance
        service_perf = [dict(r) for r in conn.execute("""
            SELECT l.ideal_service as service,
                COUNT(DISTINCT l.id) as lead_count,
                SUM(CASE WHEN o.reply_detected=1 THEN 1 ELSE 0 END) as replies,
                COUNT(DISTINCT CASE WHEN o.status='sent' THEN o.lead_id END) as contacted
            FROM leads l LEFT JOIN outreach o ON o.lead_id = l.id
            GROUP BY l.ideal_service
            HAVING contacted > 2 ORDER BY replies DESC
        """).fetchall()]

        # Source query performance
        query_perf = [dict(r) for r in conn.execute("""
            SELECT l.source_query,
                COUNT(*) as leads_found,
                ROUND(AVG(l.lead_score),1) as avg_score,
                SUM(CASE WHEN o.reply_detected=1 THEN 1 ELSE 0 END) as replies
            FROM leads l LEFT JOIN outreach o ON o.lead_id=l.id
            GROUP BY l.source_query ORDER BY replies DESC LIMIT 20
        """).fetchall()]

        # Daily volume (14 days)
        daily = [dict(r) for r in conn.execute("""
            SELECT DATE(sent_at) as day, COUNT(*) as sent,
                SUM(open_tracked) as opened,
                SUM(reply_detected) as replied
            FROM outreach WHERE status='sent' AND sent_at > datetime('now','-14 days')
            GROUP BY DATE(sent_at) ORDER BY day
        """).fetchall()]

        # Bounce stats
        bounce = dict(conn.execute("""
            SELECT COUNT(*) as total,
                SUM(CASE WHEN bounce_type='hard' THEN 1 ELSE 0 END) as hard,
                SUM(CASE WHEN bounce_type='soft' THEN 1 ELSE 0 END) as soft
            FROM outreach WHERE status='bounced'
        """).fetchone())

        # Warmup health
        warmup = [dict(r) for r in conn.execute("""
            SELECT wa.id, wa.email, wa.domain, wa.role, wa.current_day, wa.status, wa.daily_limit
            FROM warmup_accounts wa ORDER BY wa.role, wa.domain
        """).fetchall()]

        # SEO overview
        seo = dict(conn.execute("""
            SELECT COUNT(DISTINCT lead_id) as tracked,
                COUNT(*) as total_kw,
                SUM(CASE WHEN position IS NOT NULL AND position<=10 THEN 1 ELSE 0 END) as page_one,
                SUM(CASE WHEN position IS NULL THEN 1 ELSE 0 END) as not_ranking,
                ROUND(AVG(CASE WHEN position IS NOT NULL THEN position END),1) as avg_pos
            FROM seo_rankings
        """).fetchone())

    return {
        "funnel": funnel, "rates": rates, "campaign_perf": campaign_perf,
        "service_perf": service_perf, "query_perf": query_perf,
        "daily_volume": daily, "bounce_stats": bounce,
        "warmup_health": warmup, "seo_overview": seo,
    }


def get_cohort_analysis(days_back: int = 30):
    """Cohort analysis: group leads by acquisition week and track outreach performance."""
    with get_conn() as conn:
        rows = conn.execute("""
            SELECT DATE(l.scraped_at, 'weekday 0', '-6 days') as cohort_week,
                   COUNT(DISTINCT l.id) as leads,
                   COUNT(DISTINCT CASE WHEN o.status='sent' THEN l.id END) as contacted,
                   SUM(CASE WHEN o.reply_detected=1 THEN 1 ELSE 0 END) as replies,
                   SUM(CASE WHEN o.open_tracked=1 THEN 1 ELSE 0 END) as opens
            FROM leads l
            LEFT JOIN outreach o ON o.lead_id = l.id
            WHERE l.scraped_at >= datetime('now', ?)
            GROUP BY cohort_week
            ORDER BY cohort_week DESC
        """, (f"-{days_back} days",)).fetchall()
        return [dict(r) for r in rows]


def get_ab_test_results():
    """Aggregate A/B test performance."""
    with get_conn() as conn:
        rows = conn.execute("""
            SELECT subject, variant,
                   SUM(opened) as total_opens,
                   SUM(clicked) as total_clicks,
                   SUM(replied) as total_replies,
                   COUNT(*) as sends
            FROM ab_tests
            GROUP BY subject, variant
            ORDER BY subject, variant
        """).fetchall()
        return [dict(r) for r in rows]


def get_best_send_time():
    """Analyze open rates by hour of day (based on sent_at)."""
    with get_conn() as conn:
        rows = conn.execute("""
            SELECT CAST(strftime('%H', sent_at) AS INTEGER) as hour,
                   COUNT(*) as total_sent,
                   SUM(open_tracked) as opens,
                   SUM(reply_detected) as replies
            FROM outreach
            WHERE status='sent' AND sent_at IS NOT NULL
            GROUP BY hour
            ORDER BY hour
        """).fetchall()
        result = []
        for r in rows:
            rate = (r["opens"] / r["total_sent"] * 100) if r["total_sent"] else 0
            result.append({
                "hour": r["hour"],
                "total_sent": r["total_sent"],
                "opens": r["opens"],
                "replies": r["replies"],
                "open_rate": round(rate, 1)
            })
        return result


def export_csv(data: list[dict]) -> str:
    """Export list of dicts (or sqlite3.Row objects) to CSV string."""
    if not data:
        return ""
    # Convert sqlite3.Row objects to dicts
    if hasattr(data[0], 'keys'):
        # Already dict-like with keys() method
        pass
    else:
        # sqlite3.Row or similar: convert to dict
        data = [dict(row) for row in data]
    output = io.StringIO()
    writer = csv.DictWriter(output, fieldnames=list(data[0].keys()))
    writer.writeheader()
    writer.writerows(data)
    return output.getvalue()


def export_excel(data: list[dict]) -> bytes:
    """Export list of dicts to Excel (.xlsx) bytes using openpyxl."""
    try:
        from openpyxl import Workbook
    except ImportError as e:
        raise ImportError("openpyxl required for Excel export") from e
    from io import BytesIO
    wb = Workbook()
    ws = wb.active
    if data:
        keys = list(data[0].keys())
        ws.append(keys)
        for row in data:
            if hasattr(row, 'get'):
                ws.append([row.get(k) for k in keys])
            else:
                ws.append([row[k] for k in keys])
    buffer = BytesIO()
    wb.save(buffer)
    return buffer.getvalue()


async def send_slack_notification(message: str) -> bool:
    """Send notification to Slack webhook (async-safe)."""
    if not config.SLACK_WEBHOOK_URL:
        return False
    import aiohttp
    try:
        async with aiohttp.ClientSession() as session:
            async with session.post(
                config.SLACK_WEBHOOK_URL,
                json={"text": message},
                timeout=aiohttp.ClientTimeout(total=5),
            ) as resp:
                return 200 <= resp.status < 300
    except Exception:
        return False


async def send_discord_notification(message: str) -> bool:
    """Send notification to Discord webhook (async-safe)."""
    if not config.DISCORD_WEBHOOK_URL:
        return False
    import aiohttp
    try:
        async with aiohttp.ClientSession() as session:
            async with session.post(
                config.DISCORD_WEBHOOK_URL,
                json={"content": message},
                timeout=aiohttp.ClientTimeout(total=5),
            ) as resp:
                return 200 <= resp.status < 300
    except Exception:
        return False