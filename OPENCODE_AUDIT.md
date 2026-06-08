# OpenCode Platform — Code Audit Report

**Date:** 2026-06-08  
**Auditors:** Principal Engineer (Deep Reviewer) + Systems Debugger (Runtime)  
**Scope:** Full-stack — data layer, business logic, communication, API, infrastructure, frontend

---

## Executive Summary

| Severity | Count |
|----------|-------|
| **Critical Bug** | 10 |
| **Architectural Concern** | 14 |
| **Optimization Vector** | 10 |
| **Total** | 34 |

**Top Risks:** Encryption key derived from JWT secret (C-01), destructive DELETE-before-INSERT in intel engine (C-02/C-03), unbounded public webhook endpoint (C-04), SSE queue memory leak (C-05), concurrent `.env` file corruption (C-06), and non-atomic config reload (A-01). The combination of silent exception suppression patterns and missing operational failure logging makes multiple failure modes invisible during production incidents.

---

## 1. Critical Bugs

### C-01 — Encryption Key Derived from JWT Secret
- **File:Line:** `config.py:42-51`
- **Priority:** P0
- **Description:** `_derive_encryption_key()` uses `SHA-256(JWT_SECRET)` → base64 as the Fernet key for all encrypted fields (SMTP passwords, API keys). No salt, no HKDF, no key separation. If the JWT signing key is compromised, all stored secrets are simultaneously exposed.
- **Fix:** Introduce a separate `ENCRYPTION_KEY` environment variable, or use HKDF with a unique application-level salt to derive separate keys from a master secret.

### C-02 — Destructive DELETE Before INSERT: SEO Rankings
- **File:Line:** `intel.py:166-174`
- **Priority:** P0
- **Description:** `rank_check_lead()` deletes all existing SEO ranking rows before confirming new data was successfully fetched. A transient API failure or network timeout permanently wipes all ranking history, with no recovery path.
- **Fix:** Wrap in a transaction with a savepoint; only DELETE after the new INSERT batch commits successfully. Alternatively, use an upsert pattern keyed on (lead_id, keyword, date) and delete stale rows afterwards.

### C-03 — Destructive DELETE Before INSERT: Competitors
- **File:Line:** `intel.py:230-233`
- **Priority:** P0
- **Description:** `find_competitors()` applies the same destructive DELETE-before-INSERT pattern for competitor records. Any upstream fetch failure results in complete data loss for the competitor table.
- **Fix:** Same transactional fix as C-02 — stage new data, commit, then purge old entries.

### C-04 — Unbounded Public Webhook Endpoint
- **File:Line:** `app.py:1420-1421`
- **Priority:** P0
- **Description:** `/api/webhook/capture` is a public-facing endpoint with no rate limiting, no CSRF protection, and no CAPTCHA challenge. An attacker can submit millions of requests, exhausting database connections (the fixed 20-connection pool), inflating storage, and causing denial of service.
- **Fix:** Add `@limiter.limit("30/minute")` decorator, validate `Referer`/`Origin` headers, and consider reCAPTCHA or a proof-of-work challenge for unauthenticated access.

### C-05 — SSE Queue Orphan on Client Disconnect
- **File:Line:** `app.py:169`, corroborated by Source 2 finding #1
- **Priority:** P1
- **Description:** The SSE producer continues to push messages into an `asyncio.Queue` after the client disconnects. The queue accumulates messages indefinitely with no reader, growing memory until the background task completes or the server exhausts available RAM.
- **Fix:** Add a cancellation `asyncio.Event`; on client disconnect, signal the event and have the producer task check it before each queue `.put()`.

### C-06 — Concurrent `.env` File Corruption
- **File:Line:** `app.py:1134`, `config.py:358`
- **Priority:** P1
- **Description:** `api_update_config()` writes to the `.env` file while `reload_config()` concurrently reads from it. Interleaved writes produce a corrupted file, silently breaking all subsequent config reads until the file is manually repaired.
- **Fix:** Use an `asyncio.Lock` or `threading.Lock` guarding all `.env` file I/O. Write to a temp file and atomically `os.replace()` it over the target.

