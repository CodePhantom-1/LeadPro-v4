"""
LeadPro v3 — Outreach Engine
Now passes decision-maker name + dollar amounts to email generator.
"""
import smtplib, asyncio, time, base64, re, datetime
from email.mime.text import MIMEText
from email.mime.multipart import MIMEMultipart
from email.mime.base import MIMEBase
from email import encoders
from config import (YOUR_EMAIL, YOUR_APP_PASSWORD, YOUR_NAME, YOUR_COMPANY,
                    SMTP_SERVER, SMTP_PORT, EMAIL_DELAY_SEC, DAILY_EMAIL_CAP,
                    FOLLOWUP_SCHEDULE, TRACKING_DOMAIN, BASE_URL)
from database import (get_leads_for_outreach, get_due_followups,
                      get_opted_out_emails, get_hard_bounce_emails,
                      save_outreach, mark_outreach_sent, log_event, get_conn)
from ai_engine import generate_email
from audit_pages import generate_audit_page, generate_audit_preview

_HARD_BOUNCE_CODES = {550,551,552,553,554,421}

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
    def __init__(self): self._conn = None
    def _connect(self):
        s = smtplib.SMTP(SMTP_SERVER, SMTP_PORT, timeout=30)
        s.starttls(); s.login(YOUR_EMAIL, YOUR_APP_PASSWORD); self._conn = s

    def send(self, to, subject, body, outreach_id=None, attachment_path=None):
        plain, html = _inject_tracking(body, outreach_id) if outreach_id else (body,"")
        msg = MIMEMultipart("mixed")
        msg["From"] = f"{YOUR_NAME} <{YOUR_EMAIL}>"; msg["To"] = to; msg["Subject"] = subject
        footer = f"\n\n---\n{YOUR_NAME} | {YOUR_COMPANY}\nReply 'unsubscribe' to opt out."
        alt = MIMEMultipart("alternative")
        alt.attach(MIMEText(plain + footer, "plain"))
        if html:
            hf = footer.replace("\n","<br>")
            alt.attach(MIMEText(html.replace("</body>",f"<br><small>{hf}</small></body>"), "html"))
        msg.attach(alt)
        if attachment_path:
            try:
                with open(attachment_path,"rb") as f: part=MIMEBase("application","pdf"); part.set_payload(f.read())
                encoders.encode_base64(part)
                part.add_header("Content-Disposition","attachment",filename=attachment_path.split("/")[-1])
                msg.attach(part)
            except: pass
        for attempt in range(3):
            try:
                if self._conn is None: self._connect()
                self._conn.send_message(msg); return True, None
            except smtplib.SMTPResponseException as e:
                b = _classify_smtp_error(e)
                if b == "hard": return False, "hard"
                self._conn = None; time.sleep(15)
            except (smtplib.SMTPServerDisconnected, ConnectionError):
                self._conn = None
                if attempt < 2: time.sleep(5)
            except: self._conn = None; time.sleep(10) if attempt < 2 else None
        return False, "soft"

    def quit(self):
        try:
            if self._conn: self._conn.quit()
        except: pass
        self._conn = None

def test_smtp():
    try:
        s = smtplib.SMTP(SMTP_SERVER, SMTP_PORT, timeout=10); s.starttls()
        s.login(YOUR_EMAIL, YOUR_APP_PASSWORD); s.quit(); return True
    except: return False

def _emails_sent_today():
    today = datetime.date.today().isoformat()
    with get_conn() as conn:
        r = conn.execute("SELECT COUNT(*) FROM outreach WHERE channel='email' AND status='sent' AND sent_at LIKE ?", (today+"%",)).fetchone()
    return r[0] if r else 0

def _schedule_followups(lead_id, campaign_id, initial_subject, sent_at):
    base = datetime.datetime.fromisoformat(sent_at)
    for i, days in enumerate(FOLLOWUP_SCHEDULE, start=2):
        scheduled = (base + datetime.timedelta(days=days)).isoformat()
        save_outreach({"lead_id":lead_id,"campaign_id":campaign_id,"channel":"email",
                       "sequence_step":i,"subject":None,"body":None,"status":"pending",
                       "scheduled_for":scheduled,"ai_model_used":None})


