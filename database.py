"""
LeadPro v3 — Database Layer
Full schema with v3 tables + ops/intent/enrichment columns.
"""
import sqlite3
import json
import queue
import threading
from contextlib import contextmanager
from datetime import datetime, date
from config import DB_PATH

# ── Simple SQLite connection pool (reuses connections to reduce overhead) ──
_pool: queue.Queue | None = None
_pool_create = threading.Lock()
_POOL_SIZE = 20


def _get_pool() -> queue.Queue:
    global _pool
    if _pool is not None:
        return _pool
    with _pool_create:
        if _pool is not None:
            return _pool
        p: queue.Queue = queue.Queue(maxsize=_POOL_SIZE)
        for _ in range(_POOL_SIZE):
            conn = sqlite3.connect(DB_PATH, timeout=30, check_same_thread=False)
            conn.row_factory = sqlite3.Row
            conn.execute("PRAGMA journal_mode=WAL")
            conn.execute("PRAGMA foreign_keys=ON")
            p.put(conn)
        _pool = p
    return _pool


POOL_GET_TIMEOUT = 15


@contextmanager
def get_conn():
    try:
        conn = _get_pool().get(timeout=POOL_GET_TIMEOUT)
    except queue.Empty:
        raise sqlite3.OperationalError("Connection pool timeout — all connections busy")
    try:
        yield conn
        conn.commit()
    except Exception:
        conn.rollback()
        try:
            conn.close()
        except Exception:
            pass
        conn = sqlite3.connect(DB_PATH, timeout=30, check_same_thread=False)
        conn.row_factory = sqlite3.Row
        conn.execute("PRAGMA journal_mode=WAL")
        conn.execute("PRAGMA foreign_keys=ON")
        raise
    finally:
        _get_pool().put(conn)

