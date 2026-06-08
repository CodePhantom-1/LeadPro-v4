"""
LeadPro v3 — Consolidated Outreach Engine
Combines email sending, reply detection, and warmup functionality.
"""
import email
import smtplib
import imaplib
import asyncio
import time
import base64
import re
import random
import datetime
from datetime import timezone, timedelta
from email.mime.text import MIMEText
from email.mime.multipart import MIMEMultipart
from email.mime.base import MIMEBase
from email import encoders
from email.header import decode_header
from config import (
    YOUR_EMAIL, YOUR_APP_PASSWORD, YOUR_NAME, YOUR_COMPANY,
    SMTP_SERVER, SMTP_PORT, EMAIL_DELAY_SEC, DAILY_EMAIL_CAP,
    FOLLOWUP_SCHEDULE, TRACKING_DOMAIN, BASE_URL,
    IMAP_SERVER, PHANTOMBUSTER_API_KEY, TWILIO_ACCOUNT_SID,
    TWILIO_AUTH_TOKEN, TWILIO_PHONE_FROM, REPLY_INTELLIGENCE_ENABLED,
    BREVO_SMTP_LOGIN, BREVO_SMTP_KEY, BREVO_SMTP_HOST, BREVO_SMTP_PORT,
    CENTRAL_INBOX_EMAIL, CENTRAL_INBOX_PASSWORD, CENTRAL_INBOX_IMAP,
    decrypt_password
)
from database import (
    get_leads_for_outreach, get_due_followups,
    get_opted_out_emails, get_hard_bounce_emails,
    save_outreach, mark_outreach_sent, log_event, get_conn,
    get_outreach_accounts, pick_outreach_account
)
from ai_engine import generate_email, generate_warmup_email, generate_reply_draft
from audit_pages import generate_audit_page, generate_audit_preview
from audit import get_currency_for_country, get_language_for_country
from reply_handler import classify_reply
from database import save_reply_intelligence

# ============================================================================
# Email Validation
# ============================================================================

def is_valid_email(email: str) -> bool:
    """Validate email format and basic sanity."""
    if not email or not isinstance(email, str):
        return False
    email = email.strip().lower()
    if email in ("", "n/a", "none", "no email"):
        return False
    # Basic regex for email format
    pattern = r'^[a-zA-Z0-9._%+-]{2,}@[a-zA-Z0-9.-]+\.[a-zA-Z]{2,24}$'
    if not re.match(pattern, email):
        return False
    # Check disposable domains (common temporary email providers)
    disposable_domains = {
        "tempmail.com", "mailinator.com", "guerrillamail.com", "10minutemail.com",
        "throwawaymail.com", "yopmail.com", "temp-mail.org", "fakeinbox.com",
        "sharklasers.com", "getairmail.com", "tempail.com", "trashmail.com",
        "maildrop.cc", "dispostable.com", "mailnesia.com", "tmpmail.org",
    }
    domain = email.split('@')[1]
    if domain in disposable_domains:
        return False
    return True

# ============================================================================
# Send‑Time Optimization
# ============================================================================

def get_optimal_hour() -> int:
    """Return hour of day (0‑23) with highest historical open rate."""
    from database import get_conn
    with get_conn() as conn:
        rows = conn.execute("""
            SELECT CAST(strftime('%H', sent_at) AS INTEGER) as hour,
                   SUM(open_tracked)*1.0/COUNT(*) as open_rate
            FROM outreach
            WHERE status='sent' AND sent_at IS NOT NULL
            GROUP BY hour
            HAVING COUNT(*) > 5
            ORDER BY open_rate DESC
            LIMIT 1
        """).fetchall()
        if rows:
            return rows[0]["hour"]
    # Fallback: 10 AM local time
    return 10


def schedule_for_optimal_time(base_datetime: datetime.datetime) -> datetime.datetime:
    """Shift a datetime to the optimal sending hour, keeping same day."""
    optimal = get_optimal_hour()
    return base_datetime.replace(hour=optimal, minute=random.randint(0, 59), second=0)


# ============================================================================
# Emailer Module
# ============================================================================

_HARD_BOUNCE_CODES = {550, 551, 552, 553, 554}

def _classify_smtp_error(exc):
    return "hard" if exc.smtp_code in _HARD_BOUNCE_CODES else "soft"

def _inject_tracking(body_text, outreach_id):
    if not TRACKING_DOMAIN:
        return body_text, ""
    html_body = body_text.replace("&","&amp;").replace("<","&lt;").replace(">","&gt;").replace("\n","<br>")
    url_pattern = r'(https?://[^\s<>"]+)'
    def _rewrite(m):
        url = m.group(1)
        if TRACKING_DOMAIN in url: return url
        enc = base64.urlsafe_b64encode(url.encode()).decode()
        return f"{TRACKING_DOMAIN}/t/c/{outreach_id}/{enc}"
    html_body = re.sub(url_pattern, _rewrite, html_body)
    pixel = f'<img src="{TRACKING_DOMAIN}/t/o/{outreach_id}.gif" width="1" height="1" style="display:none">'
    return body_text, f"<html><body><p>{html_body}</p>{pixel}</body></html>"