### C-07 — Background Tasks Abandoned on Shutdown
- **File:Line:** `app.py:565-591`
- **Priority:** P1
- **Description:** `api_start_leadgen()` creates tasks via `asyncio.create_task()` but never appends them to the `_background_tasks` set. On server shutdown, these tasks are cancelled mid-operation, potentially leaving database rows in an inconsistent state (partial scraping, half-sent emails).
- **Fix:** Store the task reference: `_background_tasks.add(task)` followed by `task.add_done_callback(_background_tasks.discard)`.

### C-08 — IMAP Password Passed as Ciphertext
- **File:Line:** `outreach.py:798-806`; `database.py:210-218`
- **Priority:** P1
- **Description:** `get_outreach_accounts()` decrypts the stored password, but if `decrypt_password()` encounters an error it returns the raw ciphertext silently (see A-03). Downstream, `check_replies()` passes whatever it receives — potentially undecrypted ciphertext — to `_scan_imap()`, causing cryptic IMAP authentication failures.
- **Fix:** Create a single uniform decryption helper that raises on failure; call it at the point of use. Never propagate ciphertext through the data layer.

### C-09 — Ephemeral JWT Secret Invalidates All Tokens
- **File:Line:** `config.py:300`
- **Priority:** P2
- **Description:** On POSIX, `_load_or_generate_jwt_secret()` uses `fcntl.flock()` for file locking. If locking fails, a bare `except: pass` falls through to an ephemeral in-memory token, invalidating all previously issued JWT tokens and logging out every active user silently.
- **Fix:** Use a cross-platform locking library (`portalocker`, `filelock`) and log a critical error on lock failure rather than silently degrading.

### C-10 — AI Call Blocks Event Loop (Sequential Fallback)
- **File:Line:** `ai_engine.py:60-132`
- **Priority:** P2
- **Description:** `_call_ai()` iterates through 9 models × 3 retries sequentially on a single coroutine. Worst-case latency is 810 seconds, during which the event loop is effectively blocked for all other AI-dependent operations.
- **Fix:** Add a global `asyncio.wait_for()` timeout (e.g., 120s) and use `asyncio.as_completed()` or `asyncio.gather(return_exceptions=True)` to race multiple model calls simultaneously, taking the first successful result.

---

## 2. Architectural Concerns

### A-01 — Non-Atomic Config Reload
- **File:Line:** `config.py:358`
- **Priority:** P1
- **Description:** `reload_config()` updates 40+ module-level globals one at a time. Concurrent calls (e.g., a scheduled reload overlapping with an API-triggered reload) cause a partially-updated state where some globals reflect old values and some reflect new values, leading to unpredictable behavior for the duration of the reload.
- **Fix:** Load all new values into a temporary dict first; validate the dict; then atomically swap all globals in a single critical section under a lock.

### A-02 — Poisoned Connections Returned to Pool
- **File:Line:** `database.py:43-53`
- **Priority:** P1
- **Description:** `get_conn()` returns connections to the pool after rollback from fatal SQLite errors (`SQLITE_CORRUPT`, `SQLITE_FULL`) without any health check. Subsequent consumers receive a connection in an undefined state, causing cascading failures.
- **Fix:** After a rollback from a fatal error code, close the connection and replace it with a fresh one in the pool. Never return a potentially corrupted connection to the shared pool.

### A-03 — Silent Encryption Failure Masking
- **File:Line:** `config.py:54-64`
- **Priority:** P1
- **Description:** `encrypt_password()` catches all exceptions and returns the plaintext input unchanged. `decrypt_password()` catches all exceptions and returns the ciphertext as-is. Both functions mask operational failures (missing key, corrupted key, Fernet token format errors), and the calling code cannot distinguish "decryption succeeded and the password is an encrypted-looking string" from "decryption failed silently."
- **Fix:** Log a critical-level warning with traceback on encryption/decryption failure. Raise a custom `CryptoError` exception so callers can branch on failure. Never silently return unencrypted or ciphertext data.

