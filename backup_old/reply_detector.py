"""
LeadPro v2 - Reply Detector
Checks Gmail INBOX via IMAP for replies to sent campaigns.
Marks leads as replied, detects opt-outs, suppresses future contact.
"""
import imaplib
import email
from email.header import decode_header
from datetime import datetime, timedelta
import re
from database import get_conn, log_event
from config import YOUR_EMAIL, YOUR_APP_PASSWORD, IMAP_SERVER

OPT_OUT_PHRASES = [
    "unsubscribe", "remove me", "stop emailing", "not interested",
    "please stop", "do not contact", "take me off", "opt out",
]


def _decode_header_value(value: str) -> str:
    parts = decode_header(value)
    decoded = []
    for part, enc in parts:
        if isinstance(part, bytes):
            decoded.append(part.decode(enc or "utf-8", errors="replace"))
        else:
            decoded.append(part)
    return " ".join(decoded)


def _is_opt_out(body: str) -> bool:
    low = body.lower()
    return any(p in low for p in OPT_OUT_PHRASES)


def _imap_since_date(days_back: int = 30) -> str:
    """Returns IMAP-formatted date string for N days ago."""
    since = datetime.now() - timedelta(days=days_back)
    return since.strftime("%d-%b-%Y")


def check_replies(dry_run: bool = False) -> dict:
    """
    Connects to IMAP, scans inbox for replies to campaign emails.
    Updates DB accordingly. Returns summary stats.
    """
    stats = {"scanned": 0, "replies": 0, "opt_outs": 0, "errors": 0}

    try:
        mail = imaplib.IMAP4_SSL(IMAP_SERVER)
        mail.login(YOUR_EMAIL, YOUR_APP_PASSWORD)
        mail.select("INBOX")

        # Dynamic lookback: last 30 days (not a hardcoded stale date)
        since_date = _imap_since_date(days_back=30)
        _, msg_ids = mail.search(None, f'SINCE "{since_date}"')
        all_ids = msg_ids[0].split()
        print(f"📬 Scanning {len(all_ids)} inbox messages since {since_date}...")

        with get_conn() as conn:
            sent = conn.execute(
                "SELECT o.id, o.lead_id, o.subject, l.email "
                "FROM outreach o JOIN leads l ON l.id = o.lead_id "
                "WHERE o.status = 'sent'"
            ).fetchall()

        sent_map = {
            (r["subject"] or "").lower(): (r["id"], r["lead_id"], r["email"])
            for r in sent
        }

        for mid in all_ids[-200:]:
            stats["scanned"] += 1
            try:
                _, data = mail.fetch(mid, "(RFC822)")
                msg = email.message_from_bytes(data[0][1])

                subject = _decode_header_value(msg.get("Subject", ""))
                from_addr = msg.get("From", "")
                from_email = re.search(r'[\w._%+-]+@[\w.-]+\.[A-Z|a-z]{2,}', from_addr)
                if not from_email:
                    continue
                from_email = from_email.group(0).lower()

                clean_subject = re.sub(
                    r'^(re:|fwd?:)\s*', '', subject, flags=re.IGNORECASE
                ).strip().lower()

                match = sent_map.get(clean_subject)
                if not match:
                    with get_conn() as conn:
                        lead_row = conn.execute(
                            "SELECT id FROM leads WHERE LOWER(email)=?", (from_email,)
                        ).fetchone()
                    if not lead_row:
                        continue
                    with get_conn() as conn:
                        o = conn.execute(
                            "SELECT id FROM outreach WHERE lead_id=? "
                            "ORDER BY sent_at DESC LIMIT 1",
                            (lead_row["id"],)
                        ).fetchone()
                    if not o:
                        continue
                    oid, lid = o["id"], lead_row["id"]
                else:
                    oid, lid, _ = match

                body = ""
                if msg.is_multipart():
                    for part in msg.walk():
                        if part.get_content_type() == "text/plain":
                            body = part.get_payload(decode=True).decode(errors="replace")
                            break
                else:
                    body = msg.get_payload(decode=True).decode(errors="replace")

                is_opt = _is_opt_out(body)

                if not dry_run:
                    with get_conn() as conn:
                        conn.execute(
                            "UPDATE outreach SET reply_detected=1 WHERE id=?", (oid,)
                        )
                        event = "opted_out" if is_opt else "replied"
                        conn.execute(
                            "INSERT INTO events (lead_id, event_type, note) VALUES (?,?,?)",
                            (lid, event, subject[:100])
                        )
                        if is_opt:
                            conn.execute(
                                "UPDATE outreach SET status='cancelled' "
                                "WHERE lead_id=? AND status='pending'",
                                (lid,)
                            )

                stats["replies"] += 1
                if is_opt:
                    stats["opt_outs"] += 1
                    print(f"   🚫 OPT-OUT from: {from_email}")
                else:
                    print(f"   💬 REPLY from: {from_email} | '{subject[:50]}'")

            except Exception:
                stats["errors"] += 1
                continue

        mail.logout()

    except Exception as e:
        print(f"❌ IMAP error: {e}")
        stats["errors"] += 1

    print(f"\n📊 Reply scan done: {stats}")
    return stats
