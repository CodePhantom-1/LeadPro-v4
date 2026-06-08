"""
LeadPro v4 — Web Server
FastAPI: REST + SSE + tracking + audit pages.
"""
from __future__ import annotations
import asyncio, json, uuid, base64, os, aiohttp, time, logging, re
from urllib.parse import urlparse
from pathlib import Path
from typing import AsyncGenerator, Optional, List, AsyncIterator
from contextlib import asynccontextmanager
from datetime import datetime, timedelta, timezone

from functools import lru_cache
from fastapi import FastAPI, HTTPException, Request, Depends, status
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import HTMLResponse, StreamingResponse, Response, FileResponse, RedirectResponse
from fastapi.security import HTTPBearer, HTTPAuthorizationCredentials
from pydantic import BaseModel
from audit_pages import delete_expired_audit_pages
from slowapi import Limiter, _rate_limit_exceeded_handler
from slowapi.errors import RateLimitExceeded
import config
from jose import JWTError, jwt
import bcrypt
@asynccontextmanager
async def lifespan(app: FastAPI) -> AsyncIterator[None]:
    """Initialize database, run migrations, start background scheduler."""
    from database import init_db, create_fts_triggers
    from scheduler import start_scheduler
    from audit import init_fx_rates
    init_db()
    create_fts_triggers()
    _ensure_default_admin()
    init_fx_rates()
    if config.SCHEDULER_ENABLED:
        start_scheduler()
        print("[OK] Background scheduler started")
    yield
    from scheduler import stop_scheduler
    stop_scheduler()
    # Cancel any lingering background tasks
    pending = [t for t in _background_tasks if not t.done()]
    for t in pending:
        t.cancel()
    if pending:
        await asyncio.gather(*pending, return_exceptions=True)
    print(f"[STOP] Scheduler stopped — {len(pending)} background tasks cancelled")

app = FastAPI(title="LeadPro v4", lifespan=lifespan)
app.add_middleware(CORSMiddleware, allow_origins=config.ALLOWED_ORIGINS, allow_methods=["*"], allow_headers=["*"])

# Security headers middleware
@app.middleware("http")
async def add_security_headers(request: Request, call_next):
    response = await call_next(request)
    response.headers["Strict-Transport-Security"] = "max-age=63072000; includeSubDomains; preload"
    response.headers["X-Content-Type-Options"] = "nosniff"
    response.headers["X-Frame-Options"] = "DENY"
    response.headers["Referrer-Policy"] = "strict-origin-when-cross-origin"
    response.headers["X-XSS-Protection"] = "1; mode=block"
    return response

# Request logging middleware
_logger = logging.getLogger("request")

@app.middleware("http")
async def log_requests(request: Request, call_next):
    start_time = time.time()
    response = await call_next(request)
    process_time = time.time() - start_time
    _logger.info(
        f"{request.method} {request.url.path} {response.status_code} {process_time:.3f}s"
    )
    return response

def _rate_limit_key(request: Request) -> str:
    """Rate limit key: respects X-Forwarded-For behind proxy, falls back to direct IP."""
    forwarded = request.headers.get("X-Forwarded-For")
    if forwarded:
        return forwarded.split(",")[0].strip()
    if request.client:
        return request.client.host
    return "127.0.0.1"


# Rate limiting
limiter = Limiter(key_func=_rate_limit_key)
app.state.limiter = limiter
app.add_exception_handler(RateLimitExceeded, _rate_limit_exceeded_handler)

# JWT authentication (non-auto-error so optional auth works; enforced in dependency)
security = HTTPBearer(auto_error=False)
JWT_ALGORITHM = "HS256"
ACCESS_TOKEN_EXPIRE_HOURS = 24


def _hash_password(password: str) -> str:
    return bcrypt.hashpw(password.encode("utf-8"), bcrypt.gensalt()).decode("utf-8")


def _verify_password(password: str, password_hash: str) -> bool:
    try:
        return bcrypt.checkpw(password.encode("utf-8"), password_hash.encode("utf-8"))
    except Exception:
        return False


def create_access_token(data: dict) -> str:
    to_encode = data.copy()
    expire = datetime.now(timezone.utc) + timedelta(hours=ACCESS_TOKEN_EXPIRE_HOURS)
    to_encode.update({"exp": expire})
    return jwt.encode(to_encode, config.JWT_SECRET, algorithm=JWT_ALGORITHM)


async def get_current_user(request: Request = None, credentials: HTTPAuthorizationCredentials = Depends(security)) -> str:
    token = credentials.credentials if credentials else ""
    if (not token or token == "null") and request:
        token = request.query_params.get("token", "")
    if not token:
        raise HTTPException(status_code=401, detail="Not authenticated")
    try:
        payload = jwt.decode(token, config.JWT_SECRET, algorithms=[JWT_ALGORITHM])
        username = payload.get("sub")
        if not username:
            raise HTTPException(status_code=401, detail="Invalid token")
        return username
    except JWTError:
        raise HTTPException(status_code=401, detail="Invalid token")


async def get_current_user_role(credentials: HTTPAuthorizationCredentials = Depends(security)) -> tuple[str, str]:
    if credentials is None:
        raise HTTPException(status_code=401, detail="Missing authorization header")
    token = credentials.credentials
    try:
        payload = jwt.decode(token, config.JWT_SECRET, algorithms=[JWT_ALGORITHM])
        username = payload.get("sub", "")
        role = payload.get("role", "user")
        return username, role
    except JWTError:
        raise HTTPException(status_code=401, detail="Invalid token")


async def require_admin(user_role: tuple[str, str] = Depends(get_current_user_role)):
    _, role = user_role
    if role != "admin":
        raise HTTPException(status_code=403, detail="Admin access required")


def _ensure_default_admin():
    """Bootstrap a default admin user if the users table is empty.

    Prints the initial password to stderr — change immediately on first login.
    """
    from database import count_users, create_user
    import secrets
    if count_users() > 0:
        return
    initial_password = os.getenv("INITIAL_ADMIN_PASSWORD") or secrets.token_urlsafe(16)
    create_user("admin", _hash_password(initial_password), role="admin")
    print("=" * 60)
    print("[AUTH] Default admin user created.")
    print(f"[AUTH]   username: admin")
    print(f"[AUTH]   password: {initial_password}")
    print("[AUTH] CHANGE THIS via POST /api/auth/change-password")
    print("=" * 60)



_PIXEL = base64.b64decode("R0lGODlhAQABAIAAAAAAAP///yH5BAEAAAAALAAAAAABAAEAAAIBRAA7")
_jobs: dict[str, asyncio.Queue] = {}
_background_tasks: set = set()
_audit_email_sem = asyncio.Semaphore(10)

def _new_job():
    jid = str(uuid.uuid4())[:8]; q = asyncio.Queue(); _jobs[jid] = q; return jid, q

async def _sse_stream(job_id):
    q = _jobs.get(job_id)
    if q is None: yield f"data: {json.dumps({'type':'error','message':'Job not found'})}\n\n"; return
    cancel_event = asyncio.Event()
    try:
        while True:
            try: msg = await asyncio.wait_for(q.get(), timeout=60.0)
            except asyncio.TimeoutError: yield ": keepalive\n\n"; continue
            if msg is None: yield f"data: {json.dumps({'type':'done'})}\n\n"; break
            yield f"data: {json.dumps(msg)}\n\n"
    except asyncio.CancelledError:
        cancel_event.set()
    finally:
        try:
            while not q.empty():
                try: q.get_nowait()
                except Exception: break
        except Exception:
            pass
        _jobs.pop(job_id, None)

def _sse(jid): return StreamingResponse(_sse_stream(jid),media_type="text/event-stream",headers={"Cache-Control":"no-cache","X-Accel-Buffering":"no"})

# ── Authentication ──
class LoginRequest(BaseModel):
    username: str
    password: str


class ChangePasswordRequest(BaseModel):
    old_password: str
    new_password: str


@app.post("/api/auth/login")
@limiter.limit("5/minute")
async def login(request: Request, body: LoginRequest):
    from database import get_user, update_user_last_login
    row = get_user(body.username)
    if not row or not _verify_password(body.password, row["password_hash"]):
        raise HTTPException(status_code=401, detail="Invalid credentials")
    update_user_last_login(row["id"])
    token = create_access_token(data={"sub": row["username"], "role": row["role"]})
    return {"access_token": token, "token_type": "bearer"}