SCHEMA = """
PRAGMA journal_mode=WAL;
PRAGMA foreign_keys=ON;

CREATE TABLE IF NOT EXISTS leads (
    id                  INTEGER PRIMARY KEY AUTOINCREMENT,
    place_id            TEXT    UNIQUE NOT NULL,
    business_name       TEXT    NOT NULL,
    phone               TEXT,
    email               TEXT,
    website             TEXT,
    rating              REAL    DEFAULT 0,
    review_count        INTEGER DEFAULT 0,
    address             TEXT,
    niche               TEXT,
    city                TEXT,
    country             TEXT,

    -- Marketing audit flags
    has_website         INTEGER DEFAULT 0,
    has_ssl             INTEGER DEFAULT 0,
    is_mobile_friendly  INTEGER DEFAULT 0,
    has_tracking_pixel  INTEGER DEFAULT 0,
    has_facebook        INTEGER DEFAULT 0,
    has_instagram       INTEGER DEFAULT 0,
    has_linkedin        INTEGER DEFAULT 0,
    site_dead           INTEGER DEFAULT 0,
    uses_free_email     INTEGER DEFAULT 0,
    pagespeed_score     INTEGER DEFAULT -1,

    pain_points         TEXT,
    ideal_service       TEXT,
    lead_score          INTEGER DEFAULT 0,

    -- Operations audit (Model B)
    ops_score           INTEGER DEFAULT 0,
    ops_pain_points     TEXT,
    tech_stack_json     TEXT,
    estimated_monthly_loss INTEGER DEFAULT 0,

    -- Intent signals
    intent_score        INTEGER DEFAULT 0,
    intent_reasons      TEXT,

    -- Enrichment
    decision_maker      TEXT,
    decision_maker_title TEXT,
    dm_source           TEXT,
    dm_linkedin         TEXT,

    -- Audit page
    audit_page_token    TEXT,
    audit_page_generated_at TEXT,
    audit_page_expires_at TEXT,

    -- Meta
    source_query        TEXT,
    campaign_id         INTEGER,
    pipeline_stage      TEXT DEFAULT 'new',
    competitor_count    INTEGER DEFAULT 0,
    avg_competitor_score REAL   DEFAULT 0,
    best_keyword_pos    INTEGER,

    scraped_at          TEXT    DEFAULT (datetime('now')),
    last_updated        TEXT    DEFAULT (datetime('now'))
);

CREATE TABLE IF NOT EXISTS campaigns (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    name        TEXT    NOT NULL,
    country     TEXT,
    created_at  TEXT    DEFAULT (datetime('now')),
    status      TEXT    DEFAULT 'active'
);

CREATE TABLE IF NOT EXISTS outreach (
    id              INTEGER PRIMARY KEY AUTOINCREMENT,
    lead_id         INTEGER NOT NULL REFERENCES leads(id),
    campaign_id     INTEGER REFERENCES campaigns(id),
    channel         TEXT    NOT NULL,
    sequence_step   INTEGER DEFAULT 1,
    subject         TEXT,
    body            TEXT,
    sent_at         TEXT,
    status          TEXT    DEFAULT 'pending',
    bounce_type     TEXT,
    scheduled_for   TEXT,
    ai_model_used   TEXT,
    open_tracked    INTEGER DEFAULT 0,
    opened_at       TEXT,
    clicked_at      TEXT,
    reply_detected  INTEGER DEFAULT 0,
    proposal_id     INTEGER,
    domain_used     TEXT
);

CREATE TABLE IF NOT EXISTS events (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    lead_id     INTEGER REFERENCES leads(id),
    event_type  TEXT,
    note        TEXT,
    created_at  TEXT    DEFAULT (datetime('now'))
);

CREATE TABLE IF NOT EXISTS seo_rankings (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    lead_id     INTEGER NOT NULL REFERENCES leads(id),
    keyword     TEXT    NOT NULL,
    position    INTEGER,
    serp_url    TEXT,
    checked_at  TEXT    DEFAULT (datetime('now'))
);

CREATE TABLE IF NOT EXISTS competitors (
    id                  INTEGER PRIMARY KEY AUTOINCREMENT,
    lead_id             INTEGER NOT NULL REFERENCES leads(id),
    place_id            TEXT,
    name                TEXT    NOT NULL,
    website             TEXT,
    rating              REAL    DEFAULT 0,
    review_count        INTEGER DEFAULT 0,
    has_ssl             INTEGER DEFAULT 0,
    is_mobile_friendly  INTEGER DEFAULT 0,
    has_tracking_pixel  INTEGER DEFAULT 0,
    has_facebook        INTEGER DEFAULT 0,
    has_instagram       INTEGER DEFAULT 0,
    has_linkedin        INTEGER DEFAULT 0,
    pagespeed_score     INTEGER DEFAULT -1,
    pain_points         TEXT,
    found_at            TEXT    DEFAULT (datetime('now')),
    UNIQUE(lead_id, place_id)
);

CREATE TABLE IF NOT EXISTS tracking_events (
    id              INTEGER PRIMARY KEY AUTOINCREMENT,
    outreach_id     INTEGER REFERENCES outreach(id),
    event_type      TEXT,
    ip              TEXT,
    user_agent      TEXT,
    created_at      TEXT    DEFAULT (datetime('now'))
);

CREATE TABLE IF NOT EXISTS proposals (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    lead_id     INTEGER NOT NULL REFERENCES leads(id),
    pdf_path    TEXT,
    page_path   TEXT,
    sent_via    TEXT,
    outreach_id INTEGER REFERENCES outreach(id),
    created_at  TEXT    DEFAULT (datetime('now'))
);

CREATE TABLE IF NOT EXISTS outreach_accounts (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    email       TEXT    UNIQUE NOT NULL,
    password    TEXT    NOT NULL,
    smtp_host   TEXT    DEFAULT 'smtp.gmail.com',
    smtp_port   INTEGER DEFAULT 587,
    imap_host   TEXT    DEFAULT 'imap.gmail.com',
    display_name TEXT,
    daily_limit INTEGER DEFAULT 150,
    active      INTEGER DEFAULT 1,
    created_at  TEXT    DEFAULT (datetime('now'))
);

CREATE TABLE IF NOT EXISTS warmup_accounts (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    email       TEXT    UNIQUE NOT NULL,
    password    TEXT    NOT NULL,
    smtp_host   TEXT    DEFAULT 'smtp.gmail.com',
    smtp_port   INTEGER DEFAULT 587,
    imap_host   TEXT    DEFAULT 'imap.gmail.com',
    domain      TEXT    NOT NULL,
    role        TEXT    DEFAULT 'sender',
    daily_limit INTEGER DEFAULT 10,
    current_day INTEGER DEFAULT 0,
    started_at  TEXT    DEFAULT (datetime('now')),
    status      TEXT    DEFAULT 'warming'
);

CREATE TABLE IF NOT EXISTS warmup_log (
    id              INTEGER PRIMARY KEY AUTOINCREMENT,
    from_account    INTEGER REFERENCES warmup_accounts(id),
    to_account      INTEGER REFERENCES warmup_accounts(id),
    subject         TEXT,
    sent_at         TEXT,
    landed_in       TEXT,
    rescued         INTEGER DEFAULT 0
);


-- Lead tags (many-to-many)
CREATE TABLE IF NOT EXISTS lead_tags (
    lead_id   INTEGER NOT NULL REFERENCES leads(id) ON DELETE CASCADE,
    tag       TEXT NOT NULL,
    PRIMARY KEY (lead_id, tag)
);

-- Per-lead notes (CRM)
CREATE TABLE IF NOT EXISTS lead_notes (
    id         INTEGER PRIMARY KEY AUTOINCREMENT,
    lead_id    INTEGER NOT NULL REFERENCES leads(id),
    note       TEXT NOT NULL,
    author     TEXT DEFAULT 'system',
    created_at TEXT DEFAULT (datetime('now'))
);

-- AI reply classification results
CREATE TABLE IF NOT EXISTS reply_intelligence (
    id                INTEGER PRIMARY KEY AUTOINCREMENT,
    lead_id           INTEGER REFERENCES leads(id),
    outreach_id       INTEGER REFERENCES outreach(id),
    category          TEXT,   -- INTERESTED / NOT_INTERESTED / QUESTION / OPT_OUT / REFERRAL
    summary           TEXT,
    suggested_response TEXT,
    raw_reply         TEXT,
    classified_at     TEXT DEFAULT (datetime('now'))
);

-- A/B test tracking
CREATE TABLE IF NOT EXISTS ab_tests (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    outreach_id INTEGER REFERENCES outreach(id),
    variant     TEXT,    -- 'A' or 'B'
    subject     TEXT,
    opened      INTEGER DEFAULT 0,
    clicked     INTEGER DEFAULT 0,
    replied     INTEGER DEFAULT 0,
    created_at  TEXT DEFAULT (datetime('now'))
);

-- Scheduled tasks (for one-time future sends)
CREATE TABLE IF NOT EXISTS scheduled_tasks (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    task_type   TEXT NOT NULL,    -- 'send_email', 'generate_audit', 'run_campaign'
    payload     TEXT,             -- JSON
    run_at      TEXT NOT NULL,
    status      TEXT DEFAULT 'pending',
    result      TEXT,
    created_at  TEXT DEFAULT (datetime('now'))
);

-- FTS5 full-text search across leads
CREATE VIRTUAL TABLE IF NOT EXISTS leads_fts USING fts5(
    business_name, email, city, niche, pain_points,
    content='leads', content_rowid='id'
);

CREATE INDEX IF NOT EXISTS idx_leads_country    ON leads(country);
CREATE INDEX IF NOT EXISTS idx_leads_score      ON leads(lead_score DESC);
CREATE INDEX IF NOT EXISTS idx_leads_email      ON leads(email);
CREATE INDEX IF NOT EXISTS idx_leads_ops        ON leads(ops_score DESC);
CREATE INDEX IF NOT EXISTS idx_leads_intent     ON leads(intent_score DESC);
CREATE INDEX IF NOT EXISTS idx_leads_loss       ON leads(estimated_monthly_loss DESC);
CREATE INDEX IF NOT EXISTS idx_leads_token      ON leads(audit_page_token);
CREATE INDEX IF NOT EXISTS idx_outreach_status  ON outreach(status);
CREATE INDEX IF NOT EXISTS idx_outreach_sched   ON outreach(scheduled_for);
CREATE INDEX IF NOT EXISTS idx_seo_lead         ON seo_rankings(lead_id);
CREATE INDEX IF NOT EXISTS idx_comp_lead        ON competitors(lead_id);
CREATE INDEX IF NOT EXISTS idx_tracking_oid     ON tracking_events(outreach_id);

-- Composite indexes for the most common query
CREATE INDEX IF NOT EXISTS idx_leads_score_email ON leads(lead_score DESC, email);
CREATE INDEX IF NOT EXISTS idx_reply_lead        ON reply_intelligence(lead_id);
CREATE INDEX IF NOT EXISTS idx_tasks_run_at      ON scheduled_tasks(run_at, status);
"""

