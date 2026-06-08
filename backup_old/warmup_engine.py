"""
LeadPro v3 — Email Warmup Engine
Builds sender reputation by exchanging real emails between accounts,
rescuing from spam, and tracking inbox placement.
"""
import imaplib
import smtplib
import random
import asyncio
from email.mime.text import MIMEText
from datetime import datetime
from database import get_conn
from ai_engine import generate_warmup_email

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
                for domain in sender_domains:
                    _, msg_ids = mail.search(None, f'FROM "@{domain}"')
                    if not msg_ids[0]:
                        continue
                    for mid in msg_ids[0].split():
                        mail.copy(mid, "INBOX")
                        mail.store(mid, "+FLAGS", "\\Deleted")
                        rescued += 1
                mail.expunge()
            except Exception:
                continue
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

    with get_conn() as conn:
        senders = [dict(r) for r in conn.execute(
            "SELECT * FROM warmup_accounts WHERE role='sender' AND status='warming'"
        ).fetchall()]
        receivers = [dict(r) for r in conn.execute(
            "SELECT * FROM warmup_accounts WHERE role='receiver'"
        ).fetchall()]

    if not senders or not receivers:
        await log("warning", "Need at least 1 sender + 1 receiver account.")
        return

    loop = asyncio.get_event_loop()

    for sender in senders:
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
                        (sender["id"], receiver["id"], subject, datetime.utcnow().isoformat()),
                    )
            await asyncio.sleep(random.uniform(25, 75))

        await log("success", f"Sent {sent}/{limit} for {sender['email']}")

        # Rescue phase
        await log("info", "Waiting 5 min then rescuing from spam…")
        await asyncio.sleep(300)

        sender_domains = list(set(s["domain"] for s in senders))
        total_rescued = 0
        for recv in receivers:
            r = await loop.run_in_executor(None, lambda rv=recv: rescue_from_spam(
                rv["imap_host"], rv["email"], rv["password"], sender_domains,
            ))
            total_rescued += r
        await log("success", f"Rescued {total_rescued} from spam")

        # Placement check
        for recv in receivers:
            p = await loop.run_in_executor(None, lambda rv=recv: check_placement(
                rv["imap_host"], rv["email"], rv["password"], sender_domains,
            ))
            rate = p["inbox_rate"] * 100
            await log("success" if rate > 80 else "warning",
                      f"{recv['email']}: {rate:.0f}% inbox ({p['inbox']}i/{p['spam']}s)")

        # Update day
        with get_conn() as conn:
            new_status = "ready" if day >= 28 else "warming"
            conn.execute(
                "UPDATE warmup_accounts SET current_day=?, daily_limit=?, status=? WHERE id=?",
                (day, limit, new_status, sender["id"]),
            )
            if new_status == "ready":
                await log("success", f"🎉 {sender['email']} warmup COMPLETE!")

    await log("done", "Warmup cycle complete")