class SMTPSession:
    """
    Account-aware SMTP session.
    Pass an outreach_account dict to use a specific account,
    or leave None to fall back to the legacy config credentials.
    """
    def __init__(self, account: dict = None):
        self._conn = None
        self.signature = None
        if account:
            if account.get("sending_mode") == "brevo_relay":
                self.smtp_host   = BREVO_SMTP_HOST
                self.smtp_port   = BREVO_SMTP_PORT
                self.login_email = BREVO_SMTP_LOGIN
                self.password    = BREVO_SMTP_KEY
                self.from_name   = account.get("display_name") or YOUR_NAME
                self.from_email  = account["email"]
                self.account_id  = account["id"]
                self.signature   = account.get("signature")
            else:
                self.smtp_host   = account["smtp_host"]
                self.smtp_port   = account["smtp_port"]
                self.login_email = account["email"]
                self.password    = decrypt_password(account["password"])
                self.from_name   = account.get("display_name") or YOUR_NAME
                self.from_email  = account["email"]
                self.account_id  = account["id"]
        else:
            self.smtp_host   = SMTP_SERVER
            self.smtp_port   = SMTP_PORT
            self.login_email = YOUR_EMAIL
            self.password    = YOUR_APP_PASSWORD
            self.from_name   = YOUR_NAME
            self.from_email  = YOUR_EMAIL
            self.account_id  = None

    def _connect(self):
        s = smtplib.SMTP(self.smtp_host, self.smtp_port, timeout=30)
        s.starttls()
        s.login(self.login_email, self.password)
        self._conn = s

    def send(self, to, subject, body, outreach_id=None, attachment_path=None):
        plain, html = _inject_tracking(body, outreach_id) if outreach_id else (body, "")
        msg = MIMEMultipart("mixed")
        msg["From"] = f"{self.from_name} <{self.from_email}>"
        msg["To"] = to
        msg["Subject"] = subject
        if self.signature:
            footer = f"\n\n{self.signature}\nReply 'unsubscribe' to opt out."
        else:
            footer = f"\n\n---\n{self.from_name} | {YOUR_COMPANY}\nReply 'unsubscribe' to opt out."
        alt = MIMEMultipart("alternative")
        alt.attach(MIMEText(plain + footer, "plain"))
        if html:
            hf = footer.replace("\n", "<br>")
            alt.attach(MIMEText(html.replace("</body>", f"<br><small>{hf}</small></body>"), "html"))
        msg.attach(alt)
        if attachment_path:
            try:
                with open(attachment_path, "rb") as f:
                    part = MIMEBase("application", "pdf")
                    part.set_payload(f.read())
                encoders.encode_base64(part)
                part.add_header("Content-Disposition", "attachment",
                                filename=attachment_path.split("/")[-1])
                msg.attach(part)
            except Exception:
                pass
        for attempt in range(3):
            try:
                if self._conn is None:
                    self._connect()
                self._conn.send_message(msg)
                return True, None
            except smtplib.SMTPResponseException as e:
                b = _classify_smtp_error(e)
                if b == "hard":
                    return False, "hard"
                self._conn = None
                time.sleep(15)
            except (smtplib.SMTPServerDisconnected, ConnectionError):
                self._conn = None
                if attempt < 2:
                    time.sleep(5)
                else:
                    return False, None
            except Exception:
                self._conn = None
                if attempt < 2:
                    time.sleep(10)
                else:
                    return False, None
        return False, "soft"

    def quit(self):
        try:
            if self._conn:
                self._conn.quit()
        except Exception:
            pass
        self._conn = None


class _BrevoSlave(SMTPSession):
    """Brevo agent that delegates its SMTP connection to a master session."""
    def __init__(self, account, master):
        super().__init__(account)
        self._master = master
    def _connect(self):
        if not self._master._conn:
            self._master._connect()
        self._conn = self._master._conn


def _get_smtp_pool(accounts: list | None = None) -> list[SMTPSession]:
    """
    Build a pool of SMTPSession objects from outreach_accounts table.
    Falls back to a single legacy-config session if no accounts are configured.
    For brevo_relay accounts, shares a single SMTP connection across all agents.
    """
    if accounts is None:
        accounts = get_outreach_accounts()
    if not accounts:
        return [SMTPSession(None)]
    active = [a for a in accounts if a["remaining"] > 0]

    brevo_accounts = [a for a in active if a.get("sending_mode") == "brevo_relay"]
    smtp_accounts = [a for a in active if a.get("sending_mode") != "brevo_relay"]

    pool = [SMTPSession(a) for a in smtp_accounts]

    if brevo_accounts:
        master = SMTPSession(brevo_accounts[0])
        pool.append(master)
        for a in brevo_accounts[1:]:
            pool.append(_BrevoSlave(a, master))

    return pool