@app.post("/api/auth/change-password")
@limiter.limit("5/minute")
async def change_password(request: Request, body: ChangePasswordRequest,
                           user: str = Depends(get_current_user)):
    from database import get_user, get_conn
    row = get_user(user)
    if not row or not _verify_password(body.old_password, row["password_hash"]):
        raise HTTPException(status_code=401, detail="Old password incorrect")
    if len(body.new_password) < 8:
        raise HTTPException(status_code=400, detail="Password must be at least 8 characters")
    if not any(c.isupper() for c in body.new_password):
        raise HTTPException(status_code=400, detail="Password must contain an uppercase letter")
    if not any(c.islower() for c in body.new_password):
        raise HTTPException(status_code=400, detail="Password must contain a lowercase letter")
    if not any(c.isdigit() for c in body.new_password):
        raise HTTPException(status_code=400, detail="Password must contain a digit")
    new_hash = _hash_password(body.new_password)
    with get_conn() as conn:
        conn.execute("UPDATE users SET password_hash=? WHERE id=?", (new_hash, row["id"]))
    return {"ok": True}


@app.get("/api/auth/me")
async def auth_me(user: str = Depends(get_current_user)):
    from database import get_user
    row = get_user(user)
    if not row:
        raise HTTPException(status_code=404, detail="User not found")
    return {"username": row["username"], "role": row["role"]}


# ── UI (cached in memory) ──
@lru_cache(maxsize=2)
def _read_ui_html(name: str) -> str:
    p = Path(__file__).parent / name
    if not p.exists():
        return ""
    return p.read_text(encoding="utf-8")


@app.get("/", response_class=HTMLResponse)
async def serve_ui():
    html = _read_ui_html("index.html")
    if not html:
        raise HTTPException(404)
    return HTMLResponse(html)


@app.get("/landing", response_class=HTMLResponse)
async def serve_landing():
    html = _read_ui_html("landing.html")
    if not html:
        raise HTTPException(404)
    return HTMLResponse(html)

@app.get("/new_style.css", response_class=FileResponse)
async def serve_css():
    p = Path("new_style.css")
    if not p.exists():
        raise HTTPException(404)
    return FileResponse(str(p), media_type="text/css")

# ── Tracking ──
_track_log = logging.getLogger("tracking")


@app.get("/t/o/{oid}.gif")
@limiter.limit("60/minute")
async def track_open(oid: int, request: Request):
    from database import get_conn
    try:
        with get_conn() as conn:
            conn.execute(
                "INSERT INTO tracking_events (outreach_id,event_type,ip,user_agent) VALUES (?,?,?,?)",
                (oid, "open", request.client.host if request.client else "",
                 request.headers.get("user-agent", "")),
            )
            conn.execute(
                "UPDATE outreach SET open_tracked=1, opened_at=? WHERE id=? AND open_tracked=0",
                (datetime.now(timezone.utc).isoformat(), oid),
            )
    except Exception as e:
        _track_log.warning("open-track failed for oid=%s: %s", oid, e)
    return Response(content=_PIXEL, media_type="image/gif", headers={"Cache-Control": "no-store"})


@app.get("/t/c/{oid}/{url_b64}")
@limiter.limit("30/minute")
async def track_click(oid: int, url_b64: str, request: Request):
    from database import get_conn
    try:
        with get_conn() as conn:
            conn.execute(
                "INSERT INTO tracking_events (outreach_id,event_type,ip,user_agent) VALUES (?,?,?,?)",
                (oid, "click", request.client.host if request.client else "",
                 request.headers.get("user-agent", "")),
            )
            conn.execute(
                "UPDATE outreach SET clicked_at=? WHERE id=? AND clicked_at IS NULL",
                (datetime.now(timezone.utc).isoformat(), oid),
            )
    except Exception as e:
        _track_log.warning("click-track failed for oid=%s: %s", oid, e)
    try:
        target = base64.urlsafe_b64decode(url_b64).decode()
    except Exception:
        target = "/"
    # Validate target URL — only allow http/https and the tracking domain or /
    if target not in ("/", ""):
        try:
            parsed = urlparse(target)
            if parsed.scheme not in ("http", "https") or not parsed.netloc:
                target = "/"
        except Exception:
            target = "/"
    return RedirectResponse(url=target, status_code=302)


# ── Audit page tracking pixel ──
@app.get("/t/audit/{token}.gif")
@limiter.limit("60/minute")
async def track_audit_view(token: str, request: Request):
    from database import get_conn
    try:
        with get_conn() as conn:
            lead = conn.execute(
                "SELECT id FROM leads WHERE audit_page_token=?", (token,)
            ).fetchone()
            if lead:
                conn.execute(
                    "INSERT INTO events (lead_id,event_type,note) VALUES (?,?,?)",
                    (lead["id"], "audit_viewed",
                     f"ip={request.client.host if request.client else ''}"),
                )
    except Exception as e:
        _track_log.warning("audit-view track failed for token=%s: %s", token, e)
    return Response(content=_PIXEL, media_type="image/gif", headers={"Cache-Control": "no-store"})

# ── Serve audit pages ──
_SAFE_TOKEN_RE = re.compile(r"^[a-zA-Z0-9_-]{6,64}$")


@app.get("/audit/{token}")
async def serve_audit_page(token: str):
    if not _SAFE_TOKEN_RE.match(token):
        raise HTTPException(400, "Invalid token")
    safe_path = Path(f"audits/{token}.html").resolve()
    audits_dir = Path("audits").resolve()
    if not str(safe_path).startswith(str(audits_dir)):
        raise HTTPException(400, "Invalid token")
    if not safe_path.exists():
        raise HTTPException(404, "Audit page not found")
    return HTMLResponse(safe_path.read_text(encoding="utf-8"))

# ── Stats ──
@app.get("/health")
async def health_check():
    """Production health check — no auth required."""
    from database import get_conn
    try:
        with get_conn() as conn:
            conn.execute("SELECT 1")
        return {"status": "ok", "database": "connected"}
    except Exception as e:
        return {"status": "error", "database": str(e)}


@app.get("/api/stats")
async def api_stats(user: str = Depends(get_current_user)):
    from database import get_stats; return get_stats()

@app.get("/api/smtp/test")
async def api_smtp_test(user: str = Depends(get_current_user)):
    from outreach import test_smtp
    return {"ok": await asyncio.get_running_loop().run_in_executor(None, test_smtp)}

@app.get("/api/brevo/test")
async def api_brevo_test(user: str = Depends(get_current_user)):
    from outreach import test_brevo_smtp
    return {"ok": await asyncio.get_running_loop().run_in_executor(None, test_brevo_smtp)}

@app.post("/api/audit/cleanup")
async def api_audit_cleanup(user: str = Depends(get_current_user)):
    deleted = delete_expired_audit_pages()
    return {"deleted": deleted, "message": f"Removed {deleted} expired audit pages"}

# ── Analytics ──
@app.get("/api/analytics")
async def api_analytics(user: str = Depends(get_current_user)):
    from analytics import get_full_analytics; return get_full_analytics()

# ── Campaigns ──
@app.get("/api/campaigns")
async def api_campaigns(user: str = Depends(get_current_user)):
    from database import get_conn
    with get_conn() as conn:
        rows = conn.execute("SELECT id,name,country,status,created_at,(SELECT COUNT(*) FROM leads WHERE leads.campaign_id = campaigns.id) + (SELECT COUNT(DISTINCT lead_id) FROM outreach WHERE outreach.campaign_id = campaigns.id AND lead_id NOT IN (SELECT id FROM leads WHERE leads.campaign_id = campaigns.id)) as lead_count FROM campaigns ORDER BY id DESC").fetchall()
    return [dict(r) for r in rows]

class CreateCampaignBody(BaseModel):
    name: str; country: str; lead_ids: Optional[List[int]] = None; lead_batch: Optional[str] = None