### A-04 — Connection Pool Exhaustion Under Load
- **File:Line:** `database.py:14`
- **Priority:** P1
- **Description:** A fixed pool of 20 connections with a 15-second timeout is shared across all workloads — web requests, background scraping, email sending, and scheduled tasks. Under peak load (concurrent scraping + public webhook spam + API usage), the pool exhausts and all database-dependent operations fail with timeout errors.
- **Fix:** Increase pool size to at least 50 for concurrent workloads, or partition connections into separate pools by workload type (API vs background). Add connection queue metrics for monitoring.

### A-05 — Migration Ordering Breaks Dependency Chain
- **File:Line:** `database.py:428`
- **Priority:** P1
- **Description:** Migrations are applied in order 42, 43, 48, 49, 44, 45, 46, 47 — non-monotonic. Migration 44 creates a unique index required by migrations 48 and 49, but those execute first, causing them to either fail or produce incorrect results if the index doesn't exist yet.
- **Fix:** Reorder migrations to strictly increasing sequential order: 42, 43, 44, 45, 46, 47, 48, 49. Add a CI check that verifies migration filenames are monotonic.

### A-06 — Multi-Statement Migration Errors Silently Lost
- **File:Line:** `database.py:413`
- **Priority:** P2
- **Description:** `executescript()` runs multiple SQL statements as a single batch. If the first statement succeeds and the second fails, the error is silently swallowed and the transaction may be left in an inconsistent state because `executescript()` issues implicit commits between statements.
- **Fix:** Split multi-statement migrations into individual atomic steps, each in its own migration file. Verify each step independently.

### A-07 — External Content FTS5 Strategy Mixed with Manual Triggers
- **File:Line:** `database.py:298-301`
- **Priority:** P2
- **Description:** The FTS5 full-text search uses external content tables (content-sync mode) but also defines manual INSERT/UPDATE/DELETE triggers to keep the FTS index in sync. External content and manual-trigger strategies are architecturally contradictory and introduce subtle consistency bugs.
- **Fix:** Choose one strategy: either use external content tables with periodic `REBUILD`, or use internal content tables with triggers. Document the decision. Prefer triggers for real-time search accuracy.

### A-08 — FX Cache Concurrent Write Corruption
- **File:Line:** `audit.py:89-187`
- **Priority:** P2
- **Description:** `refresh_fx_rates()` runs in a background thread and writes the FX cache file. Simultaneously, a manual refresh triggered by the API can write the same file. Without a lock, interleaved writes produce a corrupted cache, causing all currency conversions to fail or produce wrong values.
- **Fix:** Protect `_save_fx_cache()` with a `threading.Lock()` used by all FX cache writers.

### A-09 — Non-Atomic Reply Upsert
- **File:Line:** `reply_handler.py:38-60`
- **Priority:** P2
- **Description:** The reply upsert operation does a DELETE followed by INSERT as two separate statements without a transaction wrapper. A crash or concurrent operation between the two leaves the reply table with neither the old record nor the new one.
- **Fix:** Wrap both statements in `BEGIN...COMMIT` or use SQLite's `INSERT OR REPLACE` / `ON CONFLICT...DO UPDATE`.

### A-10 — Generic Exception Retry on Email Send
- **File:Line:** `outreach.py:192-216`
- **Priority:** P2
- **Description:** The email send retry logic catches all exceptions and retries blindly. Non-retryable errors (UnicodeEncodeError, permanent SMTP rejection, invalid recipient) are retried identically to transient errors (timeout, temporary server error), wasting resources and delaying the final failure report.
- **Fix:** Classify exceptions as retryable (timeout, `SMTPDataError` 4xx, `SMTPServerDisconnected`) vs non-retryable (UnicodeError, `SMTPRecipientsRefused`, `SMTPAuthenticationError`). Only retry retryable errors.

### A-11 — AI Retry Treats Permanent and Transient Errors Identically
- **File:Line:** `ai_engine.py:84-88`
- **Priority:** P2
- **Description:** The `_retry` decorator uses `reraise=False`, meaning a permanent error (401 bad API key, 403 forbidden) is retried across all 9 models before failing, instead of short-circuiting immediately. A single bad API key wastes 27 retries before the error surfaces.
- **Fix:** Distinguish permanent HTTP errors (400-level auth/permission) from transient (429, 500, 502, 503). Short-circuit on permanent errors: skip to the next model/provider immediately, don't retry.