SCHEMA_MIGRATIONS = [
    (1, "ALTER TABLE outreach ADD COLUMN bounce_type TEXT"),
    (2, "ALTER TABLE outreach ADD COLUMN opened_at TEXT"),
    (3, "ALTER TABLE outreach ADD COLUMN clicked_at TEXT"),
    (4, "ALTER TABLE outreach ADD COLUMN proposal_id INTEGER"),
    (5, "ALTER TABLE outreach ADD COLUMN domain_used TEXT"),
    (6, "ALTER TABLE leads ADD COLUMN campaign_id INTEGER"),
    (7, "ALTER TABLE leads ADD COLUMN competitor_count INTEGER DEFAULT 0"),
    (8, "ALTER TABLE leads ADD COLUMN avg_competitor_score REAL DEFAULT 0"),
    (9, "ALTER TABLE leads ADD COLUMN best_keyword_pos INTEGER"),
    (10, "ALTER TABLE leads ADD COLUMN ops_score INTEGER DEFAULT 0"),
    (11, "ALTER TABLE leads ADD COLUMN ops_pain_points TEXT"),
    (12, "ALTER TABLE leads ADD COLUMN tech_stack_json TEXT"),
    (13, "ALTER TABLE leads ADD COLUMN estimated_monthly_loss INTEGER DEFAULT 0"),
    (14, "ALTER TABLE leads ADD COLUMN intent_score INTEGER DEFAULT 0"),
    (15, "ALTER TABLE leads ADD COLUMN intent_reasons TEXT"),
    (16, "ALTER TABLE leads ADD COLUMN decision_maker TEXT"),
    (17, "ALTER TABLE leads ADD COLUMN decision_maker_title TEXT"),
    (18, "ALTER TABLE leads ADD COLUMN dm_source TEXT"),
    (19, "ALTER TABLE leads ADD COLUMN dm_linkedin TEXT"),
    (20, "ALTER TABLE leads ADD COLUMN audit_page_token TEXT"),
    (21, "ALTER TABLE proposals ADD COLUMN page_path TEXT"),
    (22, "ALTER TABLE leads ADD COLUMN audit_page_generated_at TEXT"),
    (23, "ALTER TABLE leads ADD COLUMN audit_page_expires_at TEXT"),
    (24, "ALTER TABLE outreach ADD COLUMN outreach_account_id INTEGER"),
    # v4 migrations
    (25, "ALTER TABLE leads ADD COLUMN pipeline_stage TEXT DEFAULT 'new'"),
    (26, "ALTER TABLE leads ADD COLUMN assigned_to TEXT"),
    (27, "ALTER TABLE leads ADD COLUMN last_activity_at TEXT"),
    (28, "CREATE TABLE IF NOT EXISTS lead_tags (lead_id INTEGER NOT NULL REFERENCES leads(id) ON DELETE CASCADE, tag TEXT NOT NULL, PRIMARY KEY (lead_id, tag))"),
    (29, "CREATE TABLE IF NOT EXISTS lead_notes (id INTEGER PRIMARY KEY AUTOINCREMENT, lead_id INTEGER NOT NULL REFERENCES leads(id), note TEXT NOT NULL, author TEXT DEFAULT 'system', created_at TEXT DEFAULT (datetime('now')))"),
    (30, "CREATE TABLE IF NOT EXISTS reply_intelligence (id INTEGER PRIMARY KEY AUTOINCREMENT, lead_id INTEGER REFERENCES leads(id), outreach_id INTEGER REFERENCES outreach(id), category TEXT, summary TEXT, suggested_response TEXT, raw_reply TEXT, classified_at TEXT DEFAULT (datetime('now')))"),
    (31, "CREATE TABLE IF NOT EXISTS ab_tests (id INTEGER PRIMARY KEY AUTOINCREMENT, outreach_id INTEGER REFERENCES outreach(id), variant TEXT, subject TEXT, opened INTEGER DEFAULT 0, clicked INTEGER DEFAULT 0, replied INTEGER DEFAULT 0, created_at TEXT DEFAULT (datetime('now')))"),
    (32, "CREATE TABLE IF NOT EXISTS scheduled_tasks (id INTEGER PRIMARY KEY AUTOINCREMENT, task_type TEXT NOT NULL, payload TEXT, run_at TEXT NOT NULL, status TEXT DEFAULT 'pending', result TEXT, created_at TEXT DEFAULT (datetime('now')))"),
    (33, "CREATE VIRTUAL TABLE IF NOT EXISTS leads_fts USING fts5(business_name, email, city, niche, pain_points, content='leads', content_rowid='id')"),
    (34, "CREATE INDEX IF NOT EXISTS idx_leads_score_email ON leads(lead_score DESC, email)"),
    (35, "CREATE INDEX IF NOT EXISTS idx_leads_stage ON leads(pipeline_stage)"),
    (36, "CREATE INDEX IF NOT EXISTS idx_reply_lead ON reply_intelligence(lead_id)"),
    (37, "CREATE INDEX IF NOT EXISTS idx_tasks_run_at ON scheduled_tasks(run_at, status)"),
    (38, """
        CREATE TRIGGER IF NOT EXISTS leads_ai AFTER INSERT ON leads
        BEGIN
            INSERT INTO leads_fts(rowid, business_name, email, city, niche, pain_points)
            VALUES (new.id, new.business_name, new.email, new.city, new.niche, new.pain_points);
        END
    """),
    (39, """
        CREATE TRIGGER IF NOT EXISTS leads_au AFTER UPDATE ON leads
        BEGIN
            UPDATE leads_fts SET
                business_name = new.business_name,
                email = new.email,
                city = new.city,
                niche = new.niche,
                pain_points = new.pain_points
            WHERE rowid = old.id;
        END
    """),
    (40, """
        CREATE TRIGGER IF NOT EXISTS leads_ad AFTER DELETE ON leads
        BEGIN
            DELETE FROM leads_fts WHERE rowid = old.id;
        END
    """),
    (41, "INSERT INTO leads_fts(rowid, business_name, email, city, niche, pain_points) SELECT id, business_name, email, city, niche, pain_points FROM leads WHERE id NOT IN (SELECT rowid FROM leads_fts)"),
    # v4.1 — ensure proposals has a reliable upsert key
    (42, "ALTER TABLE proposals ADD COLUMN kind TEXT DEFAULT 'pdf'"),
    # Dedupe (lead_id, kind) pairs before adding the UNIQUE index so the
    # migration is safe on databases that already have duplicate rows.
    (43, "DELETE FROM proposals WHERE id NOT IN (SELECT MIN(id) FROM proposals GROUP BY lead_id, COALESCE(kind, 'pdf'))"),
    (48, "UPDATE proposals SET kind = 'pdf' WHERE kind IS NULL"),
    (49, "CREATE UNIQUE INDEX IF NOT EXISTS idx_proposals_lead_kind ON proposals(lead_id, kind)"),
    # v4.1 — ensure reply_intelligence is deduped per outreach
    (44, "CREATE UNIQUE INDEX IF NOT EXISTS idx_reply_intel_outreach ON reply_intelligence(outreach_id) WHERE outreach_id IS NOT NULL"),
    # v4.1 — users table (proper auth)
    (45, """
        CREATE TABLE IF NOT EXISTS users (
            id              INTEGER PRIMARY KEY AUTOINCREMENT,
            username        TEXT UNIQUE NOT NULL,
            password_hash   TEXT NOT NULL,
            role            TEXT DEFAULT 'admin',
            created_at      TEXT DEFAULT (datetime('now')),
            last_login_at   TEXT
        )
    """),
    (46, "ALTER TABLE outreach_accounts ADD COLUMN sending_mode TEXT DEFAULT 'smtp_password'"),
    (47, "ALTER TABLE outreach_accounts ADD COLUMN signature TEXT"),
]