@app.post("/api/campaigns")
async def api_create_campaign(body: CreateCampaignBody, user: str = Depends(get_current_user)):
    from database import create_campaign, get_conn
    cid = create_campaign(body.name, body.country)
    with get_conn() as conn:
        if body.lead_ids:
            placeholders = ','.join('?' * len(body.lead_ids))
            conn.execute(f"UPDATE leads SET campaign_id = ? WHERE id IN ({placeholders})", [cid] + body.lead_ids)
        elif body.lead_batch:
            if body.lead_batch == '__all_unassigned__':
                conn.execute("UPDATE leads SET campaign_id = ? WHERE campaign_id IS NULL", [cid])
            else:
                conn.execute("UPDATE leads SET campaign_id = ? WHERE source_query = ? AND campaign_id IS NULL", [cid, body.lead_batch])
    return {"id":cid,"name":body.name,"country":body.country,"status":"active"}

@app.patch("/api/campaigns/{cid}/status")
async def api_campaign_status(cid: int, status: str, user: str = Depends(get_current_user)):
    """Update campaign status. Accepts ?status=... query param for backwards compat."""
    ALLOWED_STATUSES = {"active", "paused", "archived", "completed"}
    if status not in ALLOWED_STATUSES:
        raise HTTPException(400, f"Invalid status. Must be one of: {sorted(ALLOWED_STATUSES)}")
    from database import get_conn
    with get_conn() as conn:
        cur = conn.execute("UPDATE campaigns SET status=? WHERE id=?", (status, cid))
        if cur.rowcount == 0:
            raise HTTPException(404, "Campaign not found")
    return {"ok": True}

@app.delete("/api/campaigns/{cid}")
async def api_delete_campaign(cid: int, user: str = Depends(get_current_user)):
    """Delete a campaign and unlink associated outreach and leads."""
    from database import get_conn
    with get_conn() as conn:
        cur = conn.execute("SELECT id FROM campaigns WHERE id=?", (cid,))
        if cur.fetchone() is None:
            raise HTTPException(404, "Campaign not found")
        conn.execute("UPDATE outreach SET campaign_id = NULL WHERE campaign_id = ?", (cid,))
        conn.execute("UPDATE leads SET campaign_id = NULL WHERE campaign_id = ?", (cid,))
        conn.execute("DELETE FROM campaigns WHERE id = ?", (cid,))
    return {"ok": True}

@app.get("/api/lead-batches")
async def api_lead_batches(user: str = Depends(get_current_user)):
    from database import get_conn
    with get_conn() as conn:
        rows = conn.execute(
            "SELECT COALESCE(NULLIF(source_query,''),'Unassigned') as batch, "
            "COUNT(*) as count, GROUP_CONCAT(id) as lead_ids "
            "FROM leads GROUP BY source_query ORDER BY count DESC"
        ).fetchall()
    return [dict(r) for r in rows]

# ── Leads ──
@app.get("/api/leads")
async def api_leads(min_score:int=0,country:str="",service:str="",has_email:bool=False,
                    sort:str="lead_score",limit:int=100,offset:int=0,
                    user: str = Depends(get_current_user)):
    from database import get_conn
    valid_sorts = {"lead_score","ops_score","intent_score","estimated_monthly_loss","rating"}
    sort_col = sort if sort in valid_sorts else "lead_score"

    clauses,params = ["lead_score >= ?"], [min_score]
    if country: clauses.append("LOWER(country)=?"); params.append(country.lower())
    if service: clauses.append("ideal_service=?"); params.append(service)
    if has_email: clauses.append("email IS NOT NULL AND email!='' AND email!='N/A'")
    where = " AND ".join(clauses)
    cp=list(params); params+=[limit,offset]
    with get_conn() as conn:
        rows = conn.execute(
            f"SELECT id,place_id,business_name,email,phone,website,city,country,"
            f"niche,rating,review_count,pain_points,ideal_service,lead_score,"
            f"ops_score,intent_score,estimated_monthly_loss,"
            f"decision_maker,decision_maker_title,tech_stack_json,ops_pain_points,"
            f"best_keyword_pos,competitor_count,audit_page_token,source_query,scraped_at "
            f"FROM leads WHERE {where} ORDER BY {sort_col} DESC LIMIT ? OFFSET ?", params
        ).fetchall()
        total = conn.execute(f"SELECT COUNT(*) FROM leads WHERE {where}",cp).fetchone()[0]
    return {"leads":[dict(r) for r in rows],"total":total}

@app.get("/api/leads/filters")
async def api_lead_filters(user: str = Depends(get_current_user)):
    from database import get_conn
    with get_conn() as conn:
        countries=[r[0] for r in conn.execute("SELECT DISTINCT country FROM leads WHERE country IS NOT NULL ORDER BY country").fetchall()]
        services=[r[0] for r in conn.execute("SELECT DISTINCT ideal_service FROM leads WHERE ideal_service IS NOT NULL ORDER BY ideal_service").fetchall()]
    return {"countries":countries,"services":services}

@app.get("/api/leads/{lead_id}/preview-email")
async def api_preview_email(lead_id: int, user: str = Depends(get_current_user)):
    from database import get_conn
    from ai_engine import generate_email
    from audit_pages import generate_audit_preview
    from config import BASE_URL
    with get_conn() as conn:
        lead = conn.execute("SELECT * FROM leads WHERE id=?",(lead_id,)).fetchone()
    if not lead: raise HTTPException(404)
    lead = dict(lead)
    audit_url = None
    audit_preview = None
    token = lead.get("audit_page_token")
    if token:
        audit_url = f"{BASE_URL}/audit/{token}"
        audit_preview = generate_audit_preview(lead, token)
    loop = asyncio.get_running_loop()
    content = await loop.run_in_executor(None, lambda: generate_email(
        business_name=lead.get("business_name") or "",pain_points_json=lead.get("pain_points") or "[]",
        source_query=lead.get("source_query") or "",country=lead.get("country") or "",sequence_step=1,
        decision_maker=lead.get("decision_maker", None),
        estimated_monthly_loss=lead.get("estimated_monthly_loss") or 0,
        ops_pain_points_json=lead.get("ops_pain_points"),
        audit_page_url=audit_url,
        audit_preview=audit_preview,
        niche=lead.get("niche"),
    ))
    return content or {"error":"AI generation failed"}

# ── Generate audit page for a lead ──
@app.post("/api/leads/{lead_id}/audit-page")
async def api_gen_audit_page(lead_id: int, user: str = Depends(get_current_user)):
    from database import get_conn
    from audit_pages import generate_audit_page
    from audit import estimate_revenue_impact
    with get_conn() as conn:
        lead = conn.execute("SELECT * FROM leads WHERE id=?",(lead_id,)).fetchone()
        if not lead: raise HTTPException(404)
        lead = dict(lead)
        seo = [dict(r) for r in conn.execute("SELECT * FROM seo_rankings WHERE lead_id=? ORDER BY checked_at DESC LIMIT 10",(lead_id,)).fetchall()]
        comps = [dict(r) for r in conn.execute("SELECT * FROM competitors WHERE lead_id=?",(lead_id,)).fetchall()]
    pains = json.loads(lead.get("pain_points","[]") or "[]")
    ops = json.loads(lead.get("ops_pain_points","[]") or "[]")
    roi = estimate_revenue_impact(lead.get("niche",""), pains, ops, lead.get("country",""))
    loop = asyncio.get_running_loop()
    path = await loop.run_in_executor(None, lambda: generate_audit_page(lead_id, lead, seo, comps, roi))
    from config import BASE_URL
    return {"path": path, "url": f"{BASE_URL}{path}"}

# ── Lead Gen ──
class LeadGenRequest(BaseModel):
    country: str
    target: int = 100
    industry: Optional[str] = None
    business_type: Optional[str] = None  # "ecommerce", "service", "restaurant", "saas", "clinic", "all"
    city: Optional[str] = None           # override: restrict search to one city
    min_lead_score: Optional[int] = None
    min_ops_score: Optional[int] = None
    min_intent_score: Optional[int] = None
    exclude_competitors: Optional[bool] = False
    include_clean_leads: Optional[bool] = False
    tech_stack_filters: Optional[List[str]] = None
    source_selection: Optional[List[str]] = None