def _pick_session(pool: list[SMTPSession],
                  local_counters: dict[int, int] | None = None) -> SMTPSession | None:
    """Pick the session with most remaining capacity.

    Uses a local in-memory counter (populated from the DB once) so we don't
    re-query `outreach_accounts` on every send (previously O(N) extra queries
    per send). The caller tracks decrements after each successful send.
    """
    if not pool:
        return None

    if local_counters is None:
        accounts = get_outreach_accounts()
        local_counters = {a["id"]: a["remaining"] for a in accounts}

    def _cap(sess):
        if sess.account_id is None:
            return 999999  # legacy unlimited
        return local_counters.get(sess.account_id, 0)

    eligible = [s for s in pool if _cap(s) > 0]
    if not eligible:
        return None
    return max(eligible, key=_cap)


def _decrement_counter(counters: dict[int, int] | None, account_id: int | None):
    """Decrement in-memory per-account remaining counter after a successful send."""
    if counters is None or account_id is None:
        return
    if account_id in counters:
        counters[account_id] = max(0, counters[account_id] - 1)


def test_smtp():
    """Test the primary/legacy SMTP config. Returns True/False."""
    try:
        s = smtplib.SMTP(SMTP_SERVER, SMTP_PORT, timeout=10)
        s.starttls()
        s.login(YOUR_EMAIL, YOUR_APP_PASSWORD)
        s.quit()
        return True
    except Exception:
        return False

def test_brevo_smtp():
    """Test the Brevo SMTP relay config. Returns True/False."""
    if not BREVO_SMTP_KEY:
        return False
    try:
        s = smtplib.SMTP(BREVO_SMTP_HOST, BREVO_SMTP_PORT, timeout=10)
        s.starttls()
        s.login(BREVO_SMTP_LOGIN, BREVO_SMTP_KEY)
        s.quit()
        return True
    except Exception:
        return False

def _emails_sent_today():
    today = datetime.date.today().isoformat()
    with get_conn() as conn:
        r = conn.execute(
            "SELECT COUNT(*) FROM outreach WHERE channel='email' AND status='sent' AND sent_at LIKE ?",
            (today + "%",)
        ).fetchone()
    return r[0] if r else 0


def _total_remaining_capacity(accounts: list | None = None) -> int:
    """
    Total emails we can still send today across all accounts.
    Uses outreach_accounts table if populated, else falls back to config cap.
    """
    if accounts is None:
        accounts = get_outreach_accounts()
    if not accounts:
        return DAILY_EMAIL_CAP - _emails_sent_today()
    return sum(a["remaining"] for a in accounts)

def _schedule_followups(lead_id, campaign_id, initial_subject, sent_at):
    base = datetime.datetime.fromisoformat(sent_at)
    for i, days in enumerate(FOLLOWUP_SCHEDULE, start=2):
        scheduled = base + datetime.timedelta(days=days)
        scheduled = schedule_for_optimal_time(scheduled)
        save_outreach({"lead_id":lead_id,"campaign_id":campaign_id,"channel":"email",
                       "sequence_step":i,"subject":None,"body":None,"status":"pending",
                       "scheduled_for":scheduled.isoformat(),"ai_model_used":None})