def run_migrations():
    """Track and apply migrations idempotently using a versions table.

    Migrations that duplicate already-existing columns are treated as
    successfully applied (idempotent) — we record the version so the
    noise stops on subsequent startups.
    """
    with get_conn() as conn:
        conn.execute("CREATE TABLE IF NOT EXISTS schema_versions (version INTEGER PRIMARY KEY, applied_at TEXT)")
        applied = {r[0] for r in conn.execute("SELECT version FROM schema_versions").fetchall()}

    for version, sql in SCHEMA_MIGRATIONS:
        if version in applied:
            continue
        try:
            with get_conn() as conn:
                # executescript supports multi-statement / trigger bodies
                conn.executescript(sql)
                conn.execute("INSERT INTO schema_versions VALUES (?,datetime('now'))", (version,))
            print(f"[OK] Migration {version} applied")
        except Exception as e:
            msg = str(e).lower()
            # Idempotent failures: column/table/index/trigger already exists
            benign = (
                "duplicate column name" in msg
                or "already exists" in msg
            )
            if benign:
                try:
                    with get_conn() as conn:
                        conn.execute("INSERT INTO schema_versions VALUES (?,datetime('now'))", (version,))
                    print(f"[OK] Migration {version} already applied (marked)")
                except Exception:
                    pass
            else:
                print(f"[WARN] Migration {version} failed: {e}")