@app.post("/api/leadgen/start")
async def api_start_leadgen(req: LeadGenRequest, user: str = Depends(get_current_user)):
    from database import init_db; init_db()
    from config import USE_LEGACY_LEADGEN
    jid,q=_new_job()
    async def _run():
        if USE_LEGACY_LEADGEN:
            from leadgen_legacy import run_engine_web
        else:
            from leadgen import run_engine_web
        try: await run_engine_web(
            country=req.country,
            target=req.target,
            queue=q,
            industry=req.industry,
            business_type=req.business_type,
            city=req.city,
            min_lead_score=req.min_lead_score,
            min_ops_score=req.min_ops_score,
            min_intent_score=req.min_intent_score,
            exclude_competitors=req.exclude_competitors,
            include_clean_leads=req.include_clean_leads,
            tech_stack_filters=req.tech_stack_filters,
            source_selection=req.source_selection
        )
        except Exception as e: await q.put({"type":"error","message":str(e)})
        finally: await q.put(None)
    t = asyncio.create_task(_run()); _background_tasks.add(t); t.add_done_callback(_background_tasks.discard); return {"job_id":jid}

@app.get("/api/leadgen/stream/{jid}")
async def api_lg_stream(jid:str): return _sse(jid)

# ── Outreach ──
@app.get("/api/outreach/preview/{cid}")
async def api_outreach_preview(cid:int,min_score:int=40, user: str = Depends(get_current_user)):
    from database import get_leads_for_outreach, get_opted_out_emails, get_hard_bounce_emails
    oo=get_opted_out_emails();hb=get_hard_bounce_emails()
    leads=get_leads_for_outreach(cid,step=1,min_score=min_score)
    result=[]
    for l in leads[:200]:
        d=dict(l);d["_skipped"]=(l["email"] in oo or l["email"] in hb or not l["email"] or "@" not in str(l["email"]))
        result.append(d)
    return result

class OutreachRequest(BaseModel):
    campaign_id:int;min_score:int=40;lead_ids:list[int]=[]

@app.post("/api/outreach/start")
async def api_start_outreach(req:OutreachRequest, user: str = Depends(get_current_user)):
    jid,q=_new_job()
    async def _run():
        from outreach import run_initial_outreach_web
        try: await run_initial_outreach_web(req.campaign_id,req.min_score,req.lead_ids,q)
        except Exception as e: await q.put({"type":"error","message":str(e)})
        finally: await q.put(None)
    t = asyncio.create_task(_run()); _background_tasks.add(t); t.add_done_callback(_background_tasks.discard); return {"job_id":jid}

@app.post("/api/followups/start")
async def api_start_followups(user: str = Depends(get_current_user)):
    jid,q=_new_job()
    async def _run():
        from outreach import run_followups_web
        from outreach import check_replies
        try:
            await q.put({"type":"info","message":"Scanning inbox for replies…"})
            stats = await asyncio.get_running_loop().run_in_executor(None,check_replies)
            await q.put({"type":"info","message":f"Inbox scanned — {stats.get('replies',0)} replies, {stats.get('opt_outs',0)} opt-outs"})
            await run_followups_web(q)
        except Exception as e: await q.put({"type":"error","message":str(e)})
        finally: await q.put(None)
    t = asyncio.create_task(_run()); _background_tasks.add(t); t.add_done_callback(_background_tasks.discard); return {"job_id":jid}

@app.get("/api/outreach/stream/{jid}")
async def api_out_stream(jid:str): return _sse(jid)

# ── Replies ──
@app.post("/api/replies/scan")
async def api_scan_replies(user: str = Depends(get_current_user)):
    jid,q=_new_job()
    async def _run():
        from outreach import check_replies
        try:
            await q.put({"type":"info","message":"Connecting to IMAP…"})
            stats = await asyncio.get_running_loop().run_in_executor(None,check_replies)
            await q.put({"type":"stats","message":"Complete","data":stats})
        except Exception as e: await q.put({"type":"error","message":str(e)})
        finally: await q.put(None)
    t = asyncio.create_task(_run()); _background_tasks.add(t); t.add_done_callback(_background_tasks.discard); return {"job_id":jid}

@app.get("/api/replies/stream/{jid}")
async def api_rep_stream(jid:str): return _sse(jid)

# ── Intelligence ──
class IntelRequest(BaseModel):
    lead_ids:list[int]|None=None;min_score:int=50;country:str|None=None;service:str|None=None

@app.post("/api/intel/start")
async def api_start_intel(req:IntelRequest, user: str = Depends(get_current_user)):
    jid,q=_new_job()
    async def _run():
        from intel import run_intel_batch
        try: await run_intel_batch(lead_ids=req.lead_ids,min_score=req.min_score,country=req.country,service=req.service,queue=q)
        except Exception as e: await q.put({"type":"error","message":str(e)})
        finally: await q.put(None)
    t = asyncio.create_task(_run()); _background_tasks.add(t); t.add_done_callback(_background_tasks.discard); return {"job_id":jid}

@app.get("/api/intel/stream/{jid}")
async def api_intel_stream(jid:str): return _sse(jid)

# ── Proposals ──
@app.post("/api/proposals/generate/{lead_id}")
async def api_gen_proposal(lead_id:int, user: str = Depends(get_current_user)):
    from proposal_engine import generate_proposal
    path = await asyncio.get_running_loop().run_in_executor(None,generate_proposal,lead_id)
    return {"path":path,"lead_id":lead_id}

@app.post("/api/proposals/batch")
async def api_batch_proposals(data:dict, user: str = Depends(get_current_user)):
    from proposal_engine import generate_proposal
    from database import get_conn
    ms=data.get("min_score",60);lim=data.get("limit",20)
    with get_conn() as conn:
        leads=conn.execute("SELECT id FROM leads WHERE lead_score>=? AND website IS NOT NULL AND website!='' ORDER BY lead_score DESC LIMIT ?",(ms,lim)).fetchall()
    loop=asyncio.get_running_loop(); paths=[]
    for l in leads:
        try: p=await loop.run_in_executor(None,generate_proposal,l["id"]); paths.append({"lead_id":l["id"],"path":p})
        except: pass
    return {"generated":len(paths),"proposals":paths}

@app.get("/api/proposals/{lead_id}/download")
async def api_download_proposal(lead_id:int, user: str = Depends(get_current_user)):
    from database import get_conn
    with get_conn() as conn:
        row=conn.execute("SELECT pdf_path FROM proposals WHERE lead_id=? AND pdf_path IS NOT NULL ORDER BY created_at DESC LIMIT 1",(lead_id,)).fetchone()
    if not row or not os.path.exists(row["pdf_path"]): raise HTTPException(404)
    return FileResponse(row["pdf_path"],media_type="application/pdf",filename=row["pdf_path"].split("/")[-1])

# ── Warmup ──
@app.post("/api/warmup/run")
async def api_warmup_run(user: str = Depends(get_current_user)):
    jid,q=_new_job()
    async def _run():
        from outreach import run_warmup_cycle
        try: await run_warmup_cycle(q)
        except Exception as e: await q.put({"type":"error","message":str(e)})
        finally: await q.put(None)
    t = asyncio.create_task(_run()); _background_tasks.add(t); t.add_done_callback(_background_tasks.discard); return {"job_id":jid}

@app.get("/api/warmup/stream/{jid}")
async def api_wu_stream(jid:str): return _sse(jid)

@app.get("/api/warmup/accounts")
async def api_warmup_accounts(user: str = Depends(get_current_user)):
    from database import get_conn
    with get_conn() as conn:
        return [dict(r) for r in conn.execute("SELECT id,email,domain,role,current_day,status,daily_limit,smtp_host,smtp_port,imap_host FROM warmup_accounts ORDER BY role,domain").fetchall()]

class WarmupAccountBody(BaseModel):
    email:str;password:str;domain:str;role:str="sender";smtp_host:str="smtp.gmail.com";smtp_port:int=587;imap_host:str="imap.gmail.com"

class WarmupAccountUpdate(BaseModel):
    email: Optional[str] = None
    password: Optional[str] = None
    domain: Optional[str] = None
    role: Optional[str] = None
    smtp_host: Optional[str] = None
    smtp_port: Optional[int] = None
    imap_host: Optional[str] = None
    daily_limit: Optional[int] = None
    current_day: Optional[int] = None
    status: Optional[str] = None