async def run_initial_outreach_web(campaign_id, min_score, lead_ids, queue):
    loop = asyncio.get_event_loop()
    opted_out = get_opted_out_emails(); hard_bounce = get_hard_bounce_emails()
    sent_today = _emails_sent_today(); remaining = DAILY_EMAIL_CAP - sent_today
    if remaining <= 0:
        await queue.put({"type":"warning","message":f"Daily cap reached."}); return
    leads = get_leads_for_outreach(campaign_id, step=1, min_score=min_score)
    if lead_ids: leads = [l for l in leads if l["id"] in set(lead_ids)]
    await queue.put({"type":"info","message":f"{len(leads)} qualified | Cap: {remaining}"})
    smtp = SMTPSession(); sent_count = 0
    try:
        for lead in leads[:remaining]:
            ea = lead["email"]
            if not ea or "@" not in str(ea): continue
            if ea in opted_out or ea in hard_bounce: continue

            dm = lead.get("decision_maker")
            loss = lead.get("estimated_monthly_loss") or 0
            ops_json = lead.get("ops_pain_points")
            
            # Ensure audit page exists and is not expired
            audit_url = None
            audit_preview = None
            try:
                token = lead.get("audit_page_token")
                expiry = lead.get("audit_page_expires_at")
                if not token or (expiry and datetime.datetime.fromisoformat(expiry) < datetime.datetime.now()):
                    # Generate or regenerate audit page
                    await queue.put({"type":"info","message":f"Generating audit page for {lead['business_name']}..."})
                    token_path = await loop.run_in_executor(None, lambda: generate_audit_page(
                        lead["id"], lead, None, None, None
                    ))
                    token = token_path.split("/")[-1]  # Extract token from "/audit/{token}"
                    # Refresh lead data to get updated token
                    with get_conn() as conn:
                        updated = conn.execute("SELECT audit_page_token FROM leads WHERE id=?", (lead["id"],)).fetchone()
                        token = updated["audit_page_token"]
                
                audit_url = f"{BASE_URL}/audit/{token}"
                audit_preview = generate_audit_preview(lead, token)
            except Exception as e:
                await queue.put({"type":"warning","message":f"Audit page generation failed: {str(e)[:50]}"})
                # Continue without audit page

            await queue.put({"type":"generating",
                "message":f"{'📧 '+dm+' at ' if dm else ''}{lead['business_name']} (${loss:,}/mo loss)…",
                "lead_id":lead["id"],"business":lead["business_name"],"score":lead["lead_score"]})

            content = await loop.run_in_executor(None, lambda: generate_email(
                business_name=lead.get("business_name", ""),
                pain_points_json=lead.get("pain_points", "[]"),
                source_query=lead.get("source_query", ""),
                country=lead.get("country", ""), sequence_step=1,
                decision_maker=dm, estimated_monthly_loss=loss,
                ops_pain_points_json=ops_json,
                audit_page_url=audit_url,
                audit_preview=audit_preview,
                niche=lead.get("niche"),
            ))
            if not content:
                await queue.put({"type":"warning","message":f"AI failed for {lead['business_name']}"}); continue

            subject = content.get("subject", f"Quick question for {lead['business_name']}")
            body = content.get("body","")
            now = datetime.datetime.now().isoformat()
            oid = save_outreach({"lead_id":lead["id"],"campaign_id":campaign_id,"channel":"email",
                                 "sequence_step":1,"subject":subject,"body":body,"status":"sending",
                                 "scheduled_for":now,"ai_model_used":content.get("model_used","")})

            success, bounce_type = await loop.run_in_executor(None, lambda: smtp.send(ea, subject, body, outreach_id=oid))
            if success:
                mark_outreach_sent(oid,"sent")
                log_event(lead["id"],"emailed",f"step=1|dm={dm}|loss=${loss}")
                _schedule_followups(lead["id"], campaign_id, subject, now)
                sent_count += 1
                await queue.put({"type":"sent","message":f"✅ {lead['business_name']} → {ea}","sent_count":sent_count})
                await asyncio.sleep(EMAIL_DELAY_SEC)
            else:
                mark_outreach_sent(oid, "bounced" if bounce_type else "failed", bounce_type)
                await queue.put({"type":"error","message":f"❌ ({bounce_type or 'fail'}) → {ea}"})
    finally: smtp.quit()
    await queue.put({"type":"summary","message":f"Done. Sent: {sent_count}","sent":sent_count})