def init_db():
    with get_conn() as conn:
        conn.executescript(SCHEMA)
    run_migrations()
    print(f"[OK] Database ready: {DB_PATH}")


def get_existing_place_ids() -> set:
    with get_conn() as conn:
        return {r["place_id"] for r in conn.execute("SELECT place_id FROM leads").fetchall()}


def get_existing_emails() -> set:
    with get_conn() as conn:
        return {r["email"].lower().strip() for r in conn.execute("SELECT email FROM leads WHERE email IS NOT NULL AND email != ''").fetchall()}


def get_opted_out_emails() -> set:
    with get_conn() as conn:
        rows = conn.execute(
            "SELECT DISTINCT l.email FROM leads l JOIN events e ON e.lead_id=l.id WHERE e.event_type='opted_out'"
        ).fetchall()
    return {r["email"] for r in rows if r["email"]}


def get_hard_bounce_emails() -> set:
    with get_conn() as conn:
        rows = conn.execute(
            "SELECT DISTINCT l.email FROM leads l JOIN outreach o ON o.lead_id=l.id WHERE o.bounce_type='hard'"
        ).fetchall()
    return {r["email"] for r in rows if r["email"]}


def upsert_leads(leads: list[dict]):
    """Insert or update leads keyed by place_id.

    On conflict we UPDATE all audit-relevant columns so re-audits / enrichment
    refresh existing rows rather than being silently dropped.
    """
    sql = """
    INSERT INTO leads (
        place_id, business_name, phone, email, website, rating, review_count,
        address, niche, city, country,
        has_website, has_ssl, is_mobile_friendly, has_tracking_pixel,
        has_facebook, has_instagram, has_linkedin, site_dead, uses_free_email,
        pagespeed_score, pain_points, ideal_service, lead_score, source_query,
        ops_score, ops_pain_points, tech_stack_json, estimated_monthly_loss,
        intent_score, intent_reasons,
        decision_maker, decision_maker_title, dm_source
    ) VALUES (
        :place_id, :business_name, :phone, :email, :website, :rating,
        :review_count, :address, :niche, :city, :country,
        :has_website, :has_ssl, :is_mobile_friendly, :has_tracking_pixel,
        :has_facebook, :has_instagram, :has_linkedin, :site_dead,
        :uses_free_email, :pagespeed_score, :pain_points, :ideal_service,
        :lead_score, :source_query,
        :ops_score, :ops_pain_points, :tech_stack_json,
        :estimated_monthly_loss,
        :intent_score, :intent_reasons,
        :decision_maker, :decision_maker_title, :dm_source
    )
    ON CONFLICT(place_id) DO UPDATE SET
        business_name           = excluded.business_name,
        phone                   = COALESCE(NULLIF(excluded.phone, 'N/A'), leads.phone),
        email                   = COALESCE(NULLIF(excluded.email, 'N/A'), leads.email),
        website                 = COALESCE(NULLIF(excluded.website, ''), leads.website),
        rating                  = excluded.rating,
        review_count            = excluded.review_count,
        address                 = COALESCE(NULLIF(excluded.address, ''), leads.address),
        niche                   = COALESCE(NULLIF(excluded.niche, ''), leads.niche),
        city                    = COALESCE(NULLIF(excluded.city, ''), leads.city),
        country                 = COALESCE(NULLIF(excluded.country, ''), leads.country),
        has_website             = excluded.has_website,
        has_ssl                 = excluded.has_ssl,
        is_mobile_friendly      = excluded.is_mobile_friendly,
        has_tracking_pixel      = excluded.has_tracking_pixel,
        has_facebook            = excluded.has_facebook,
        has_instagram           = excluded.has_instagram,
        has_linkedin            = excluded.has_linkedin,
        site_dead               = excluded.site_dead,
        uses_free_email         = excluded.uses_free_email,
        pagespeed_score         = excluded.pagespeed_score,
        pain_points             = excluded.pain_points,
        ideal_service           = excluded.ideal_service,
        lead_score              = excluded.lead_score,
        source_query            = COALESCE(NULLIF(excluded.source_query, ''), leads.source_query),
        ops_score               = excluded.ops_score,
        ops_pain_points         = excluded.ops_pain_points,
        tech_stack_json         = excluded.tech_stack_json,
        estimated_monthly_loss  = excluded.estimated_monthly_loss,
        intent_score            = excluded.intent_score,
        intent_reasons          = excluded.intent_reasons,
        decision_maker          = COALESCE(excluded.decision_maker, leads.decision_maker),
        decision_maker_title    = COALESCE(excluded.decision_maker_title, leads.decision_maker_title),
        dm_source               = COALESCE(excluded.dm_source, leads.dm_source),
        last_updated            = datetime('now')
    """
    # Ensure all fields exist with defaults
    defaults = {
        "place_id": "", "business_name": "", "phone": "N/A",
        "email": "N/A", "website": "", "rating": 0, "review_count": 0,
        "address": "", "niche": "", "city": "", "country": "",
        "has_website": 0, "has_ssl": 0, "is_mobile_friendly": 0,
        "has_tracking_pixel": 0, "has_facebook": 0, "has_instagram": 0,
        "has_linkedin": 0, "site_dead": 0, "uses_free_email": 0,
        "pagespeed_score": -1, "pain_points": "[]", "ideal_service": "",
        "lead_score": 0, "source_query": "",
        "ops_score": 0, "ops_pain_points": "[]", "tech_stack_json": "{}",
        "estimated_monthly_loss": 0,
        "intent_score": 0, "intent_reasons": "[]",
        "decision_maker": None, "decision_maker_title": None,
        "dm_source": None,
    }
    cleaned = []
    for lead in leads:
        row = {k: lead.get(k, defaults[k]) for k in defaults}
        if not row["place_id"]:
            continue
        cleaned.append(row)

    with get_conn() as conn:
        conn.executemany(sql, cleaned)