@app.post("/api/warmup/accounts")
async def api_add_wu(body:WarmupAccountBody, _=Depends(require_admin)):
    from database import get_conn
    from config import encrypt_password
    with get_conn() as conn:
        conn.execute("INSERT OR IGNORE INTO warmup_accounts (email,password,smtp_host,smtp_port,imap_host,domain,role) VALUES (?,?,?,?,?,?,?)",
                     (body.email, encrypt_password(body.password or ""), body.smtp_host, body.smtp_port, body.imap_host, body.domain, body.role))
    return {"ok":True}

@app.get("/api/warmup/accounts/{aid}")
async def api_get_wu(aid:int, user: str = Depends(get_current_user)):
    from database import get_conn
    with get_conn() as conn:
        row = conn.execute("SELECT id, email, password, domain, role, current_day, status, daily_limit, smtp_host, smtp_port, imap_host FROM warmup_accounts WHERE id=?", (aid,)).fetchone()
        if not row:
            raise HTTPException(status_code=404, detail="Account not found")
        return dict(row)

@app.put("/api/warmup/accounts/{aid}")
async def api_update_wu(aid:int, body:WarmupAccountUpdate, user: str = Depends(get_current_user)):
    from database import get_conn
    from config import encrypt_password
    with get_conn() as conn:
        # Build update dynamically
        updates = []
        params = []
        if body.email is not None: updates.append("email=?"); params.append(body.email)
        if body.password is not None: updates.append("password=?"); params.append(encrypt_password(body.password))
        if body.domain is not None: updates.append("domain=?"); params.append(body.domain)
        if body.role is not None: updates.append("role=?"); params.append(body.role)
        if body.smtp_host is not None: updates.append("smtp_host=?"); params.append(body.smtp_host)
        if body.smtp_port is not None: updates.append("smtp_port=?"); params.append(body.smtp_port)
        if body.imap_host is not None: updates.append("imap_host=?"); params.append(body.imap_host)
        if body.daily_limit is not None: updates.append("daily_limit=?"); params.append(body.daily_limit)
        if body.current_day is not None: updates.append("current_day=?"); params.append(body.current_day)
        if body.status is not None: updates.append("status=?"); params.append(body.status)
        if not updates:
            return {"ok": True}
        params.append(aid)
        query = f"UPDATE warmup_accounts SET {', '.join(updates)} WHERE id=?"
        conn.execute(query, params)
    return {"ok":True}

@app.delete("/api/warmup/accounts/{aid}")
async def api_del_wu(aid:int, user: str = Depends(get_current_user)):
    from database import get_conn
    with get_conn() as conn: conn.execute("DELETE FROM warmup_accounts WHERE id=?",(aid,))
    return {"ok":True}

class WhatsAppGenerateRequest(BaseModel):
    lead_id: int

@app.post("/api/whatsapp/generate")
async def api_whatsapp_generate(body: WhatsAppGenerateRequest, user: str = Depends(get_current_user)):
    from database import get_conn
    from ai_engine import generate_whatsapp
    with get_conn() as conn:
        row = conn.execute("SELECT business_name, pain_points, country, phone FROM leads WHERE id=?", (body.lead_id,)).fetchone()
        if not row:
            raise HTTPException(status_code=404, detail="Lead not found")
        message = await asyncio.get_running_loop().run_in_executor(
            None, lambda: generate_whatsapp(row.get("business_name", ""), row.get("pain_points", ""), row.get("country", ""))
        )
        return {"message": message, "phone": row.get("phone", ""), "business_name": row.get("business_name", "")}

@app.get("/api/whatsapp/leads")
async def api_whatsapp_leads(limit: int = 200, min_score: int = 0, user: str = Depends(get_current_user)):
    """Get leads with phone numbers for WhatsApp outreach."""
    from database import get_conn
    with get_conn() as conn:
        rows = conn.execute(
            "SELECT id, business_name, phone, email, country, lead_score, intent_score, "
            "pain_points, decision_maker, estimated_monthly_loss, ideal_service "
            "FROM leads WHERE phone IS NOT NULL AND phone != '' AND phone != 'N/A' "
            "AND LENGTH(phone) >= 7 AND lead_score >= ? "
            "ORDER BY lead_score DESC LIMIT ?",
            (min_score, limit)
        ).fetchall()
    return [dict(r) for r in rows]

# ── Warmup Placement & Rescue ──
@app.post("/api/warmup/accounts/{aid}/placement")
async def api_check_placement(aid: int, user: str = Depends(get_current_user)):
    from database import get_conn
    from outreach import check_placement
    from config import decrypt_password
    with get_conn() as conn:
        acct = conn.execute("SELECT * FROM warmup_accounts WHERE id=?", (aid,)).fetchone()
        if not acct:
            raise HTTPException(404, "Account not found")
        senders = [dict(r) for r in conn.execute(
            "SELECT domain FROM warmup_accounts WHERE role='sender'"
        ).fetchall()]
    sender_domains = list(set(s["domain"] for s in senders))
    loop = asyncio.get_running_loop()
    result = await loop.run_in_executor(
        None, lambda: check_placement(acct["imap_host"], acct["email"], decrypt_password(acct["password"]), sender_domains)
    )
    return result

@app.post("/api/warmup/accounts/{aid}/rescue")
async def api_rescue_spam(aid: int, user: str = Depends(get_current_user)):
    from database import get_conn
    from outreach import rescue_from_spam
    from config import decrypt_password
    with get_conn() as conn:
        acct = conn.execute("SELECT * FROM warmup_accounts WHERE id=?", (aid,)).fetchone()
        if not acct:
            raise HTTPException(404, "Account not found")
        senders = [dict(r) for r in conn.execute(
            "SELECT domain FROM warmup_accounts WHERE role='sender'"
        ).fetchall()]
    sender_domains = list(set(s["domain"] for s in senders))
    loop = asyncio.get_running_loop()
    rescued = await loop.run_in_executor(
        None, lambda: rescue_from_spam(acct["imap_host"], acct["email"], decrypt_password(acct["password"]), sender_domains)
    )
    return {"rescued": rescued, "account": acct["email"]}

@app.post("/api/warmup/rescue-all")
async def api_rescue_all(user: str = Depends(get_current_user)):
    """Run spam rescue on all receiver accounts."""
    from database import get_conn
    from outreach import rescue_from_spam
    from config import decrypt_password
    with get_conn() as conn:
        receivers = [dict(r) for r in conn.execute(
            "SELECT * FROM warmup_accounts WHERE role='receiver'"
        ).fetchall()]
        senders = [dict(r) for r in conn.execute(
            "SELECT domain FROM warmup_accounts WHERE role='sender'"
        ).fetchall()]
    if not receivers:
        return {"rescued": 0, "message": "No receiver accounts found"}
    sender_domains = list(set(s["domain"] for s in senders))
    loop = asyncio.get_running_loop()
    total = 0
    for recv in receivers:
        r = await loop.run_in_executor(
            None, lambda rv=recv: rescue_from_spam(rv["imap_host"], rv["email"], decrypt_password(rv["password"]), sender_domains)
        )
        total += r
    return {"rescued": total, "accounts_checked": len(receivers)}

@app.get("/api/warmup/logs")
async def api_warmup_logs(limit: int = 100, offset: int = 0, user: str = Depends(get_current_user)):
    from database import get_conn
    with get_conn() as conn:
        rows = conn.execute(
            "SELECT wl.id, wl.subject, wl.sent_at, wl.landed_in, wl.rescued, "
            "sa.email as from_email, sa.domain as from_domain, "
            "ra.email as to_email "
            "FROM warmup_log wl "
            "LEFT JOIN warmup_accounts sa ON sa.id = wl.from_account "
            "LEFT JOIN warmup_accounts ra ON ra.id = wl.to_account "
            "ORDER BY wl.sent_at DESC LIMIT ? OFFSET ?",
            (limit, offset)
        ).fetchall()
        total = conn.execute("SELECT COUNT(*) FROM warmup_log").fetchone()[0]
    return {"logs": [dict(r) for r in rows], "total": total}