async def run_followups_web(queue):
    loop = asyncio.get_event_loop()
    opted_out = get_opted_out_emails(); hard_bounce = get_hard_bounce_emails()
    due = get_due_followups()
    if not due:
        await queue.put({"type":"info","message":"No follow-ups due."}); return
    cap_left = DAILY_EMAIL_CAP - _emails_sent_today()
    await queue.put({"type":"info","message":f"{len(due)} due | Cap: {cap_left}"})
    smtp = SMTPSession(); sent_count = 0
    try:
        for row in due[:cap_left]:
            ea = row["email"]
            if not ea or ea in opted_out or ea in hard_bounce:
                mark_outreach_sent(row["id"],"cancelled"); continue
            with get_conn() as conn:
                if conn.execute("SELECT 1 FROM outreach WHERE lead_id=? AND reply_detected=1",(row["lead_id"],)).fetchone():
                    mark_outreach_sent(row["id"],"cancelled"); continue
                orig = conn.execute("SELECT subject FROM outreach WHERE lead_id=? AND sequence_step=1 AND status='sent' LIMIT 1",(row["lead_id"],)).fetchone()
            prev_sub = orig["subject"] if orig else ""
            dm = row.get("decision_maker")
            loss = row.get("estimated_monthly_loss") or 0
            
            # Ensure audit page exists for follow-up
            audit_url = None
            audit_preview = None
            try:
                token = row.get("audit_page_token")
                expiry = row.get("audit_page_expires_at")
                if not token or (expiry and datetime.datetime.fromisoformat(expiry) < datetime.datetime.now()):
                    # Generate or regenerate audit page
                    token_path = await loop.run_in_executor(None, lambda: generate_audit_page(
                        row["lead_id"], row, None, None, None
                    ))
                    token = token_path.split("/")[-1]
                    with get_conn() as conn:
                        updated = conn.execute("SELECT audit_page_token FROM leads WHERE id=?", (row["lead_id"],)).fetchone()
                        token = updated["audit_page_token"]
                
                audit_url = f"{BASE_URL}/audit/{token}"
                audit_preview = generate_audit_preview(row, token)
            except Exception as e:
                # Continue without audit page
                pass

            content = await loop.run_in_executor(None, lambda: generate_email(
                business_name=row.get("business_name", ""),pain_points_json=row.get("pain_points", "[]") or "[]",
                source_query=row.get("source_query", "") or "",country=row.get("country", "") or "",
                sequence_step=row.get("sequence_step", 1),previous_subject=prev_sub,
                decision_maker=dm,estimated_monthly_loss=loss,
                ops_pain_points_json=row["ops_pain_points"] if "ops_pain_points" in row.keys() else None,
                audit_page_url=audit_url,
                audit_preview=audit_preview,
                niche=row.get("niche"),
            ))
            if not content: mark_outreach_sent(row["id"],"failed"); continue
            subject = content.get("subject",f"Re: {prev_sub}"); body = content.get("body","")
            with get_conn() as conn:
                conn.execute("UPDATE outreach SET subject=?, body=? WHERE id=?",(subject,body,row["id"]))
            success, bt = await loop.run_in_executor(None, lambda: smtp.send(ea, subject, body, outreach_id=row["id"]))
            mark_outreach_sent(row["id"],"sent" if success else ("bounced" if bt else "failed"), bt)
            if success:
                log_event(row["lead_id"],"emailed",f"step={row['sequence_step']}")
                sent_count += 1
                await queue.put({"type":"sent","message":f"✅ FU{row['sequence_step']} → {row['business_name']}","sent_count":sent_count})
                await asyncio.sleep(EMAIL_DELAY_SEC)
    finally: smtp.quit()
    await queue.put({"type":"summary","message":f"Follow-ups done. Sent: {sent_count}","sent":sent_count})