async def run_initial_outreach_web(campaign_id, min_score, lead_ids, queue):
    loop = asyncio.get_running_loop()
    opted_out = get_opted_out_emails(); hard_bounce = get_hard_bounce_emails()
    accounts = get_outreach_accounts()
    remaining = _total_remaining_capacity(accounts)
    if remaining <= 0:
        await queue.put({"type":"warning","message":"Daily cap reached on all accounts."}); return
    leads = get_leads_for_outreach(campaign_id, step=1, min_score=min_score)
    if lead_ids: leads = [l for l in leads if l["id"] in set(lead_ids)]

    # Build SMTP pool + in-memory capacity counters (avoids N+1 DB queries)
    pool = _get_smtp_pool(accounts)
    counters = {a["id"]: a["remaining"] for a in accounts}
    if accounts:
        acct_summary = ", ".join(f"{a['email']} ({a['remaining']} rem)" for a in accounts)
        await queue.put({"type":"info","message":f"{len(accounts)} sending account(s): {acct_summary}"})
    else:
        await queue.put({"type":"info","message":f"Sending via config account: {YOUR_EMAIL}"})
    await queue.put({"type":"info","message":f"{len(leads)} qualified leads | {remaining} sends remaining today"})

    sent_count = 0
    try:
        for lead in leads[:remaining]:
            ea = lead["email"]
            if not ea or not is_valid_email(ea): continue
            if ea in opted_out or ea in hard_bounce: continue

            dm = lead.get("decision_maker")
            loss = lead.get("estimated_monthly_loss") or 0
            ops_json = lead.get("ops_pain_points")
            cs = get_currency_for_country(lead.get("country")).get("symbol", "$")
            lang = get_language_for_country(lead.get("country"))
            
            # Ensure audit page exists and is not expired
            audit_url = None
            audit_preview = None
            try:
                token = lead["audit_page_token"]
                expiry = lead["audit_page_expires_at"]
                if not token or (expiry and datetime.datetime.fromisoformat(expiry) < datetime.datetime.now()):
                    # Generate or regenerate audit page
                    await queue.put({"type":"info","message":f"Generating audit page for {lead['business_name']}..."})
                    # Fetch seo/competitor data for richer audit page
                    with get_conn() as conn:
                        seo_rows = [dict(r) for r in conn.execute(
                            "SELECT * FROM seo_rankings WHERE lead_id=? ORDER BY checked_at DESC LIMIT 10",
                            (lead["id"],)).fetchall()]
                        comp_rows = [dict(r) for r in conn.execute(
                            "SELECT * FROM competitors WHERE lead_id=?",
                            (lead["id"],)).fetchall()]
                    # Build ROI for audit page
                    from audit import estimate_revenue_impact
                    import json as _j
                    _pains = _j.loads(lead["pain_points"] or "[]")
                    _ops = _j.loads(lead["ops_pain_points"] or "[]")
                    _roi = estimate_revenue_impact(lead["niche"], _pains, _ops, lead["country"])
                    token_path = await loop.run_in_executor(None, lambda ld=lead, s=seo_rows, c=comp_rows, r=_roi: generate_audit_page(
                        ld["id"], ld, s, c, r
                    ))
                    # token_path is e.g. "/audit/<token>"
                    fresh_token = (token_path or "").rstrip("/").split("/")[-1] or None
                    # Confirm via DB (triggers/race safety); fall back to the path-derived token
                    with get_conn() as conn:
                        updated = conn.execute("SELECT audit_page_token FROM leads WHERE id=?", (lead["id"],)).fetchone()
                    db_token = updated["audit_page_token"] if updated else None
                    token = db_token or fresh_token

                if not token:
                    raise RuntimeError("Audit token not available")
                audit_url = f"{BASE_URL}/audit/{token}"
                audit_preview = generate_audit_preview(lead, token)
            except Exception as e:
                await queue.put({"type":"warning","message":f"Audit page generation failed: {str(e)[:50]}"})
                audit_url = None
                audit_preview = None

            await queue.put({"type":"generating",
                "message":f"{dm+' at ' if dm else ''}{lead['business_name']} ({cs}{loss:,}/mo loss)...",
                "lead_id":lead["id"],"business":lead["business_name"],"score":lead["lead_score"]})

            # Pick the account with most capacity BEFORE generating email (so we know sender identity)
            sess = _pick_session(pool, counters)
            if sess is None:
                await queue.put({"type":"warning","message":"All accounts at daily cap — stopping."})
                break

            content = await loop.run_in_executor(None, lambda lead=lead, dm=dm, loss=loss, ops_json=ops_json, audit_url=audit_url, audit_preview=audit_preview, cs=cs, sn=sess.from_name: generate_email(
                business_name=lead.get("business_name") or "",
                pain_points_json=lead.get("pain_points") or "[]",
                source_query=lead.get("source_query") or "",
                country=lead.get("country") or "", sequence_step=1,
                decision_maker=dm, estimated_monthly_loss=loss,
                ops_pain_points_json=ops_json,
                audit_page_url=audit_url,
                audit_preview=audit_preview,
                niche=lead.get("niche") or "",
                currency_symbol=cs,
                language=lang,
                sender_name=sn,
            ))
            if not content:
                await queue.put({"type":"warning","message":f"AI failed for {lead['business_name']}"}); continue

            subject = content.get("subject", f"Quick question for {lead['business_name']}")
            body = content.get("body","")
            now = datetime.datetime.now().isoformat()
            oid = save_outreach({"lead_id":lead["id"],"campaign_id":campaign_id,"channel":"email",
                                 "sequence_step":1,"subject":subject,"body":body,"status":"sending",
                                 "scheduled_for":now,"ai_model_used":content.get("model_used","")})

            # Record which account is being used
            if sess.account_id:
                with get_conn() as conn:
                    conn.execute("UPDATE outreach SET outreach_account_id=? WHERE id=?",
                                 (sess.account_id, oid))

            success, bounce_type = await loop.run_in_executor(None, lambda s=sess: s.send(ea, subject, body, outreach_id=oid))
            if success:
                mark_outreach_sent(oid,"sent")
                log_event(lead["id"],"emailed",f"step=1|dm={dm}|loss={cs}{loss}|acct={sess.from_email}")
                _schedule_followups(lead["id"], campaign_id, subject, now)
                _decrement_counter(counters, sess.account_id)
                sent_count += 1
                acct_label = f" [{sess.from_email}]" if sess.account_id else ""
                await queue.put({"type":"sent","message":f"[OK] {lead['business_name']} -> {ea}{acct_label}","sent_count":sent_count})
                await asyncio.sleep(EMAIL_DELAY_SEC)
            else:
                mark_outreach_sent(oid, "bounced" if bounce_type else "failed", bounce_type)
                await queue.put({"type":"error","message":f"[FAIL] ({bounce_type or 'fail'}) {lead['business_name']} -> {ea}"})
    finally:
        for s in pool:
            s.quit()
    await queue.put({"type":"summary","message":f"Done. Sent: {sent_count} across {len(pool)} account(s)","sent":sent_count})