@app.get("/api/warmup/placement-summary")
async def api_warmup_placement_summary(user: str = Depends(get_current_user)):
    """Get placement stats summary from warmup logs."""
    from database import get_conn
    with get_conn() as conn:
        total = conn.execute("SELECT COUNT(*) FROM warmup_log").fetchone()[0]
        rescued = conn.execute("SELECT COUNT(*) FROM warmup_log WHERE rescued=1").fetchone()[0]
        by_domain = [dict(r) for r in conn.execute(
            "SELECT sa.domain, COUNT(*) as sent, SUM(wl.rescued) as rescued "
            "FROM warmup_log wl JOIN warmup_accounts sa ON sa.id=wl.from_account "
            "GROUP BY sa.domain ORDER BY sent DESC"
        ).fetchall()]
        accounts_status = [dict(r) for r in conn.execute(
            "SELECT email, domain, role, current_day, status, daily_limit FROM warmup_accounts ORDER BY role, domain"
        ).fetchall()]
    return {
        "total_sent": total,
        "total_rescued": rescued,
        "by_domain": by_domain,
        "accounts": accounts_status
    }

# ── AI: Executive Summary & Recommendations ──
@app.post("/api/leads/{lead_id}/executive-summary")
async def api_exec_summary(lead_id: int, user: str = Depends(get_current_user)):
    from database import get_conn
    from ai_engine import generate_executive_summary
    with get_conn() as conn:
        lead = conn.execute("SELECT * FROM leads WHERE id=?", (lead_id,)).fetchone()
        if not lead:
            raise HTTPException(404, "Lead not found")
        lead = dict(lead)
        seo = [dict(r) for r in conn.execute(
            "SELECT * FROM seo_rankings WHERE lead_id=? ORDER BY checked_at DESC LIMIT 10", (lead_id,)
        ).fetchall()]
        comps = [dict(r) for r in conn.execute(
            "SELECT * FROM competitors WHERE lead_id=?", (lead_id,)
        ).fetchall()]
    # Build gaps list from tech stack
    gaps = []
    try:
        ts = json.loads(lead.get("tech_stack_json") or "{}")
        gaps = [{"category": k, "gap": v[0]} for k, v in ts.items() if v and v[0] == "none"]
    except Exception:
        pass
    loop = asyncio.get_running_loop()
    summary = await loop.run_in_executor(
        None, lambda: generate_executive_summary(lead, seo, comps, gaps)
    )
    return {"summary": summary, "business_name": lead["business_name"]}

@app.post("/api/leads/{lead_id}/recommendations")
async def api_recommendations(lead_id: int, user: str = Depends(get_current_user)):
    from database import get_conn
    from ai_engine import generate_recommendations
    with get_conn() as conn:
        lead = conn.execute("SELECT * FROM leads WHERE id=?", (lead_id,)).fetchone()
        if not lead:
            raise HTTPException(404, "Lead not found")
        lead = dict(lead)
    gaps = []
    try:
        ts = json.loads(lead.get("tech_stack_json") or "{}")
        gaps = [{"category": k, "gap": v[0]} for k, v in ts.items() if v and v[0] == "none"]
    except Exception:
        pass
    loop = asyncio.get_running_loop()
    recs = await loop.run_in_executor(
        None, lambda: generate_recommendations(lead, gaps)
    )
    return {"recommendations": recs, "business_name": lead["business_name"]}

# ── Warmup SMTP Test per account ──
@app.post("/api/warmup/accounts/{aid}/test")
async def api_test_wu_account(aid: int, user: str = Depends(get_current_user)):
    from database import get_conn
    import smtplib
    from config import decrypt_password
    with get_conn() as conn:
        acct = conn.execute("SELECT * FROM warmup_accounts WHERE id=?", (aid,)).fetchone()
        if not acct:
            raise HTTPException(404, "Account not found")
    smtp_host = acct["smtp_host"]
    smtp_port = acct["smtp_port"]
    email_addr = acct["email"]
    password = decrypt_password(acct["password"])
    def _test():
        try:
            s = smtplib.SMTP(smtp_host, smtp_port, timeout=10)
            s.starttls()
            s.login(email_addr, password)
            s.quit()
            return True, None
        except Exception as e:
            return False, str(e)
    loop = asyncio.get_running_loop()
    ok, err = await loop.run_in_executor(None, _test)
    return {"ok": ok, "error": err, "email": email_addr}

# ── Outreach Accounts (multi-account primary sending) ──
class OutreachAccountBody(BaseModel):
    email: str
    password: str = ""
    smtp_host: str = "smtp.gmail.com"
    smtp_port: int = 587
    imap_host: str = "imap.gmail.com"
    display_name: Optional[str] = None
    daily_limit: int = 150
    sending_mode: str = "smtp_password"
    signature: Optional[str] = None

class OutreachAccountUpdate(BaseModel):
    password: Optional[str] = None
    smtp_host: Optional[str] = None
    smtp_port: Optional[int] = None
    imap_host: Optional[str] = None
    display_name: Optional[str] = None
    daily_limit: Optional[int] = None
    active: Optional[int] = None
    sending_mode: Optional[str] = None
    signature: Optional[str] = None

@app.get("/api/outreach-accounts")
async def api_list_outreach_accounts(user: str = Depends(get_current_user)):
    from database import get_outreach_accounts
    return get_outreach_accounts()

@app.post("/api/outreach-accounts")
async def api_add_outreach_account(body: OutreachAccountBody, _=Depends(require_admin)):
    from database import get_conn
    from config import encrypt_password
    encrypted_pw = encrypt_password(body.password or "")
    with get_conn() as conn:
        conn.execute(
            "INSERT OR IGNORE INTO outreach_accounts (email,password,smtp_host,smtp_port,imap_host,display_name,daily_limit,sending_mode,signature) VALUES (?,?,?,?,?,?,?,?,?)",
            (body.email, encrypted_pw, body.smtp_host, body.smtp_port, body.imap_host, body.display_name, body.daily_limit, body.sending_mode, body.signature)
        )
    return {"ok": True}

@app.put("/api/outreach-accounts/{aid}")
async def api_update_outreach_account(aid: int, body: OutreachAccountUpdate, user: str = Depends(get_current_user)):
    from database import get_conn
    from config import encrypt_password
    with get_conn() as conn:
        updates, params = [], []
        if body.password is not None:     updates.append("password=?");     params.append(encrypt_password(body.password))
        if body.smtp_host is not None:    updates.append("smtp_host=?");    params.append(body.smtp_host)
        if body.smtp_port is not None:    updates.append("smtp_port=?");    params.append(body.smtp_port)
        if body.imap_host is not None:    updates.append("imap_host=?");    params.append(body.imap_host)
        if body.display_name is not None: updates.append("display_name=?"); params.append(body.display_name)
        if body.daily_limit is not None:  updates.append("daily_limit=?");  params.append(body.daily_limit)
        if body.active is not None:       updates.append("active=?");       params.append(body.active)
        if body.sending_mode is not None: updates.append("sending_mode=?"); params.append(body.sending_mode)
        if body.signature is not None:    updates.append("signature=?");    params.append(body.signature)
        if not updates:
            return {"ok": True}
        params.append(aid)
        conn.execute(f"UPDATE outreach_accounts SET {', '.join(updates)} WHERE id=?", params)
    return {"ok": True}

@app.delete("/api/outreach-accounts/{aid}")
async def api_delete_outreach_account(aid: int, _=Depends(require_admin)):
    from database import get_conn
    with get_conn() as conn:
        conn.execute("DELETE FROM outreach_accounts WHERE id=?", (aid,))
    return {"ok": True}

@app.post("/api/outreach-accounts/{aid}/test")
async def api_test_outreach_account(aid: int, user: str = Depends(get_current_user)):
    import smtplib
    from database import get_conn
    with get_conn() as conn:
        acct = conn.execute("SELECT * FROM outreach_accounts WHERE id=?", (aid,)).fetchone()
        if not acct:
            raise HTTPException(404, "Account not found")
    acct = dict(acct)
    acct.setdefault("sending_mode", "smtp_password")
    if acct["sending_mode"] == "brevo_relay":
        from outreach import test_brevo_smtp
        ok = await asyncio.get_running_loop().run_in_executor(None, test_brevo_smtp)
        return {"ok": ok, "error": None if ok else "Brevo SMTP failed", "email": acct["email"], "mode": "brevo_relay"}
    smtp_host = acct["smtp_host"]
    smtp_port = acct["smtp_port"]
    email_addr = acct["email"]
    password = acct["password"]
    def _test():
        try:
            s = smtplib.SMTP(smtp_host, smtp_port, timeout=10)
            s.starttls()
            s.login(email_addr, password)
            s.quit()
            return True, None
        except Exception as e:
            return False, str(e)
    ok, err = await asyncio.get_running_loop().run_in_executor(None, _test)
    return {"ok": ok, "error": err, "email": email_addr, "mode": "smtp_password"}