def save_outreach(record: dict) -> int:
    sql = """
    INSERT INTO outreach (lead_id, campaign_id, channel, sequence_step,
                          subject, body, status, scheduled_for, ai_model_used)
    VALUES (:lead_id, :campaign_id, :channel, :sequence_step,
            :subject, :body, :status, :scheduled_for, :ai_model_used)
    """
    with get_conn() as conn:
        cur = conn.execute(sql, record)
        return cur.lastrowid


def mark_outreach_sent(outreach_id: int, status: str = "sent",
                        bounce_type: str | None = None):
    with get_conn() as conn:
        conn.execute(
            "UPDATE outreach SET status=?, sent_at=datetime('now'), bounce_type=? WHERE id=?",
            (status, bounce_type, outreach_id))


def log_event(lead_id: int, event_type: str, note: str = ""):
    with get_conn() as conn:
        conn.execute("INSERT INTO events (lead_id, event_type, note) VALUES (?,?,?)",
                     (lead_id, event_type, note))


MAX_OUTREACH_BATCH = 500


def get_leads_for_outreach(campaign_id: int, step: int = 1,
                            min_score: int = 20) -> list[sqlite3.Row]:
    sql = """
    SELECT l.* FROM leads l
    WHERE l.lead_score >= ?
      AND l.email IS NOT NULL AND l.email != '' AND l.email != 'N/A'
      AND (l.campaign_id IS NULL OR l.campaign_id = ?)
      AND l.id NOT IN (
          SELECT lead_id FROM outreach
          WHERE campaign_id=? AND sequence_step=? AND status!='failed'
      )
    ORDER BY l.lead_score DESC, l.intent_score DESC, COALESCE(l.estimated_monthly_loss, 0) DESC
    LIMIT ?
    """
    with get_conn() as conn:
        return conn.execute(sql, (min_score, campaign_id, campaign_id, step, MAX_OUTREACH_BATCH)).fetchall()


MAX_FOLLOWUP_BATCH = 200


def get_due_followups() -> list[sqlite3.Row]:
    sql = """
    SELECT o.*, l.email, l.business_name, l.ideal_service, l.pain_points,
           l.source_query, l.country, l.id as lead_id,
           l.decision_maker, l.estimated_monthly_loss, l.ops_pain_points,
           l.audit_page_token, l.audit_page_expires_at, l.niche
    FROM outreach o JOIN leads l ON l.id=o.lead_id
    WHERE o.status='pending' AND o.scheduled_for<=datetime('now') AND o.sequence_step>1
    ORDER BY o.scheduled_for ASC
    LIMIT ?
    """
    with get_conn() as conn:
        return conn.execute(sql, (MAX_FOLLOWUP_BATCH,)).fetchall()


def get_stats() -> dict:
    with get_conn() as conn:
        total      = conn.execute("SELECT COUNT(*) FROM leads").fetchone()[0]
        emailed    = conn.execute("SELECT COUNT(*) FROM outreach WHERE status='sent'").fetchone()[0]
        replied    = conn.execute("SELECT COUNT(*) FROM outreach WHERE reply_detected=1").fetchone()[0]
        high_score = conn.execute("SELECT COUNT(*) FROM leads WHERE lead_score>=60").fetchone()[0]
        bounced    = conn.execute("SELECT COUNT(*) FROM outreach WHERE status='bounced'").fetchone()[0]
        opted_out  = conn.execute("SELECT COUNT(DISTINCT lead_id) FROM events WHERE event_type='opted_out'").fetchone()[0]
        opened     = conn.execute("SELECT COUNT(*) FROM outreach WHERE open_tracked=1").fetchone()[0]
        hot_intent = conn.execute("SELECT COUNT(*) FROM leads WHERE intent_score>=60").fetchone()[0]
        total_loss = conn.execute("SELECT SUM(estimated_monthly_loss) FROM leads").fetchone()[0] or 0
        by_service = conn.execute("SELECT ideal_service, COUNT(*) as n FROM leads GROUP BY ideal_service ORDER BY n DESC LIMIT 8").fetchall()
        by_country = conn.execute("SELECT country, COUNT(*) as n FROM leads GROUP BY country ORDER BY n DESC LIMIT 6").fetchall()
    return {
        "total_leads": total, "emailed": emailed, "replied": replied,
        "bounced": bounced, "opened": opened, "opted_out": opted_out,
        "high_score_leads": high_score, "hot_intent": hot_intent,
        "total_monthly_loss": total_loss,
        "reply_rate": round(replied / emailed, 4) if emailed else 0,
        "open_rate": round(opened / emailed, 4) if emailed else 0,
        "by_service": [dict(r) for r in by_service],
        "by_country": [dict(r) for r in by_country],
    }