async def run_followups_web(queue):
    loop = asyncio.get_running_loop()
    opted_out = get_opted_out_emails(); hard_bounce = get_hard_bounce_emails()
    due = get_due_followups()
    if not due:
        await queue.put({"type":"info","message":"No follow-ups due."}); return
    cap_left = _total_remaining_capacity()
    accounts = get_outreach_accounts()
    pool = _get_smtp_pool(accounts)
    counters = {a["id"]: a["remaining"] for a in accounts}
    await queue.put({"type":"info","message":f"{len(due)} due | {cap_left} sends remaining today"})
    sent_count = 0
    try:
        for row in due[:cap_left]:
            ea = row["email"]
            if not ea or not is_valid_email(ea) or ea in opted_out or ea in hard_bounce:
                mark_outreach_sent(row["id"],"cancelled"); continue
            with get_conn() as conn:
                if conn.execute("SELECT 1 FROM outreach WHERE lead_id=? AND reply_detected=1",(row["lead_id"],)).fetchone():
                    mark_outreach_sent(row["id"],"cancelled"); continue
                orig = conn.execute("SELECT subject FROM outreach WHERE lead_id=? AND sequence_step=1 AND status='sent' LIMIT 1",(row.get("lead_id"),)).fetchone()
            prev_sub = orig["subject"] if orig else ""
            dm = row.get("decision_maker")
            loss = row.get("estimated_monthly_loss") or 0
            cs = get_currency_for_country(row.get("country")).get("symbol", "$")
            lang = get_language_for_country(row.get("country"))
            
            # Ensure audit page exists for follow-up
            audit_url = None
            audit_preview = None
            try:
                token = row["audit_page_token"]
                expiry = row["audit_page_expires_at"]
                if not token or (expiry and datetime.datetime.fromisoformat(expiry) < datetime.datetime.now()):
                    # Generate or regenerate audit page
                    with get_conn() as conn:
                        seo_rows = [dict(r) for r in conn.execute(
                            "SELECT * FROM seo_rankings WHERE lead_id=? ORDER BY checked_at DESC LIMIT 10",
                            (row["lead_id"],)).fetchall()]
                        comp_rows = [dict(r) for r in conn.execute(
                            "SELECT * FROM competitors WHERE lead_id=?",
                            (row["lead_id"],)).fetchall()]
                    from audit import estimate_revenue_impact
                    import json as _j
                    _pains = _j.loads(row["pain_points"] or "[]")
                    _ops = _j.loads(row["ops_pain_points"] or "[]") if row["ops_pain_points"] else []
                    _roi = estimate_revenue_impact(row["niche"], _pains, _ops, row["country"])
                    token_path = await loop.run_in_executor(None, lambda rv=row, s=seo_rows, c=comp_rows, r=_roi: generate_audit_page(
                        rv["lead_id"], rv, s, c, r
                    ))
                    fresh_token = (token_path or "").rstrip("/").split("/")[-1] or None
                    with get_conn() as conn:
                        updated = conn.execute("SELECT audit_page_token FROM leads WHERE id=?", (row["lead_id"],)).fetchone()
                    db_token = updated["audit_page_token"] if updated else None
                    token = db_token or fresh_token

                if token:
                    audit_url = f"{BASE_URL}/audit/{token}"
                    audit_preview = generate_audit_preview(row, token)
            except Exception:
                audit_url = None
                audit_preview = None

            # Pick the account before generating email (so we know sender identity)
            sess = _pick_session(pool, counters)
            if sess is None:
                await queue.put({"type":"warning","message":"All accounts at daily cap — stopping."})
                break

            content = await loop.run_in_executor(None, lambda: generate_email(
                business_name=row.get("business_name") or "",pain_points_json=row.get("pain_points") or "[]",
                source_query=row.get("source_query") or "",country=row.get("country") or "",
                sequence_step=row.get("sequence_step", 1),previous_subject=prev_sub,
                decision_maker=dm,estimated_monthly_loss=loss,
                ops_pain_points_json=row["ops_pain_points"] if "ops_pain_points" in row else None,
                audit_page_url=audit_url,
                audit_preview=audit_preview,
                niche=row.get("niche") or "",
                currency_symbol=cs,
                language=lang,
                sender_name=sess.from_name,
            ))
            if not content: mark_outreach_sent(row["id"],"failed"); continue
            subject = content.get("subject",f"Re: {prev_sub}"); body = content.get("body","")
            with get_conn() as conn:
                conn.execute("UPDATE outreach SET subject=?, body=? WHERE id=?",(subject,body,row["id"]))

            if sess.account_id:
                with get_conn() as conn:
                    conn.execute("UPDATE outreach SET outreach_account_id=? WHERE id=?",
                                 (sess.account_id, row["id"]))

            success, bt = await loop.run_in_executor(None, lambda s=sess: s.send(ea, subject, body, outreach_id=row["id"]))
            mark_outreach_sent(row["id"],"sent" if success else ("bounced" if bt else "failed"), bt)
            if success:
                log_event(row["lead_id"],"emailed",f"step={row['sequence_step']}|acct={sess.from_email}")
                _decrement_counter(counters, sess.account_id)
                sent_count += 1
                acct_label = f" [{sess.from_email}]" if sess.account_id else ""
                await queue.put({"type":"sent","message":f"[OK] FU{row['sequence_step']} -> {row['business_name']}{acct_label}","sent_count":sent_count})
                await asyncio.sleep(EMAIL_DELAY_SEC)
    finally:
        for s in pool:
            s.quit()
    await queue.put({"type":"summary","message":f"Follow-ups done. Sent: {sent_count}","sent":sent_count})