# ── Config ──
_SAFE={"YOUR_NAME","YOUR_COMPANY","YOUR_EMAIL","SMTP_SERVER","SMTP_PORT","IMAP_SERVER","DB_PATH","SCRAPE_THREADS","EMAIL_DELAY_SEC","DAILY_EMAIL_CAP","TRACKING_DOMAIN","MAIL_DOMAINS","BASE_URL","USE_LEGACY_LEADGEN","WARMUP_ENABLED","POSTFIX_HOST","POSTFIX_PORT","WHATSAPP_PHONE_NUMBER","BREVO_SMTP_HOST","BREVO_SMTP_PORT","BREVO_SMTP_LOGIN","CENTRAL_INBOX_EMAIL","CENTRAL_INBOX_IMAP"}
_SECRET={"SERPER_API_KEY","OPENROUTER_API_KEY","PAGESPEED_API_KEY","HUNTER_API_KEY","YOUR_APP_PASSWORD","GOOGLE_PLACES_API_KEY","YELP_API_KEY","CRUNCHBASE_API_KEY","SHOPIFY_API_KEY","GITHUB_API_KEY","CLEARBIT_API_KEY","WHATSAPP_ACCOUNT_SID","WHATSAPP_AUTH_TOKEN","BREVO_SMTP_KEY","CENTRAL_INBOX_PASSWORD"}

@app.get("/api/config")
async def api_get_config(user: str = Depends(get_current_user)):
    env={};p=Path(".env")
    if p.exists():
        for line in p.read_text().splitlines():
            if "=" in line and not line.startswith("#"):
                k,_,v=line.partition("=");k=k.strip()
                if k in _SECRET: env[k]="••••••••" if v.strip() else ""
                elif k in _SAFE: env[k]=v.strip()
    return env

@app.put("/api/config")
async def api_update_config(updates:dict, _=Depends(require_admin)):
    valid_keys = _SAFE | _SECRET
    for k in updates:
        if k not in valid_keys:
            raise HTTPException(400, f"Unknown config key: {k}")
        v = updates[k]
        if not isinstance(v, str):
            raise HTTPException(400, f"Config value for '{k}' must be a string")
        if "\n" in v or "\r" in v:
            raise HTTPException(400, f"Config value for '{k}' contains newlines")
    p=Path(".env");existing={}
    if p.exists():
        lines=p.read_text().splitlines()
    else:
        lines=[]
    env_keys={}
    for i,line in enumerate(lines):
        if "=" in line and not line.startswith("#"):
            k,_,v=line.partition("=");k=k.strip();env_keys[k]=(i,v.strip())
    for k,v in updates.items():
        if k in valid_keys and v!="••••••••":
            if k in env_keys:
                lines[env_keys[k][0]]=f"{k}={v}"
            else:
                lines.append(f"{k}={v}")
    p.write_text("\n".join(lines)+("\n" if lines else ""))
    import config; config.reload_config()
    return {"ok":True,"warning":"Server restart required for some changes to take full effect."}

# ── Activity ──
@app.get("/api/activity")
async def api_activity(limit:int=20, user: str = Depends(get_current_user)):
    from database import get_conn
    with get_conn() as conn:
        rows=conn.execute("SELECT e.event_type,e.note,e.created_at,l.business_name,l.email FROM events e JOIN leads l ON l.id=e.lead_id ORDER BY e.created_at DESC LIMIT ?",(limit,)).fetchall()
    return [dict(r) for r in rows]


# ── v4 New Endpoints ──
@app.get("/api/leads/search")
@limiter.limit("60/minute")
async def search_leads(request: Request, q: str, limit: int = 50, _=Depends(get_current_user)):
    from database import search_leads_fulltext
    rows = search_leads_fulltext(q, limit)
    return [dict(r) for r in rows]


@app.get("/api/leads/export/csv")
@limiter.limit("2/day")
async def export_leads_csv_v4(request: Request, _=Depends(get_current_user)):
    from database import get_conn
    import csv, io
    output = io.StringIO()
    writer = None
    BATCH_SIZE = 500
    offset = 0
    with get_conn() as conn:
        while True:
            rows = conn.execute(
                "SELECT * FROM leads ORDER BY id LIMIT ? OFFSET ?",
                (BATCH_SIZE, offset)
            ).fetchall()
            if not rows:
                break
            if writer is None:
                fieldnames = [k for k in rows[0].keys() if k != "id"]
                writer = csv.DictWriter(output, fieldnames=fieldnames)
                writer.writeheader()
            for row in rows:
                writer.writerow({k: row[k] for k in fieldnames})
            offset += BATCH_SIZE
    return Response(content=output.getvalue(), media_type="text/csv",
                    headers={"Content-Disposition": "attachment; filename=leads.csv"})


class BulkLeadRequest(BaseModel):
    operation: str          # "assign" | "tag" | "untag" | "pipeline" | "delete"
    lead_ids: List[int]
    value: Optional[str] = None  # assignee / tag / stage


@app.post("/api/leads/bulk")
@limiter.limit("30/minute")
async def bulk_lead_operation(request: Request, body: BulkLeadRequest, _=Depends(get_current_user)):
    from database import get_conn
    if not body.lead_ids:
        return {"updated": 0}
    op = body.operation
    ids = body.lead_ids
    placeholders = ",".join("?" * len(ids))

    if op == "assign":
        if body.value is None:
            raise HTTPException(400, "Missing 'value' (assignee)")
        with get_conn() as conn:
            cur = conn.execute(
                f"UPDATE leads SET assigned_to=?, last_activity_at=datetime('now') WHERE id IN ({placeholders})",
                [body.value, *ids],
            )
            return {"updated": cur.rowcount}

    if op == "pipeline":
        if not body.value:
            raise HTTPException(400, "Missing 'value' (pipeline stage)")
        with get_conn() as conn:
            cur = conn.execute(
                f"UPDATE leads SET pipeline_stage=?, last_activity_at=datetime('now') WHERE id IN ({placeholders})",
                [body.value, *ids],
            )
            return {"updated": cur.rowcount}

    if op == "tag":
        if not body.value:
            raise HTTPException(400, "Missing 'value' (tag)")
        with get_conn() as conn:
            conn.executemany(
                "INSERT OR IGNORE INTO lead_tags (lead_id, tag) VALUES (?,?)",
                [(lid, body.value) for lid in ids],
            )
        return {"updated": len(ids)}

    if op == "untag":
        if not body.value:
            raise HTTPException(400, "Missing 'value' (tag)")
        with get_conn() as conn:
            cur = conn.execute(
                f"DELETE FROM lead_tags WHERE tag=? AND lead_id IN ({placeholders})",
                [body.value, *ids],
            )
            return {"updated": cur.rowcount}

    if op == "delete":
        with get_conn() as conn:
            cur = conn.execute(f"DELETE FROM leads WHERE id IN ({placeholders})", ids)
            return {"updated": cur.rowcount}

    raise HTTPException(400, f"Unknown operation: {op}")


# ── Public Audit Request (landing page form) ──
class AuditRequestBody(BaseModel):
    business_name: str
    website: str
    email: str
    phone: Optional[str] = ""
    industry: Optional[str] = ""
    country: Optional[str] = ""