### A-12 — Config Fields Invisible in UI
- **File:Line:** `config.py:1094-1095`
- **Priority:** P2
- **Description:** `PHANTOMBUSTER_API_KEY`, `TWILIO_AUTH_TOKEN`, and `LINKEDIN_SESSION_COOKIE` are missing from the `_SECRET` and `_SAFE` config sets. They are invisible in the config UI, preventing administrators from inspecting or rotating these values.
- **Fix:** Add these keys to both `_SECRET` and `_SAFE` sets so they appear (masked) in the configuration interface.

### A-13 — SSE Producer Memory Leak (Systemic)
- **File:Line:** `app.py:175-186`
- **Priority:** P2
- **Description:** Beyond the single-queue orphan (C-05), the SSE producer pattern has no general mechanism to detect consumer disconnection. Every SSE task that feeds a queue continues to do so indefinitely after disconnect, and the accumulated messages are only freed when the server garbage-collects the task.
- **Fix:** Implement a general `ConnectionMonitor` mixin that wraps SSE producers, detecting `asyncio.QueueFull` or `ConnectionResetError` and cancelling the producer task.

### A-14 — fcntl Import Crashes on Windows at Module Load
- **File:Line:** `config.py:9`
- **Priority:** P3
- **Description:** `import fcntl` is executed at module top level. On Windows, this immediately raises `ModuleNotFoundError`, preventing the entire application from starting — even if the code path using `fcntl` is never reached.
- **Fix:** Move the import into `_load_or_generate_jwt_secret()` with a try/except fallback for non-POSIX platforms. Use `portalocker` as a cross-platform alternative.

---

## 3. Optimization Vectors

### O-01 — Substring Spam Detection Matches Legitimate Content
- **File:Line:** `config.py:247-254`
- **Priority:** P2
- **Description:** `SPAM_WORDS` uses naive substring matching — `"free"` matches "freedom", "freeway"; `"100%"` matches "100% uptime guarantee". Email content containing these words in legitimate contexts is incorrectly flagged as spam, reducing deliverability of valid outreach.
- **Fix:** Switch to word-boundary regex matching (`\b{word}\b`) or a lightweight NLP classifier. Compile the regex patterns once at load time for performance.

### O-02 — AI Pool Includes Rate-Limited Free Tier Models
- **File:Line:** `config.py:215`
- **Priority:** P2
- **Description:** The AI model pool includes `:free` tier models with 200 requests/day rate limits alongside paid models. Pool exhaustion occurs when free models hit their daily cap and all subsequent requests fail until the model is rotated out, causing latency spikes as the fallback chain is traversed.
- **Fix:** Separate free and paid models into distinct pools. Deplete the free pool first for low-priority tasks; reserve paid models for high-priority tasks. Add a daily counter that removes free models from rotation after their limit is hit.

### O-03 — HTML Email Filter Blocks Legitimate Domains
- **File:Line:** `audit.py:697-698`
- **Priority:** P2
- **Description:** The email extraction filter checks if a string contains `.png` or `.jpg` anywhere, blocking emails like `info@company-png.com` and `sales@photo.jpg-industries.com`. Legitimate leads with hyphenated image-related domain names are excluded.
- **Fix:** Check only against the TLD/suffix portion of the email: split on `@`, take the domain, check only `domain.endswith(('.png', '.jpg'))`.

### O-04 — Wall-Clock TTL Susceptible to Clock Skew
- **File:Line:** `cache.py:42-43`
- **Priority:** P3
- **Description:** Cache TTL is tracked via `time.time()` which can jump forward or backward due to NTP corrections, daylight savings transitions, or manual clock changes. A backward jump causes cache entries to live indefinitely; a forward jump evicts all entries prematurely.
- **Fix:** Replace `time.time()` with `time.monotonic()` which is monotonic and immune to system clock adjustments.