def create_campaign(name: str, country: str) -> int:
    with get_conn() as conn:
        cur = conn.execute("INSERT INTO campaigns (name, country) VALUES (?,?)", (name, country))
        return cur.lastrowid


def get_outreach_accounts() -> list:
    """Return all active outreach accounts with sent-today counts."""
    from config import decrypt_password
    today = date.today().isoformat()
    with get_conn() as conn:
        accounts = [dict(r) for r in conn.execute(
            "SELECT * FROM outreach_accounts WHERE active=1 ORDER BY id"
        ).fetchall()]
        for acct in accounts:
            acct["password"] = decrypt_password(acct["password"])
            sent = conn.execute(
                "SELECT COUNT(*) FROM outreach WHERE outreach_account_id=? AND status='sent' AND sent_at LIKE ?",
                (acct["id"], today + "%")
            ).fetchone()[0]
            acct["sent_today"] = sent
            acct["remaining"] = max(0, acct["daily_limit"] - sent)
            acct.setdefault("sending_mode", "smtp_password")
            acct.setdefault("signature", None)
    return accounts


def pick_outreach_account() -> dict | None:
    """
    Pick the best account for the next send.
    Strategy: account with most remaining capacity today.
    Falls back to None if no accounts configured (uses legacy config).
    """
    accounts = get_outreach_accounts()
    if not accounts:
        return None
    # Filter to accounts with capacity
    available = [a for a in accounts if a["remaining"] > 0]
    if not available:
        return None
    # Pick the one with most remaining capacity
    return max(available, key=lambda a: a["remaining"])


# ==================== v4 Helper Functions ====================

def search_leads_fulltext(query: str, limit: int = 50) -> list[sqlite3.Row]:
    """Search across business_name, email, city, niche, pain_points using FTS5."""
    with get_conn() as conn:
        sql = """
        SELECT l.*, snippet(leads_fts, 0, '<b>', '</b>', '…', 20) as snippet
        FROM leads l JOIN leads_fts fts ON l.id=fts.rowid
        WHERE leads_fts MATCH ? ORDER BY rank
        LIMIT ?
        """
        return conn.execute(sql, (query, limit)).fetchall()


def add_lead_tag(lead_id: int, tag: str):
    """Add a tag to a lead (many-to-many)."""
    with get_conn() as conn:
        conn.execute(
            "INSERT OR IGNORE INTO lead_tags (lead_id, tag) VALUES (?,?)",
            (lead_id, tag)
        )


def remove_lead_tag(lead_id: int, tag: str):
    """Remove a tag from a lead."""
    with get_conn() as conn:
        conn.execute(
            "DELETE FROM lead_tags WHERE lead_id=? AND tag=?",
            (lead_id, tag)
        )


def get_lead_tags(lead_id: int) -> list[str]:
    """Get all tags for a lead."""
    with get_conn() as conn:
        rows = conn.execute(
            "SELECT tag FROM lead_tags WHERE lead_id=? ORDER BY tag",
            (lead_id,)
        ).fetchall()
        return [r["tag"] for r in rows]


def add_lead_note(lead_id: int, note: str, author: str = "system"):
    """Add a note to a lead (CRM)."""
    with get_conn() as conn:
        conn.execute(
            "INSERT INTO lead_notes (lead_id, note, author) VALUES (?,?,?)",
            (lead_id, note, author)
        )


def get_lead_notes(lead_id: int) -> list[sqlite3.Row]:
    """Get all notes for a lead."""
    with get_conn() as conn:
        return conn.execute(
            "SELECT * FROM lead_notes WHERE lead_id=? ORDER BY created_at DESC",
            (lead_id,)
        ).fetchall()


def save_reply_intelligence(
    lead_id: int,
    outreach_id: int | None,
    category: str,
    summary: str,
    suggested_response: str,
    raw_reply: str
) -> int:
    """Save AI-classified reply."""
    with get_conn() as conn:
        cur = conn.execute(
            """
            INSERT INTO reply_intelligence
            (lead_id, outreach_id, category, summary, suggested_response, raw_reply)
            VALUES (?,?,?,?,?,?)
            ON CONFLICT(outreach_id) DO UPDATE SET
                category=excluded.category,
                summary=excluded.summary,
                suggested_response=excluded.suggested_response,
                raw_reply=excluded.raw_reply,
                classified_at=datetime('now')
            """,
            (lead_id, outreach_id, category, summary, suggested_response, raw_reply)
        )
        return cur.lastrowid


def get_reply_intelligence(lead_id: int) -> list[sqlite3.Row]:
    """Get all reply intelligence for a lead."""
    with get_conn() as conn:
        return conn.execute(
            "SELECT * FROM reply_intelligence WHERE lead_id=? ORDER BY classified_at DESC",
            (lead_id,)
        ).fetchall()


def create_ab_test(outreach_id: int, variant: str, subject: str) -> int:
    """Create an A/B test entry."""
    with get_conn() as conn:
        cur = conn.execute(
            "INSERT INTO ab_tests (outreach_id, variant, subject) VALUES (?,?,?)",
            (outreach_id, variant, subject)
        )
        return cur.lastrowid