# ============================================================================
# Reply Detector Module
# ============================================================================

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
    since = datetime.datetime.now() - datetime.timedelta(days=days_back)
    return since.strftime("%d-%b-%Y")


def check_replies(dry_run: bool = False, max_messages: int = 1000,
                  days_back: int = 30) -> dict:
    """
    Connects to IMAP, scans inbox for replies to campaign emails.
    Updates DB accordingly. Returns summary stats.

    Scans up to `max_messages` most-recent messages within the SINCE window.
    """
    stats = {"scanned": 0, "replies": 0, "opt_outs": 0, "errors": 0}

    # Build sent map (same for all accounts)
    with get_conn() as conn:
        sent = conn.execute(
            "SELECT o.id, o.lead_id, o.subject, l.email "
            "FROM outreach o JOIN leads l ON l.id = o.lead_id "
            "WHERE o.status = 'sent'"
        ).fetchall()

    sent_map = {}
    for r in sorted(sent, key=lambda x: x['sent_at'] or ''):
        key = ((r["subject"] or "").lower(), (r["email"] or "").lower())
        sent_map[key] = (r["id"], r["lead_id"], r["email"])

    def _scan_imap(imap_host, email_addr, password):
        """Scan a single IMAP inbox."""
        nonlocal stats
        try:
            mail = imaplib.IMAP4_SSL(imap_host)
            mail.login(email_addr, password)
            mail.select("INBOX")

            since_date = _imap_since_date(days_back=days_back)
            _, msg_ids = mail.search(None, f'SINCE "{since_date}"')
            all_ids = msg_ids[0].split()
            print(f"[SCAN] Scanning {len(all_ids)} inbox messages since {since_date} for {email_addr}...")

            for mid in all_ids[-max_messages:]:
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

                    match = sent_map.get((clean_subject, from_email))
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

                    # Idempotency: skip already-processed replies
                    with get_conn() as conn:
                        already = conn.execute(
                            "SELECT reply_detected FROM outreach WHERE id=?", (oid,)
                        ).fetchone()
                    if already and already["reply_detected"]:
                        continue

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
                            
                            # AI reply intelligence (if enabled)
                            if REPLY_INTELLIGENCE_ENABLED and not is_opt:
                                try:
                                    biz_row = conn.execute(
                                        "SELECT business_name FROM leads WHERE id=?", (lid,)
                                    ).fetchone()
                                    business_name = biz_row["business_name"] if biz_row else ""
                                    classification = classify_reply(body, business_name) or {}
                                    category = classification.get("category", "UNRELATED")
                                    summary = classification.get("summary", "")
                                    suggested = classification.get("suggested_response") or ""
                                    save_reply_intelligence(
                                        lead_id=lid,
                                        outreach_id=oid,
                                        category=category,
                                        summary=summary,
                                        suggested_response=suggested,
                                        raw_reply=body[:500]
                                    )
                                    stage_map = {
                                        "INTERESTED": "interested",
                                        "NOT_INTERESTED": "closed",
                                        "QUESTION": "replied_question",
                                        "REFERRAL": "referral",
                                        "OPT_OUT": "opted_out",
                                    }
                                    stage = stage_map.get(category)
                                    if stage:
                                        conn.execute(
                                            "UPDATE leads SET pipeline_stage=?, last_activity_at=datetime('now') WHERE id=?",
                                            (stage, lid)
                                        )
                                except Exception as e:
                                    print(f"[WARN] Reply intelligence failed: {e}")

                    stats["replies"] += 1
                    if is_opt:
                        stats["opt_outs"] += 1
                        print(f"   [OPT-OUT] from: {from_email}")
                    else:
                        print(f"   [REPLY] from: {from_email} | '{subject[:50]}'")

                except Exception:
                    stats["errors"] += 1
                    continue

            mail.logout()

        except Exception as e:
            print(f"[ERROR] IMAP error for {email_addr}: {e}")
            stats["errors"] += 1

    # Scan inboxes — use central inbox if Brevo agents exist with Cloudflare routing
    accounts = get_outreach_accounts()
    brevo_accounts = [a for a in accounts if a.get("sending_mode") == "brevo_relay"] if accounts else []

    if brevo_accounts and CENTRAL_INBOX_EMAIL:
        print(f"[SCAN] Using central inbox ({CENTRAL_INBOX_EMAIL}) for {len(brevo_accounts)} Brevo agent(s)")
        _scan_imap(CENTRAL_INBOX_IMAP, CENTRAL_INBOX_EMAIL, CENTRAL_INBOX_PASSWORD)
        for acct in accounts:
            if acct.get("sending_mode") != "brevo_relay":
                imap_host = acct.get("imap_host") or IMAP_SERVER
                _scan_imap(imap_host, acct["email"], acct["password"])
    elif accounts:
        for acct in accounts:
            imap_host = acct.get("imap_host") or IMAP_SERVER
            email_addr = acct["email"]
            password = acct["password"]
            _scan_imap(imap_host, email_addr, password)
    else:
        _scan_imap(IMAP_SERVER, YOUR_EMAIL, YOUR_APP_PASSWORD)

    print(f"\n[DONE] Reply scan: {stats}")
    return stats


