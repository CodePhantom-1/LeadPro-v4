# LeadPro v4 — AI-Powered Lead Generation & Multi-Agent Email Outreach

[![Python 3.10+](https://img.shields.io/badge/python-3.10%2B-blue)](https://python.org)
[![FastAPI](https://img.shields.io/badge/FastAPI-0.115+-00a393)](https://fastapi.tiangolo.com)
[![License: MIT](https://img.shields.io/badge/License-MIT-yellow.svg)](LICENSE)

> **💸 Freemium by Design** — LeadPro uses [OpenRouter's](https://openrouter.ai) `openrouter/free` auto-router for all AI generation, which automatically selects from available free models. The only services you may need to pay for are [Serper](https://serper.dev) (lead scraping, ~$50/mo) and optional email sending via [Brevo](https://brevo.com) (free tier: 300 emails/day). Everything else — AI generation, email warmup, analytics, proposal PDFs, and audit pages — runs on free infrastructure.

**LeadPro v4** is a self-hostable, AI-powered lead generation and multi-agent email outreach platform. It discovers leads from Google Maps, Yelp, and web search, audits their websites for ops gaps and revenue opportunities, then sends personalized AI-generated emails from a pool of sender identities — all from a single FastAPI server with an embedded SPA dashboard.

## Table of Contents

- [Features](#features)
- [Quick Start](#quick-start)
- [Email Architecture](#email-architecture)
- [Configuration Reference](#configuration-reference)
- [Web UI Guide](#web-ui-guide)
- [API Reference](#api-reference)
- [Production Deployment](#production-deployment)
- [Database](#database)
- [Background Scheduler](#background-scheduler)
- [Troubleshooting](#troubleshooting)
- [Development](#development)
- [FAQ](#faq)
- [License](#license)

---

## Features

### Lead Generation & Intelligence
- **Multi-source scraping** — Google Places, Serper Maps, Yelp Fusion, Serper Web Search
- **AI query generation** — Builds niche × city grids for targeted discovery
- **Full website audit** — SSL, mobile-friendliness, tracking pixels, social presence, PageSpeed
- **Operations audit** — Tech stack analysis, workflow gaps, estimated monthly revenue loss
- **Intent scoring** — Identifies hot leads based on digital readiness and competitive context
- **Competitor analysis** — Discovers and audits 3–5 local competitors per lead
- **Decision-maker enrichment** — Extracts contact names, titles via Clearbit/Hunter

### Email Outreach
- **Multi-account pool** — Round-robin across unlimited sender identities
- **Brevo/Cloudflare architecture** — Virtual agent emails with central inbox scanning
- **AI-generated emails** — Context-aware, pain-point-specific messaging in 20+ languages
- **4-step drip sequences** — Day 0, 3, 7, 14 follow-ups with auto reply detection
- **Real-time reply scanning** — IMAP inbox monitoring with opt-out detection
- **A/B subject lines** — Two variants, winner selected by open rate
- **Email warmup** — 28-day ramp-up schedule for new domains/accounts
- **Open/click tracking** — Custom tracking domain support

### Platform
- **FastAPI backend** — 50+ REST endpoints + real-time SSE job streaming
- **JWT auth** — Role-based access with secure password hashing
- **SQLite with WAL** — Full relational schema, FTS search, 49-step migration system
- **Background scheduler** — Automated follow-ups, reply scanning, cache cleanup
- **In-memory cache** — TTL-based caching for API responses
- **Rate limiting** — Configurable per-minute and per-day limits
- **Accessible SPA UI** — Keyboard-navigable, screen-reader-friendly, responsive

---

## Quick Start

### Prerequisites

- **Python 3.10+** (3.11 or 3.12 recommended)
- **Chromium** (for Playwright-based web scraping)
- **SMTP account** — Gmail App Password, Brevo, or any SMTP relay
- **API keys** — [Serper.dev](https://serper.dev) (required) + [OpenRouter](https://openrouter.ai) (required)

### 1. Clone & Setup

```bash
git clone https://github.com/yourusername/leadpro.git
cd leadpro
python3 -m venv venv
source venv/bin/activate  # On Windows: venv\Scripts\activate
pip install -r requirements.txt
playwright install chromium
```

### 2. Configure Environment

```bash
cp .env.template .env
nano .env   # Fill in at minimum: SERPER_API_KEY, OPENROUTER_API_KEY
```

Required minimum config:
```env
SERPER_API_KEY=sk-your-serper-key
OPENROUTER_API_KEY=sk-or-your-openrouter-key
YOUR_NAME=Your Name
YOUR_COMPANY=Your Company
```

### 3. Start the Server

```bash
python app.py
```

First run will:
1. Generate a `.jwt_secret` file (for JWT signing)
2. Create the SQLite database (`leadpro.db`) with all migrations
3. Display the default admin password in the terminal

### 4. Login

Open `http://localhost:8000` in your browser.
- Username: `admin`
- Password: _(shown in terminal on first run — copy it immediately)_
- Change password immediately via **Settings → Change Password**

---

## Email Architecture

### Option A: Legacy SMTP (Gmail / Office 365)

Each sender account connects directly via SMTP + IMAP. Best for small-scale use (<50 emails/day per account).

```env
YOUR_EMAIL=you@gmail.com
YOUR_APP_PASSWORD=your-16-char-app-password
SMTP_SERVER=smtp.gmail.com
SMTP_PORT=587
IMAP_SERVER=imap.gmail.com
```

> **Gmail App Passwords**: Generate at https://myaccount.google.com/apppasswords (requires 2FA enabled).

### Option B: Brevo/Cloudflare (Recommended for Scale)

Uses a single Brevo SMTP relay for all sending + Cloudflare Email Routing to collect replies in one inbox. Supports unlimited virtual sender identities.

```
agent1@outreach.yourdomain.com ─┐
agent2@outreach.yourdomain.com ─┤──► Brevo SMTP Relay ──► Recipients
agent3@outreach.yourdomain.com ─┘

*@outreach.yourdomain.com ──► Cloudflare Email Routing ──► Central Gmail Inbox
                                                                  │
                                                          LeadPro scans via IMAP
```

```env
BREVO_SMTP_LOGIN=login@brevo.com
BREVO_SMTP_KEY=xkeysib-xxxxx
CENTRAL_INBOX_EMAIL=central@gmail.com
CENTRAL_INBOX_PASSWORD=app-password
```

---

## Configuration Reference

Full list of all environment variables. See `.env.template` for the latest.

### Required
| Variable | Type | Default | Description |
|----------|------|---------|-------------|
| `SERPER_API_KEY` | string | — | Serper.dev API key (Google Maps/Web search) |
| `OPENROUTER_API_KEY` | string | — | OpenRouter API key (AI generation) |
| `YOUR_NAME` | string | `Alex` | Default sender display name |
| `YOUR_COMPANY` | string | `DD Marketer` | Company name in email footers |

### Email (Legacy SMTP)
| Variable | Type | Default | Description |
|----------|------|---------|-------------|
| `YOUR_EMAIL` | string | — | SMTP login email |
| `YOUR_APP_PASSWORD` | string | — | Gmail App Password (not regular password) |
| `SMTP_SERVER` | string | `smtp.gmail.com` | SMTP hostname |
| `SMTP_PORT` | int | `587` | SMTP port |
| `IMAP_SERVER` | string | `imap.gmail.com` | IMAP hostname for reply scanning |

### Email (Brevo/Cloudflare)
| Variable | Type | Default | Description |
|----------|------|---------|-------------|
| `BREVO_SMTP_LOGIN` | string | — | Brevo SMTP login email |
| `BREVO_SMTP_KEY` | string | — | Brevo SMTP key (not API key) |
| `BREVO_SMTP_HOST` | string | `smtp-relay.brevo.com` | Brevo SMTP host |
| `BREVO_SMTP_PORT` | int | `587` | Brevo SMTP port |
| `CENTRAL_INBOX_EMAIL` | string | — | Gmail inbox for forwarded replies |
| `CENTRAL_INBOX_PASSWORD` | string | — | Gmail App Password for central inbox |
| `CENTRAL_INBOX_IMAP` | string | `imap.gmail.com` | IMAP host for central inbox |

### Integrations
| Variable | Type | Default | Description |
|----------|------|---------|-------------|
| `GOOGLE_PLACES_API_KEY` | string | — | Google Places API (alternative maps) |
| `YELP_API_KEY` | string | — | Yelp Fusion API |
| `CRUNCHBASE_API_KEY` | string | — | Company funding/stage data |
| `HUNTER_API_KEY` | string | — | Email verification/enrichment |
| `CLEARBIT_API_KEY` | string | — | Decision-maker enrichment |
| `PAGESPEED_API_KEY` | string | — | Google PageSpeed Insights |
| `SHOPIFY_API_KEY` | string | — | Shopify e-commerce data |
| `GITHUB_API_KEY` | string | — | GitHub tech stack detection |
| `WHATSAPP_ACCOUNT_SID` | string | — | WhatsApp Business API |
| `TWILIO_ACCOUNT_SID` | string | — | Twilio SMS outreach |
| `PHANTOMBUSTER_API_KEY` | string | — | LinkedIn automation |
| `SLACK_WEBHOOK_URL` | string | — | Slack notifications |
| `DISCORD_WEBHOOK_URL` | string | — | Discord notifications |

### Postfix
| Variable | Type | Default | Description |
|----------|------|---------|-------------|
| `POSTFIX_HOST` | string | — | Local MTA hostname |
| `POSTFIX_PORT` | int | `25` | Local MTA port |

### Tuning
| Variable | Type | Default | Description |
|----------|------|---------|-------------|
| `SCRAPE_THREADS` | int | `30` | Concurrent scraping threads |
| `EMAIL_DELAY_SEC` | int | `20` | Seconds between outbound emails |
| `DAILY_EMAIL_CAP` | int | `200` | Global daily sending limit |
| `MAX_CONCURRENT_TASKS` | int | `5` | Max background tasks |
| `CACHE_TTL_SECONDS` | int | `3600` | Cache expiry in seconds |
| `INTEL_COMPETITOR_COUNT` | int | `3` | Competitors to analyze per lead |
| `INTEL_KEYWORD_COUNT` | int | `5` | SEO keywords per lead |
| `AUDIT_PAGE_EXPIRY_HOURS` | int | `48` | Audit page link expiry |

### Feature Toggles
| Variable | Type | Default | Description |
|----------|------|---------|-------------|
| `SCHEDULER_ENABLED` | bool | `true` | Background job scheduler |
| `WARMUP_ENABLED` | bool | `false` | Email warmup system |
| `USE_LEGACY_LEADGEN` | bool | `false` | Legacy single-source leadgen |
| `REPLY_INTELLIGENCE_ENABLED` | bool | `true` | AI reply classification |
| `AB_TEST_ENABLED` | bool | `true` | A/B subject line testing |
| `FULLTEXT_SEARCH_ENABLED` | bool | `true` | FTS on leads table |
| `VERIFY_SSL` | bool | `true` | SSL verification for outbound |

### Networking
| Variable | Type | Default | Description |
|----------|------|---------|-------------|
| `BASE_URL` | string | `http://localhost:8000` | Public-facing URL |
| `TRACKING_DOMAIN` | string | — | Custom tracking domain |
| `ALLOWED_ORIGINS` | string | `*` | CORS origins (comma-separated) |
| `MAIL_DOMAINS` | string | — | Owned mail domains (comma-separated) |
| `RATE_LIMIT_PER_MINUTE` | int | `60` | API requests/minute/IP |
| `RATE_LIMIT_PER_DAY` | int | `1000` | API requests/day/IP |
| `DB_PATH` | string | `leadpro.db` | SQLite database path |

---

## Web UI Guide

The single-page app has 10 sections accessible from the sidebar:

| Section | Function |
|---------|----------|
| **Dashboard** | Real-time stats: total leads, hot leads, email performance, revenue gap |
| **Lead Gen** | Configure and start AI-powered lead scraping by country/niche |
| **Campaigns** | Create outreach campaigns with score-based lead filtering |
| **Leads** | Browse, filter, search, and export leads with full audit data |
| **Intelligence** | SEO rankings, competitor analysis, executive summaries |
| **Outreach** | Send initial emails, manage follow-ups, WhatsApp messages |
| **Replies** | Scan inboxes, view reply stats, manage opt-outs |
| **Analytics** | Conversion funnel, campaign performance, daily volume charts |
| **Warmup** | Email warmup account management and cycle control |
| **Settings** | Edit .env config, manage SMTP accounts, change password |

---

## API Reference

### Authentication
| Method | Path | Description |
|--------|------|-------------|
| `POST` | `/api/auth/login` | Login with username/password, returns JWT |
| `POST` | `/api/auth/change-password` | Change current user password |
| `GET` | `/api/me` | Get current user info |

### Leads
| Method | Path | Description |
|--------|------|-------------|
| `GET` | `/api/leads` | Paginated leads list with filters |
| `GET` | `/api/leads/{id}` | Full lead detail with scores |
| `GET` | `/api/leads/filters` | Available country/service filter options |
| `GET` | `/api/leads/export` | Export filtered leads as CSV |
| `POST` | `/api/leads/{id}/audit-page` | Generate shareable audit landing page |
| `POST` | `/api/leads/{id}/executive-summary` | AI executive summary |
| `POST` | `/api/leads/{id}/recommendations` | AI recommendations |
| `PUT` | `/api/leads/{id}` | Update lead pipeline stage |

### Lead Generation
| Method | Path | Description |
|--------|------|-------------|
| `POST` | `/api/leadgen/start` | Start multi-source lead generation job |
| `GET` | `/api/leadgen/stream/{jid}` | SSE stream for leadgen progress |

### Campaigns
| Method | Path | Description |
|--------|------|-------------|
| `GET` | `/api/campaigns` | List all campaigns |
| `POST` | `/api/campaigns` | Create new campaign |
| `GET` | `/api/lead-batches` | List available lead batches |
| `PATCH` | `/api/campaigns/{id}/status` | Pause/resume campaign |
| `DELETE` | `/api/campaigns/{id}` | Delete campaign |

### Outreach
| Method | Path | Description |
|--------|------|-------------|
| `GET` | `/api/outreach/preview/{campaign_id}` | Preview leads for sending |
| `POST` | `/api/outreach/start` | Start AI email sending job |
| `GET` | `/api/outreach/stream/{jid}` | SSE stream for outreach progress |
| `POST` | `/api/followups/start` | Start follow-up processing |
| `POST` | `/api/replies/scan` | Scan inboxes for replies |
| `GET` | `/api/replies/stream/{jid}` | SSE stream for reply scanning |

### Intelligence
| Method | Path | Description |
|--------|------|-------------|
| `POST` | `/api/intel/start` | Start SEO/competitor analysis batch |
| `GET` | `/api/intel/stream/{jid}` | SSE stream for intel progress |

### Analytics
| Method | Path | Description |
|--------|------|-------------|
| `GET` | `/api/stats` | Dashboard statistics |
| `GET` | `/api/analytics` | Full analytics (funnel, campaign perf, volume) |
| `GET` | `/api/activity` | Recent activity feed |

### Outreach Accounts
| Method | Path | Description |
|--------|------|-------------|
| `GET` | `/api/outreach-accounts` | List all sender accounts |
| `POST` | `/api/outreach-accounts` | Add new sender account |
| `PUT` | `/api/outreach-accounts/{id}` | Update account |
| `DELETE` | `/api/outreach-accounts/{id}` | Delete account |
| `PATCH` | `/api/outreach-accounts/{id}/toggle` | Enable/disable account |
| `GET` | `/api/outreach-accounts/{id}/test` | Test SMTP connection |

### Warmup
| Method | Path | Description |
|--------|------|-------------|
| `GET` | `/api/warmup/accounts` | List warmup accounts |
| `POST` | `/api/warmup/accounts` | Add warmup account |
| `DELETE` | `/api/warmup/accounts/{id}` | Remove warmup account |
| `POST` | `/api/warmup/run` | Start warmup cycle |
| `GET` | `/api/warmup/stream/{jid}` | SSE stream for warmup progress |
| `GET` | `/api/warmup/placement-summary` | Check inbox placement rates |
| `GET` | `/api/warmup/logs` | Warmup log history |
| `POST` | `/api/warmup/rescue-spam` | Rescue accounts from spam folder |

### Configuration
| Method | Path | Description |
|--------|------|-------------|
| `GET` | `/api/config` | Get safe .env values for UI |
| `PUT` | `/api/config` | Update .env variables |
| `GET` | `/api/smtp/test` | Test legacy SMTP connection |
| `POST` | `/api/brevo/test` | Test Brevo SMTP connection |

### Tracking & Analytics
| Method | Path | Description |
|--------|------|-------------|
| `GET` | `/t/o/{oid}` | Open tracking pixel (1×1 GIF) |
| `GET` | `/t/c/{oid}/{url_b64}` | Click tracking redirect |
| `GET` | `/audit/{token}` | Shareable audit landing page |
| `POST` | `/api/audit/cleanup` | Delete expired audit pages |

### Proposals
| Method | Path | Description |
|--------|------|-------------|
| `POST` | `/api/proposals/generate/{lead_id}` | Generate PDF proposal |
| `GET` | `/api/proposals/{lead_id}/download` | Download PDF proposal |
| `POST` | `/api/proposals/batch` | Generate proposals in batch |

### System
| Method | Path | Description |
|--------|------|-------------|
| `GET` | `/health` | Health check (DB connectivity) |
| `GET` | `/api/me` | Current user + role info |

---

## Production Deployment

### Run with uvicorn directly

```bash
uvicorn app:app --host 0.0.0.0 --port 8000 --workers 2 --log-level info
```

### Run with gunicorn (recommended for multi-worker)

```bash
gunicorn app:app --worker-class uvicorn.workers.UvicornWorker --bind 0.0.0.0:8000 --workers 4 --timeout 120
```

### Systemd service unit

```ini
[Unit]
Description=LeadPro v4
After=network.target

[Service]
Type=simple
User=leadpro
WorkingDirectory=/opt/leadpro
Environment=PATH=/opt/leadpro/venv/bin
ExecStart=/opt/leadpro/venv/bin/uvicorn app:app --host 127.0.0.1 --port 8000
Restart=always
RestartSec=5

[Install]
WantedBy=multi-user.target
```

### Nginx reverse proxy

```nginx
server {
    listen 443 ssl;
    server_name leads.yourdomain.com;

    ssl_certificate /etc/letsencrypt/live/leads.yourdomain.com/fullchain.pem;
    ssl_certificate_key /etc/letsencrypt/live/leads.yourdomain.com/privkey.pem;

    location / {
        proxy_pass http://127.0.0.1:8000;
        proxy_set_header Host $host;
        proxy_set_header X-Real-IP $remote_addr;
        proxy_set_header X-Forwarded-For $proxy_add_x_forwarded_for;
        proxy_set_header X-Forwarded-Proto $scheme;
        proxy_buffering off;  # Required for SSE streaming
        proxy_cache off;
        proxy_read_timeout 86400s;
    }

    # Increase max body size for CSV exports
    client_max_body_size 10M;
}
```

Update environment for production:
```env
BASE_URL=https://leads.yourdomain.com
ALLOWED_ORIGINS=https://leads.yourdomain.com
VERIFY_SSL=true
```

---

## Database

LeadPro uses SQLite with WAL mode for concurrent read/write performance.

### Schema
- **12 tables**, 49 schema migrations
- Full-text search (FTS5) on leads table
- Automatic migration on startup

### Key Tables
| Table | Description |
|-------|-------------|
| `leads` | Core lead data + audit scores + intent signals |
| `campaigns` | Outreach campaigns with country/filters |
| `outreach` | Sent email history with tracking status |
| `outreach_accounts` | Sender identities (SMTP/Brevo) |
| `warmup_accounts` | Warmup senders/receivers |
| `events` | Activity log (emailed, replied, opted_out) |
| `competitors` | Competitor analysis per lead |
| `seo_rankings` | Keyword position tracking |
| `tracking_events` | Open/click pixel tracking |
| `users` | Authentication (admin/team) |

### Backup

```bash
# Create a backup
sqlite3 leadpro.db ".backup leadpro.backup.db"

# Restore from backup
sqlite3 leadpro.db ".restore leadpro.backup.db"

# Or simply copy the file (while server is stopped)
cp leadpro.db leadpro.backup.db
```

### Reset Database

```bash
# Delete and regenerate (all data lost)
rm leadpro.db
python app.py   # Will recreate with fresh migrations
```

---

## Background Scheduler

Enabled by default (`SCHEDULER_ENABLED=true`). Runs as asyncio tasks inside the server process.

| Job | Schedule | Description |
|-----|----------|-------------|
| Auto-follow-ups | Daily 09:00 | Sends due follow-ups (Day 3/7/14) |
| Reply scanning | Hourly | Checks all inboxes for replies |
| Warmup cycle | Daily 10:00 | Sends warmup emails |
| Audit cleanup | Daily 02:00 | Deletes expired audit pages |
| Cache purge | Daily 03:00 | Removes expired cache entries |

---

## Troubleshooting

### "SMTP connection failed"
- **Gmail**: Use an [App Password](https://myaccount.google.com/apppasswords), not your regular password.
- **Brevo**: Use the SMTP key (starts with `xkeysib-`), not the API key.
- Verify `SMTP_SERVER` / `BREVO_SMTP_HOST` is correct.
- Check port: 587 for STARTTLS, 465 for SSL.

### "No leads found"
- Verify `SERPER_API_KEY` has credits at https://serper.dev.
- Try a broad search (country only, no city/industry).
- Check browser console for API error messages.

### "Login modal keeps showing"
- Clear browser local storage (JWT token expired).
- If `.jwt_secret` was deleted, restart the server to regenerate it.
- Check that `JWT_SECRET` in `.env` isn't empty.

### "Port 8000 already in use"
```bash
# Find and kill the process
lsof -i :8000
kill -9 <PID>
# Or use a different port
python app.py --port 8001
```

### "Database is locked"
- SQLite throws `database is locked` under concurrent write pressure.
- This is normal during heavy scraping + email sending.
- The server retries automatically with exponential backoff.
- For multi-instance deployments, consider PostgreSQL migration.

### "playwright: command not found"
```bash
# Install Playwright browsers
playwright install chromium
# Or if playwright is not installed:
pip install playwright && playwright install chromium
```

### "Migration X failed"
- Delete `leadpro.db` and restart (fresh start).
- For existing data, check `_apply_migrations()` in `database.py` for the failing migration.

### How to reset admin password
```bash
# Method 1: Through UI (Settings → Change Password)
# Method 2: Direct database edit (while server is stopped)
sqlite3 leadpro.db "UPDATE users SET password_hash='$2b$12$...' WHERE username='admin';"
# Generate a new bcrypt hash: python -c "import bcrypt; print(bcrypt.hashpw(b'newpass', bcrypt.gensalt()).decode())"
```

### Common SMTP errors
| Error | Meaning | Fix |
|-------|---------|-----|
| `535` | Authentication failed | Check username/password |
| `550` | Mailbox unavailable | Recipient email doesn't exist |
| `554` | Transaction failed | Message content rejected (spam) |
| `421` | Service unavailable | Server is rate-limiting you; wait |

---

## Development

### Project Structure

```
leadpro/
├── app.py                 # FastAPI server + 50+ REST endpoints
├── config.py              # Environment config with safe parsing
├── database.py            # SQLite schema + 49 migrations + connection pool
├── leadgen.py             # Multi-source lead generation engine
├── leadgen_legacy.py      # Legacy single-source leadgen
├── audit.py               # Website + ops + intent audit pipeline
├── audit_pages.py         # Shareable audit landing page generator
├── outreach.py            # Email sending + reply scanning + warmup
├── ai_engine.py           # LLM wrapper with model rotation
├── intel.py               # SEO rankings + competitor analysis
├── scheduler.py           # APScheduler background jobs
├── analytics.py           # Dashboard stats + reporting
├── proposal_engine.py     # PDF proposal generator
├── reply_handler.py       # Email reply classification
├── cache.py               # In-memory TTL cache
├── utils.py               # Shared utility functions
├── index.html             # Single-page accessible web UI
├── new_style.css          # UI stylesheet
├── requirements.txt       # Python dependencies
├── .env.template          # Environment variable template
└── README.md              # This file
```

### Running Tests

```bash
# No formal test suite yet — run the server and test manually
# Check for syntax errors:
python -m py_compile app.py config.py database.py
# Check for import issues:
python -c "from app import app; print('OK')"
```

### Code Style
- Import style: standard library, third-party, local (grouped)
- Async: use `asyncio.to_thread()` or `run_in_executor()` for blocking calls
- DB: always use parameterized queries (never f-strings in SQL)
- Config: all env vars go through `config.py` helper functions

### Adding a New Lead Source
1. Create source class in `leadgen.py` that returns `list[LeadData]`
2. Register in `_get_active_sources()`
3. Add API key handling to `config.py`

### Customizing Email Templates
Edit `ai_engine.py` — `generate_email()` uses dynamically constructed prompts that reference:
- Country/language (20+ languages supported)
- Niche-specific social proof
- Pain points from ops audit
- Estimated monthly revenue loss
- Decision-maker name/title

---

## FAQ

**Can I use this with Outlook/Office 365?**
Yes. Set `SMTP_SERVER=smtp.office365.com`, `SMTP_PORT=587`, and use your Office 365 email + password (or app password if 2FA is enabled). IMAP must be enabled on the mailbox.

**How many leads can I generate per day?**
With default settings (~30 threads): ~500 leads/hour from Serper Maps, plus ~100/hour from Yelp and Google Places. Total: ~5,000–10,000 leads/day depending on API rate limits and query breadth.

**Does this work on Windows?**
Yes. Python 3.10+ runs on Windows. Use `venv\Scripts\activate` instead of `source venv/bin/activate`. For Playwright, run `playwright install chromium` (may need PowerShell admin).

**Can I run multiple instances?**
Yes, but with caveats. SQLite supports multiple readers but single writer. For multi-instance deployments:
- Use a network filesystem (NFS) for shared `leadpro.db`
- Expect occasional `database is locked` errors under write contention
- Consider migrating to PostgreSQL for production scale

**How do I update from v3 to v4?**
v4 is a complete rewrite. Key changes:
- Multi-source leadgen (replaces single-source)
- Brevo/Cloudflare email architecture
- AI model rotation (9+ models)
- Accessible web UI with keyboard navigation
- New database schema (49 migrations)

Database migration from v3 to v4 is not automated. Export leads as CSV from v3 and re-import.

**How do I contribute?**
Fork the repository, make your changes, and submit a pull request. See the [Development](#development) section for code conventions.

---

## License

MIT License — see [LICENSE](LICENSE) for details.

Copyright (c) 2025 LeadPro

Permission is hereby granted, free of charge, to any person obtaining a copy
of this software and associated documentation files (the "Software"), to deal
in the Software without restriction, including without limitation the rights
to use, copy, modify, merge, publish, distribute, sublicense, and/or sell
copies of the Software, and to permit persons to whom the Software is
furnished to do so, subject to the following conditions:

The above copyright notice and this permission notice shall be included in all
copies or substantial portions of the Software.

THE SOFTWARE IS PROVIDED "AS IS", WITHOUT WARRANTY OF ANY KIND, EXPRESS OR
IMPLIED, INCLUDING BUT NOT LIMITED TO THE WARRANTIES OF MERCHANTABILITY,
FITNESS FOR A PARTICULAR PURPOSE AND NONINFRINGEMENT. IN NO EVENT SHALL THE
AUTHORS OR COPYRIGHT HOLDERS BE LIABLE FOR ANY CLAIM, DAMAGES OR OTHER
LIABILITY, WHETHER IN AN ACTION OF CONTRACT, TORT OR OTHERWISE, ARISING FROM,
OUT OF OR IN CONNECTION WITH THE SOFTWARE OR THE USE OR OTHER DEALINGS IN THE
SOFTWARE.