def update_ab_test_metrics(test_id: int, opened: bool = False, clicked: bool = False, replied: bool = False):
    """Increment A/B test metrics."""
    updates = []
    params = []
    if opened:
        updates.append("opened = opened + 1")
    if clicked:
        updates.append("clicked = clicked + 1")
    if replied:
        updates.append("replied = replied + 1")
    if not updates:
        return
    sql = f"UPDATE ab_tests SET {', '.join(updates)} WHERE id=?"
    with get_conn() as conn:
        conn.execute(sql, (test_id,))


def schedule_task(task_type: str, payload: dict, run_at: str) -> int:
    """Schedule a one-time task."""
    with get_conn() as conn:
        cur = conn.execute(
            "INSERT INTO scheduled_tasks (task_type, payload, run_at) VALUES (?,?,?)",
            (task_type, json.dumps(payload), run_at)
        )
        return cur.lastrowid


def get_pending_tasks(limit: int = 100) -> list[sqlite3.Row]:
    """Get tasks due now or earlier."""
    with get_conn() as conn:
        return conn.execute(
            "SELECT * FROM scheduled_tasks WHERE status='pending' AND run_at <= datetime('now') ORDER BY run_at LIMIT ?",
            (limit,)
        ).fetchall()


def mark_task_completed(task_id: int, result: str = ""):
    """Mark a scheduled task as completed."""
    with get_conn() as conn:
        conn.execute(
            "UPDATE scheduled_tasks SET status='completed', result=? WHERE id=?",
            (result, task_id)
        )


def update_pipeline_stage(lead_id: int, stage: str):
    """Update lead's pipeline stage and set last_activity_at."""
    with get_conn() as conn:
        conn.execute(
            "UPDATE leads SET pipeline_stage=?, last_activity_at=datetime('now') WHERE id=?",
            (stage, lead_id)
        )


def assign_lead(lead_id: int, assigned_to: str):
    """Assign a lead to a user."""
    with get_conn() as conn:
        conn.execute(
            "UPDATE leads SET assigned_to=?, last_activity_at=datetime('now') WHERE id=?",
            (assigned_to, lead_id)
        )


def record_activity(lead_id: int):
    """Update last_activity_at timestamp."""
    with get_conn() as conn:
        conn.execute(
            "UPDATE leads SET last_activity_at=datetime('now') WHERE id=?",
            (lead_id,)
        )


def get_leads_by_stage(stage: str, limit: int = 100) -> list[sqlite3.Row]:
    """Get leads filtered by pipeline stage."""
    with get_conn() as conn:
        return conn.execute(
            "SELECT * FROM leads WHERE pipeline_stage=? ORDER BY last_activity_at DESC LIMIT ?",
            (stage, limit)
        ).fetchall()


def get_assigned_leads(user: str) -> list[sqlite3.Row]:
    """Get leads assigned to a specific user."""
    with get_conn() as conn:
        return conn.execute(
            "SELECT * FROM leads WHERE assigned_to=? ORDER BY last_activity_at DESC",
            (user,)
        ).fetchall()


def get_lead_funnel() -> dict:
    """Count leads per pipeline stage."""
    with get_conn() as conn:
        rows = conn.execute(
            "SELECT pipeline_stage, COUNT(*) as count FROM leads GROUP BY pipeline_stage"
        ).fetchall()
        return {r["pipeline_stage"]: r["count"] for r in rows}


# FTS maintenance triggers (call after init_db or migrations)
def create_fts_triggers():
    """Create triggers to keep leads_fts synchronized with leads table."""
    triggers = [
        """
        CREATE TRIGGER IF NOT EXISTS leads_ai AFTER INSERT ON leads
        BEGIN
            INSERT INTO leads_fts(rowid, business_name, email, city, niche, pain_points)
            VALUES (new.id, new.business_name, new.email, new.city, new.niche, new.pain_points);
        END
        """,
        """
        CREATE TRIGGER IF NOT EXISTS leads_au AFTER UPDATE ON leads
        BEGIN
            UPDATE leads_fts SET
                business_name = new.business_name,
                email = new.email,
                city = new.city,
                niche = new.niche,
                pain_points = new.pain_points
            WHERE rowid = old.id;
        END
        """,
        """
        CREATE TRIGGER IF NOT EXISTS leads_ad AFTER DELETE ON leads
        BEGIN
            DELETE FROM leads_fts WHERE rowid = old.id;
        END
        """
    ]
    with get_conn() as conn:
        for sql in triggers:
            try:
                conn.execute(sql)
            except Exception as e:
                print(f"[WARN] Trigger creation skipped: {e}")


# ==================== User auth helpers ====================

def get_user(username: str):
    with get_conn() as conn:
        return conn.execute(
            "SELECT id, username, password_hash, role FROM users WHERE username=?",
            (username,)
        ).fetchone()


def create_user(username: str, password_hash: str, role: str = "admin") -> int:
    with get_conn() as conn:
        cur = conn.execute(
            "INSERT INTO users (username, password_hash, role) VALUES (?,?,?)",
            (username, password_hash, role)
        )
        return cur.lastrowid


def update_user_last_login(user_id: int):
    with get_conn() as conn:
        conn.execute(
            "UPDATE users SET last_login_at=datetime('now') WHERE id=?",
            (user_id,)
        )


def count_users() -> int:
    with get_conn() as conn:
        return conn.execute("SELECT COUNT(*) FROM users").fetchone()[0]