### O-05 — Race Condition on Cache Initialization
- **File:Line:** `cache.py:15-23`
- **Priority:** P3
- **Description:** `_init_cache()` is called at module import time. If two threads import the module concurrently, the cache is initialized twice, with the second initialization overwriting the first and losing any cached data.
- **Fix:** Defer initialization to `init_db()` or use a module-level `threading.Lock` with a `_cache_initialized` flag to guard against double-init.

### O-06 — Manual HTML Escaping Fragile Under Composition
- **File:Line:** `outreach.py:108-120`
- **Priority:** P3
- **Description:** `_inject_tracking()` performs manual string-replacement HTML escaping. When composed with other escaping functions, it double-encodes `&amp;` → `&amp;amp;`, producing malformed HTML in email bodies.
- **Fix:** Replace manual escaping with `html.escape(s, quote=False)` from the standard library. Apply escaping exactly once at the final serialization point.

### O-07 — Phone Normalization Loses Extensions, Italy Bug
- **File:Line:** `utils.py:45-65`
- **Priority:** P3
- **Description:** The phone normalization function strips extensions (x1234) and has a bug with Italian numbers: leading `0` is incorrectly stripped because the regex removes the international prefix and the trunk prefix together. Valid Italian mobile `+39 347 1234567` becomes `39471234567` (missing the initial digit of the subscriber number).
- **Fix:** Use the `phonenumbers` library (Google's libphonenumber) for all phone parsing and formatting. It handles extensions, Italian leading-zero, and edge cases correctly.

### O-08 — SQLite datetime Timezone Mismatch
- **File:Line:** `intel.py:520-527`
- **Priority:** P3
- **Description:** `_has_recent_intel()` compares a timezone-aware Python `datetime` against a timezone-naive SQLite `datetime` string. The comparison always fails or produces unintended results because the timezone offset is not accounted for in the SQLite side.
- **Fix:** Store all datetimes in SQLite as UTC ISO-8601 strings (`datetime.utcnow().isoformat()`). Convert all Python datetimes to UTC before comparison. Add a column constraint or CHECK to enforce the format.

### O-09 — XSS in Proposal PDF Title
- **File:Line:** `proposal_engine.py:176-328`
- **Priority:** P3
- **Description:** `business_name` is interpolated into the `<title>` tag of the proposal HTML without escaping. If a business name contains `<script>alert(1)</script>`, it injects JavaScript into the rendered PDF when viewed in a browser-based PDF viewer that executes HTML content.
- **Fix:** Apply `html.escape(business_name)` before interpolation into any HTML template context.

### O-10 — FPDF Not Thread-Safe for Concurrent PDF Generation
- **File:Line:** `proposal_engine.py:13`
- **Priority:** P3
- **Description:** FPDF is documented as not thread-safe. When two API requests trigger simultaneous proposal PDF generation, internal state can be corrupted, producing garbled output or crashing the worker thread.
- **Fix:** Instantiate a new FPDF object per request (not per module), or protect the shared instance with a per-thread `threading.Lock()`.

---

## 4. Additional Runtime Findings (Source 2 — Systems Debugger)

### R-01 — Multiple Bare `except: pass` on SMTP/IMAP Failures
- **File:Line:** `outreach.py:190, 222, 865, 879, 906, 912`
- **Priority:** P2
- **Description:** Six locations in the email sending and reply-checking paths use `except: pass`, suppressing all errors including `KeyboardInterrupt` and `SystemExit`. Email failures are invisible, making it impossible to diagnose deliverability issues in production.
- **Fix:** Replace all bare `except: pass` with `except Exception as e: logger.error(...)`. Never suppress `BaseException` subclasses.

### R-02 — Silent JSON Parse Failures in Tech Stack
- **File:Line:** `app.py:940, 961`
- **Priority:** P3
- **Description:** JSON parsing of technology stack data silently swallows `json.JSONDecodeError`, returning partial or empty tech stack results without any indication that the data was malformed.
- **Fix:** Log a warning with the raw input and error details on parse failure. Surface the error in the API response as a partial-result indicator.

### R-03 — AI Engine File Corruption
- **File:Line:** `ai_engine_reconstructed.py`
- **Priority:** P3
- **Description:** A backup/reconstructed copy of `ai_engine.py` contains null bytes, indicating filesystem corruption or an interrupted write. The file may be partially readable and could be imported accidentally if the primary file is missing.
- **Fix:** Delete the corrupted backup file. Restore from version control if a backup is needed. Add a CI check that scans for null bytes in all `.py` files.

---

## 5. Verified Safe (No Action Needed)

| Finding | Verdict |
|---------|---------|
| `audit_pages.py:552` — JSON in Chart.js | `json.dumps()` produces safe output; no XSS vector. |
| `audit_pages.py:529` — Tracking pixel BASE_URL | Config-validated; HTML-escaping correctly applied to all user fields. |
| `app.py:467-490` — f-string SQL | `sort_col` validated against whitelist before interpolation; SQL injection prevented. |
| `index.html` data interpolation | All dynamic content goes through `esc()`; XSS prevention verified. |

---

## 6. Prioritized Action Items

### Immediate (Sprint 0 — Ship Blocker)

| ID | Action | Owner |
|----|--------|-------|
| C-01 | Introduce separate `ENCRYPTION_KEY` env var; migrate existing encrypted data | Backend |
| C-02 | Transactional DELETE-after-INSERT for SEO rankings | Backend |
| C-03 | Transactional DELETE-after-INSERT for competitors | Backend |
| C-04 | Rate-limit `/api/webhook/capture`; add CSRF | Backend |
| C-05 | Add cancellation event to SSE producer; test disconnect path | Backend |
| C-06 | Atomic `.env` write (temp file + `os.replace`) | Backend |
| A-01 | Atomic config reload with temporary dict + lock | Backend |

### High Priority (This Week)

| ID | Action | Owner |
|----|--------|-------|
| A-02 | Connection health check before returning to pool | Backend |
| A-03 | Raise `CryptoError` on encryption/decryption failure | Backend |
| A-04 | Increase connection pool or partition by workload | Backend |
| A-05 | Reorder migrations to monotonic sequence | Backend |
| C-07 | Add all background tasks to `_background_tasks` set | Backend |
| C-08 | Uniform decryption helper; never propagate ciphertext | Backend |
| C-09 | Cross-platform locking for JWT secret file | Backend |
| O-01 | Word-boundary spam word matching | Backend |
| R-01 | Replace bare `except: pass` with logged exceptions | Backend |

### Medium Priority (This Sprint)

| ID | Action | Owner |
|----|--------|-------|
| A-06 | Split multi-statement migrations into atomic steps | Backend |
| A-08 | Threading lock around FX cache writes | Backend |
| A-09 | Transaction wrapper for reply upsert | Backend |
| A-10 | Classify retryable vs non-retryable email errors | Backend |
| A-11 | Short-circuit AI retry on permanent errors | Backend |
| A-12 | Add missing keys to `_SECRET`/`_SAFE` config sets | Backend |
| A-13 | ConnectionMonitor mixin for SSE producers | Backend |
| C-10 | Race AI model calls with timeout | Backend |
| O-02 | Separate free/paid AI model pools | Backend |
| O-03 | Fix email domain filter to check TLD only | Backend |

### Low Priority (Backlog)

| ID | Action | Owner |
|----|--------|-------|
| A-07 | Document and fix FTS5 strategy (choose one approach) | Backend |
| A-14 | Cross-platform import for `fcntl` | Backend |
| O-04 | `time.monotonic()` for cache TTL | Backend |
| O-05 | Guard cache init against double-init race | Backend |
| O-06 | Replace manual escaping with `html.escape` | Backend |
| O-07 | Integrate `phonenumbers` library | Backend |
| O-08 | Consistent UTC datetime storage in SQLite | Backend |
| O-09 | `html.escape()` business_name in proposal PDF | Backend |
| O-10 | Per-request FPDF instance | Backend |
| R-02 | Log JSON parse failures with raw input | Backend |
| R-03 | Remove corrupted `ai_engine_reconstructed.py` | Backend |

---

*Report generated automatically from Principal Engineer Code Audit and Systems Debugger analysis. All line numbers reference the codebase state at audit time and may shift with subsequent commits.*