@app.post("/api/public/audit-request")
@limiter.limit("5/minute")
async def public_audit_request(request: Request, body: AuditRequestBody):
    from database import get_conn, upsert_leads
    from audit import audit_lead, estimate_revenue_impact
    from audit_pages import generate_audit_page
    import hashlib

    website = body.website.strip()
    if not website.startswith("http"):
        website = "https://" + website

    place_id = f"audit-{hashlib.md5(website.encode()).hexdigest()[:12]}"

    with get_conn() as conn:
        existing = conn.execute(
            "SELECT id, audit_page_token FROM leads WHERE place_id=?", (place_id,)
        ).fetchone()

    if existing and existing["audit_page_token"]:
        from config import BASE_URL
        existing_url = f"{BASE_URL}/audit/{existing['audit_page_token']}"
        return {"ok": True, "status": "existing", "audit_url": existing_url, "message": "Your audit is ready!"}

    raw = {
        "title": body.business_name,
        "placeId": place_id,
        "website": website,
        "phoneNumber": body.phone or "",
        "email": body.email,
        "rating": 0,
        "userRatingCount": 0,
        "address": "",
        "category": body.industry or "",
        "_niche": body.industry or "",
        "_city": "",
        "_country": body.country or "",
        "_query": "landing-page-audit",
        "_source": "landing_page",
    }

    loop = asyncio.get_running_loop()
    connector = aiohttp.TCPConnector(ssl=config.VERIFY_SSL, ttl_dns_cache=300)
    timeout_cfg = aiohttp.ClientTimeout(total=30)

    try:
        async with aiohttp.ClientSession(connector=connector, timeout=timeout_cfg) as audit_session:
            audited = await audit_lead(raw, audit_session, skip_competitor_filter=True)
    except Exception as e:
        audited = None

    if not audited or not isinstance(audited, dict):
        audited = {
            "place_id": place_id,
            "business_name": body.business_name,
            "phone": body.phone or "N/A",
            "email": body.email,
            "website": website,
            "niche": body.industry or "",
            "country": body.country or "",
            "lead_score": 0,
            "pain_points": "[]",
            "ops_pain_points": "[]",
            "tech_stack_json": "{}",
            "estimated_monthly_loss": 0,
            "ideal_service": "",
            "source_query": "landing-page-audit",
            "has_website": 0, "has_ssl": 0, "is_mobile_friendly": 0,
            "has_tracking_pixel": 0, "site_dead": 0, "uses_free_email": 0,
            "pagespeed_score": -1, "rating": 0, "review_count": 0,
            "address": "", "city": "",
            "has_facebook": 0, "has_instagram": 0, "has_linkedin": 0,
            "ops_score": 0, "intent_score": 0, "intent_reasons": "[]",
            "decision_maker": None, "decision_maker_title": None, "dm_source": None,
        }
    else:
        audited["email"] = body.email
        audited["phone"] = body.phone or audited.get("phone", "N/A")
        if not audited.get("niche") and body.industry:
            audited["niche"] = body.industry
        if not audited.get("country") and body.country:
            audited["country"] = body.country

    upsert_leads([audited])

    with get_conn() as conn:
        lead_row = conn.execute("SELECT id FROM leads WHERE place_id=?", (place_id,)).fetchone()
    lead_id = lead_row["id"]

    pains = json.loads(audited.get("pain_points", "[]") or "[]")
    ops = json.loads(audited.get("ops_pain_points", "[]") or "[]")
    roi = estimate_revenue_impact(
        audited.get("niche", ""), pains, ops, audited.get("country", "")
    )

    seo = []
    comps = []
    audit_path = await loop.run_in_executor(
        None, lambda: generate_audit_page(lead_id, audited, seo, comps, roi)
    )

    from config import BASE_URL
    audit_url = f"{BASE_URL}{audit_path}"

    with get_conn() as conn:
        conn.execute(
            "INSERT INTO lead_notes (lead_id, note, author) VALUES (?,?,?)",
            (lead_id, f"Audit requested from landing page. Website: {website}", "landing_page"),
        )
        conn.execute(
            "INSERT INTO events (lead_id, event_type, note) VALUES (?,?,?)",
            (lead_id, "audit_requested", f"email={body.email} website={website}"),
        )

    async def _send_audit_email():
        async with _audit_email_sem:
            from outreach import SMTPSession
            from config import YOUR_NAME, YOUR_COMPANY
            try:
                smtp = SMTPSession(None)
                subject = f"Your Free Digital Audit — {body.business_name}"
                body_text = (
                    f"Hi there,\n\n"
                    f"Here's your complimentary Digital Presence Audit for {body.business_name}.\n\n"
                    f"Your personalized audit page is ready:\n{audit_url}\n\n"
                    f"This page includes:\n"
                    f"- Your digital health score\n"
                    f"- Revenue impact of each gap found\n"
                    f"- Technology stack analysis\n"
                    f"- Prioritized fix list\n\n"
                    f"This link expires in 48 hours.\n\n"
                    f"If you'd like to discuss the results or need help fixing any of the issues, "
                    f"just reply to this email — we'd be happy to help.\n\n"
                    f"Best,\n{YOUR_NAME}\n{YOUR_COMPANY}"
                )
                ok, err = smtp.send(body.email, subject, body_text)
                smtp.quit()
                if ok:
                    with get_conn() as conn:
                        conn.execute(
                            "INSERT INTO events (lead_id, event_type, note) VALUES (?,?,?)",
                            (lead_id, "audit_email_sent", f"Audit page emailed to {body.email}"),
                        )
                else:
                    with get_conn() as conn:
                        conn.execute(
                            "INSERT INTO events (lead_id, event_type, note) VALUES (?,?,?)",
                            (lead_id, "audit_email_failed", f"SMTP error: {err}"),
                        )
            except Exception as e:
                with get_conn() as conn:
                    conn.execute(
                        "INSERT INTO events (lead_id, event_type, note) VALUES (?,?,?)",
                        (lead_id, "audit_email_error", str(e)),
                    )

    t = asyncio.create_task(_send_audit_email())
    _background_tasks.add(t)
    t.add_done_callback(_background_tasks.discard)

    return {"ok": True, "status": "created", "audit_url": audit_url, "message": "Your audit is being prepared! Check your email within a few minutes."}


@app.post("/api/webhook/capture")
@limiter.limit("30/minute")
async def capture_webhook(request: Request):
    """Capture email from audit page form (public, no auth)."""
    try:
        data = await request.json()
    except Exception:
        raise HTTPException(400, "Invalid JSON")
    email = (data.get("email") or "").strip()
    token = (data.get("token") or "").strip()
    if not email or not token:
        raise HTTPException(400, "Missing email/token")
    from database import get_conn
    with get_conn() as conn:
        lead = conn.execute(
            "SELECT id, business_name FROM leads WHERE audit_page_token=?",
            (token,),
        ).fetchone()
        if not lead:
            raise HTTPException(404, "Audit token not found")
        conn.execute(
            "INSERT INTO events (lead_id, event_type, note) VALUES (?,?,?)",
            (lead["id"], "captured_email", email),
        )
        # Also record as a note for CRM visibility
        conn.execute(
            "INSERT INTO lead_notes (lead_id, note, author) VALUES (?,?,?)",
            (lead["id"], f"Captured email from audit page: {email}", "system"),
        )
    return {"ok": True, "message": "Email captured"}


@app.get("/api/analytics/cohort")
@limiter.limit("30/minute")
async def analytics_cohort(request: Request, days_back: int = 30, _=Depends(get_current_user)):
    from analytics import get_cohort_analysis
    return get_cohort_analysis(days_back)


@app.get("/api/analytics/ab")
@limiter.limit("30/minute")
async def analytics_ab(request: Request, _=Depends(get_current_user)):
    from analytics import get_ab_test_results
    return get_ab_test_results()


@app.get("/api/analytics/sendtime")
@limiter.limit("30/minute")
async def analytics_sendtime(request: Request, _=Depends(get_current_user)):
    from analytics import get_best_send_time
    return get_best_send_time()


class NotifyBody(BaseModel):
    message: str


@app.post("/api/notify/slack")
@limiter.limit("10/hour")
async def notify_slack(request: Request, body: NotifyBody, _=Depends(get_current_user)):
    from analytics import send_slack_notification
    success = await send_slack_notification(body.message)
    return {"ok": success}


@app.post("/api/notify/discord")
@limiter.limit("10/hour")
async def notify_discord(request: Request, body: NotifyBody, _=Depends(get_current_user)):
    from analytics import send_discord_notification
    success = await send_discord_notification(body.message)
    return {"ok": success}


if __name__ == "__main__":
    import uvicorn; uvicorn.run("app:app",host="0.0.0.0",port=8000,reload=True)