# ============================================================================
# Warmup Engine Module
# ============================================================================

WARMUP_RAMP = {
    1:5, 2:5, 3:8, 4:8, 5:10, 6:12, 7:15, 8:18, 9:20, 10:25,
    11:30, 12:35, 13:40, 14:50, 15:60, 16:70, 17:80, 18:90,
    19:100, 20:120, 21:140, 22:160, 23:180, 24:200, 25:220,
    26:240, 27:260, 28:280,
}


def _send_warmup_email(smtp_host, smtp_port, email, password,
                        to_email, subject, body) -> bool:
    try:
        msg = MIMEText(body, "plain")
        msg["From"] = email
        msg["To"] = to_email
        msg["Subject"] = subject
        with smtplib.SMTP(smtp_host, smtp_port, timeout=30) as s:
            s.starttls()
            s.login(email, password)
            s.send_message(msg)
        return True
    except Exception:
        return False


def rescue_from_spam(imap_host, email_addr, password, sender_domains) -> int:
    rescued = 0
    mail = None
    try:
        mail = imaplib.IMAP4_SSL(imap_host)
        mail.login(email_addr, password)
        for folder in ["[Gmail]/Spam", "Junk", "Spam", "INBOX.Spam", "Bulk Mail"]:
            try:
                status, _ = mail.select(f'"{folder}"')
                if status != "OK":
                    continue
            except Exception:
                continue
            try:
                for domain in sender_domains:
                    _, msg_ids = mail.search(None, f'FROM "@{domain}"')
                    if not msg_ids[0]:
                        continue
                    for mid in msg_ids[0].split():
                        mail.copy(mid, "INBOX")
                        mail.store(mid, "+FLAGS", "\\Deleted")
                        rescued += 1
            finally:
                try:
                    mail.expunge()
                except Exception:
                    pass
        mail.select("INBOX")
        for domain in sender_domains:
            _, msg_ids = mail.search(None, f'FROM "@{domain}"')
            if msg_ids[0]:
                for mid in msg_ids[0].split():
                    mail.store(mid, "+FLAGS", "\\Flagged")
    except Exception:
        pass
    finally:
        if mail:
            try:
                mail.logout()
            except Exception:
                pass
    return rescued


def check_placement(imap_host, email_addr, password, sender_domains) -> dict:
    result = {"inbox": 0, "spam": 0}
    mail = None
    try:
        mail = imaplib.IMAP4_SSL(imap_host)
        mail.login(email_addr, password)
        mail.select("INBOX")
        for d in sender_domains:
            _, ids = mail.search(None, f'FROM "@{d}"')
            if ids[0]:
                result["inbox"] += len(ids[0].split())
        for folder in ["[Gmail]/Spam", "Junk", "Spam"]:
            try:
                s, _ = mail.select(f'"{folder}"')
                if s != "OK":
                    continue
                for d in sender_domains:
                    _, ids = mail.search(None, f'FROM "@{d}"')
                    if ids[0]:
                        result["spam"] += len(ids[0].split())
            except Exception:
                continue
    except Exception:
        pass
    finally:
        if mail:
            try:
                mail.logout()
            except Exception:
                pass
    result["total"] = result["inbox"] + result["spam"]
    result["inbox_rate"] = result["inbox"] / result["total"] if result["total"] > 0 else 0
    return result


