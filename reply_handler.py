"""
reply_handler.py — Classify replies and trigger next actions.
"""
from ai_engine import _call_ai
from database import get_conn, log_event

CATEGORIES = ["INTERESTED", "NOT_INTERESTED", "QUESTION", "OPT_OUT", "REFERRAL", "UNRELATED", "AUTO_REPLY"]


def classify_reply(reply_body: str, business_name: str) -> dict:
    """Classify a reply. Always returns a dict with category/summary/suggested_response."""
    prompt = f"""Classify this cold email reply from {business_name}.
Reply: {reply_body[:500]}
OUTPUT JSON ONLY: {{"category": one of {CATEGORIES}, "summary": "10 words max", "suggested_response": "2-sentence draft reply or null if opt-out"}}"""
    result = _call_ai(prompt, expect_json=True)
    if not isinstance(result, dict):
        return {"category": "UNRELATED", "summary": "Could not classify", "suggested_response": None}
    # Normalize category
    cat = str(result.get("category", "UNRELATED")).upper().strip()
    if cat not in CATEGORIES:
        cat = "UNRELATED"
    result["category"] = cat
    result.setdefault("summary", "")
    result.setdefault("suggested_response", None)
    return result


def handle_classified_reply(lead_id: int, outreach_id: int, reply_body: str, business_name: str):
    """Classify a reply and update the database + trigger next steps."""
    classification = classify_reply(reply_body, business_name)
    category = classification["category"]
    summary = classification.get("summary") or ""
    suggested = classification.get("suggested_response")

    with get_conn() as conn:
        # Upsert classification (unique index on outreach_id from migration 44).
        # Fall back to manual replace for older DBs where the unique index is missing.
        try:
            conn.execute(
                """INSERT INTO reply_intelligence
                    (lead_id, outreach_id, category, summary, suggested_response, raw_reply, classified_at)
                    VALUES (?,?,?,?,?,?,datetime('now'))
                    ON CONFLICT(outreach_id) DO UPDATE SET
                        category=excluded.category,
                        summary=excluded.summary,
                        suggested_response=excluded.suggested_response,
                        raw_reply=excluded.raw_reply,
                        classified_at=datetime('now')""",
                (lead_id, outreach_id, category, summary, suggested, reply_body[:2000]),
            )
        except Exception:
            conn.execute(
                "DELETE FROM reply_intelligence WHERE outreach_id=?", (outreach_id,)
            )
            conn.execute(
                """INSERT INTO reply_intelligence
                    (lead_id, outreach_id, category, summary, suggested_response, raw_reply, classified_at)
                    VALUES (?,?,?,?,?,?,datetime('now'))""",
                (lead_id, outreach_id, category, summary, suggested, reply_body[:2000]),
            )

        # Cancel pending follow-ups for non-interested or opt-out
        if category in ("OPT_OUT", "NOT_INTERESTED"):
            conn.execute(
                "UPDATE outreach SET status='cancelled' WHERE lead_id=? AND status='pending'",
                (lead_id,),
            )

        # Move lead to pipeline stage
        stage_map = {
            "INTERESTED": "replied_interested",
            "QUESTION": "replied_question",
            "NOT_INTERESTED": "replied_cold",
            "OPT_OUT": "opted_out",
            "REFERRAL": "referral",
            "AUTO_REPLY": "replied_auto",
        }
        stage = stage_map.get(category, "replied")
        conn.execute(
            "UPDATE leads SET pipeline_stage=?, last_activity_at=datetime('now') WHERE id=?",
            (stage, lead_id),
        )

    log_event(lead_id, f"reply_{category.lower()}", summary)
    return classification