async def run_warmup_cycle(queue: asyncio.Queue = None):
    async def log(t, m, **kw):
        if queue:
            await queue.put({"type": t, "message": m, **kw})
        print(f"[WARMUP] [{t}] {m}")

    from config import decrypt_password

    with get_conn() as conn:
        senders = [dict(r) for r in conn.execute(
            "SELECT * FROM warmup_accounts WHERE role='sender' AND status='warming'"
        ).fetchall()]
        receivers = [dict(r) for r in conn.execute(
            "SELECT * FROM warmup_accounts WHERE role='receiver'"
        ).fetchall()]

    # Decrypt passwords before use
    for s in senders:
        s["password"] = decrypt_password(s["password"])
    for r in receivers:
        r["password"] = decrypt_password(r["password"])

    if not senders or not receivers:
        await log("warning", "Need at least 1 sender + 1 receiver account.")
        return

    loop = asyncio.get_running_loop()
    sender_domains = list(set(s["domain"] for s in senders))

    async def _warmup_one_sender(sender):
        day = sender["current_day"] + 1
        limit = WARMUP_RAMP.get(day, 200)
        await log("info", f"Day {day} for {sender['email']} — {limit} emails")

        sent = 0
        for _ in range(limit):
            receiver = random.choice(receivers)
            subject, body = await loop.run_in_executor(None, generate_warmup_email)
            ok = await loop.run_in_executor(None, lambda: _send_warmup_email(
                sender["smtp_host"], sender["smtp_port"],
                sender["email"], sender["password"],
                receiver["email"], subject, body,
            ))
            if ok:
                sent += 1
                with get_conn() as conn:
                    conn.execute(
                        "INSERT INTO warmup_log (from_account, to_account, subject, sent_at) "
                        "VALUES (?,?,?,?)",
                        (sender["id"], receiver["id"], subject, datetime.datetime.now(datetime.timezone.utc).isoformat()),
                    )
            await asyncio.sleep(random.uniform(25, 75))

        await log("success", f"Sent {sent}/{limit} for {sender['email']}")

        # Update day
        with get_conn() as conn:
            new_status = "ready" if day >= 28 else "warming"
            conn.execute(
                "UPDATE warmup_accounts SET current_day=?, daily_limit=?, status=? WHERE id=?",
                (day, limit, new_status, sender["id"]),
            )
            if new_status == "ready":
                await log("success", f"[DONE] {sender['email']} warmup COMPLETE!")

    # Run all senders concurrently instead of sequentially
    await asyncio.gather(*[_warmup_one_sender(s) for s in senders])

    # Shared rescue + placement phase (once after all senders finish)
    await log("info", "Waiting 5 min then rescuing from spam…")
    await asyncio.sleep(300)

    total_rescued = 0
    for recv in receivers:
        r = await loop.run_in_executor(None, lambda rv=recv: rescue_from_spam(
            rv["imap_host"], rv["email"], rv["password"], sender_domains,
        ))
        total_rescued += r
    await log("success", f"Rescued {total_rescued} from spam")

    for recv in receivers:
        p = await loop.run_in_executor(None, lambda rv=recv: check_placement(
            rv["imap_host"], rv["email"], rv["password"], sender_domains,
        ))
        rate = p["inbox_rate"] * 100
        await log("success" if rate > 80 else "warning",
                  f"{recv['email']}: {rate:.0f}% inbox ({p['inbox']}i/{p['spam']}s)")

    await log("done", "Warmup cycle complete")


# ============================================================================
# Multi‑Channel Outreach (LinkedIn + SMS)
# ============================================================================

async def send_linkedin_message(profile_url: str, message: str) -> bool:
    """Send LinkedIn message via PhantomBuster API."""
    if not PHANTOMBUSTER_API_KEY:
        print("[WARN] PHANTOMBUSTER_API_KEY missing")
        return False
    import aiohttp
    url = "https://api.phantombuster.com/api/v2/agents/launch"
    headers = {"X-Phantombuster-Key": PHANTOMBUSTER_API_KEY}
    payload = {
        "arguments": {
            "profileUrl": profile_url,
            "message": message
        }
    }
    try:
        async with aiohttp.ClientSession() as session:
            async with session.post(url, json=payload, headers=headers, timeout=30) as resp:
                if resp.status in (200, 201):
                    print(f"[OK] LinkedIn message queued for {profile_url}")
                    return True
                else:
                    print(f"[ERROR] PhantomBuster error: {resp.status}")
                    return False
    except Exception as e:
        print(f"[ERROR] LinkedIn API error: {e}")
        return False


async def send_sms(to_phone: str, body: str) -> bool:
    """Send SMS via Twilio."""
    if not TWILIO_ACCOUNT_SID or not TWILIO_AUTH_TOKEN or not TWILIO_PHONE_FROM:
        print("[WARN] Twilio credentials missing")
        return False
    from twilio.rest import Client
    try:
        client = Client(TWILIO_ACCOUNT_SID, TWILIO_AUTH_TOKEN)
        message = client.messages.create(
            body=body,
            from_=TWILIO_PHONE_FROM,
            to=to_phone
        )
        print(f"[OK] SMS sent to {to_phone} (SID: {message.sid})")
        return True
    except Exception as e:
        print(f"[ERROR] Twilio error: {e}")
        return False