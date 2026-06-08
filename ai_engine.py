"""
LeadPro v3 — AI Engine
Emails now reference dollar amounts and use owner names.
"""
import json, re, math, random, threading
from openai import OpenAI
from tenacity import retry, stop_after_attempt, wait_exponential, retry_if_exception, RetryError
from config import YOUR_NAME, YOUR_COMPANY, SPAM_WORDS, CACHE_TTL_SECONDS
import config
from cache import cache_get, cache_set
from audit import get_currency_for_country, get_language_for_country

LANG_NAMES = {
    "en": "English", "de": "German", "fr": "French", "es": "Spanish",
    "pt": "Portuguese", "it": "Italian", "nl": "Dutch", "pl": "Polish",
    "cs": "Czech", "ro": "Romanian", "hu": "Hungarian", "hr": "Croatian",
    "el": "Greek", "fi": "Finnish", "da": "Danish", "sv": "Swedish",
    "no": "Norwegian", "ja": "Japanese", "ko": "Korean", "zh": "Chinese",
    "ar": "Arabic", "he": "Hebrew", "tr": "Turkish", "th": "Thai",
    "vi": "Vietnamese", "id": "Indonesian", "ru": "Russian", "uk": "Ukrainian",
}

SPAM_PATTERNS_COMPILED = [
    re.compile(r'\b(?:schedule|book|set up)\s+(?:a\s+)?(?:call|meeting|demo|appointment)\b'),
    re.compile(r'\b(?:hop on|jump on|get on)\s+(?:a\s+)?(?:call|chat)\b'),
    re.compile(r'\b(?:let\'?s?\s+)?(?:connect|chat|talk|discuss)\s+(?:about|on|over)\b'),
    re.compile(r'\bi\s+(?:would\s+)?love\s+to\b'),
    re.compile(r'\b(?:are\s+you\s+)?(?:interested|available|free)\b'),
]

_client = None
_last_api_key = None
_client_lock = threading.Lock()
_cache_lock = threading.Lock()

def _get_client():
    """Lazy-init OpenAI client, picking up any .env reload of the API key."""
    global _client, _last_api_key
    with _client_lock:
        if _client is None or _last_api_key != config.OPENROUTER_API_KEY:
            _client = OpenAI(
                base_url="https://openrouter.ai/api/v1",
                api_key=config.OPENROUTER_API_KEY,
                default_headers={"HTTP-Referer": "https://leadpro.app", "X-Title": "LeadPro-v4"},
            )
            _last_api_key = config.OPENROUTER_API_KEY
    return _client

def _is_rate_limit(exc): return "429" in str(exc)

def _call_ai(prompt, expect_json=True, max_retries=3):
    """Call OpenRouter via openrouter/free auto-router, with model fallback."""
    # ── Cache lookup ──
    cached = cache_get(prompt)
    if cached is not None:
        if expect_json:
            if isinstance(cached, (dict, list)):
                return cached
            try:
                return json.loads(cached)
            except (json.JSONDecodeError, TypeError):
                pass
        else:
            if isinstance(cached, str) and cached.strip():
                return cached.strip()

    _retry = retry(
        stop=stop_after_attempt(max_retries),
        wait=wait_exponential(multiplier=10, min=10, max=60),
        retry=retry_if_exception(_is_rate_limit),
        reraise=False,
    )

    client = _get_client()
    # Try the openrouter/free router first; fall back to individual free models
    models = config.AI_MODELS + config._DEFAULT_FREE_FALLBACKS

    @_retry
    def _attempt(model):
        c = client.chat.completions.create(
            model=model,
            messages=[{"role": "user", "content": prompt}],
            timeout=30,
        )
        return c.choices[0].message.content or ""

    for model in models:
        try:
            raw = _attempt(model)
        except (RetryError, Exception):
            continue
        if not raw or not raw.strip():
            continue

        if not expect_json:
            cache_set(prompt, raw.strip(), ttl=CACHE_TTL_SECONDS)
            return raw.strip()

        cleaned = raw.replace("```json", "").replace("```", "").strip()
        m = re.search(r'[\[\{].*[\]\}]', cleaned, re.DOTALL)
        if not m:
            continue
        try:
            parsed = json.loads(m.group(0))
        except json.JSONDecodeError:
            continue
        cache_set(prompt, parsed, ttl=CACHE_TTL_SECONDS)
        return parsed
    return None


def build_search_queries(country, target_leads):
    country_display = country.upper() if len(country)<=3 else country.title()
    queries_needed = math.ceil(target_leads/15)
    needed_cities = max(3, min(80, math.ceil(queries_needed/15*1.6)))
    niches = _call_ai(f"List exactly 15 high-ticket LOCAL service business niches in {country_display} that need marketing. OUTPUT: JSON list of 15 strings ONLY.")
    cities = _call_ai(f"List top {needed_cities} most populous cities in {country_display}. OUTPUT: JSON list of strings ONLY.")
    if not isinstance(niches,list) or not niches:
        niches = ["Dentist","Roofer","Plumber","HVAC","Lawyer","Chiropractor","Med Spa","Pest Control","Auto Shop","Accountant","Gym","Restaurant","Salon","Vet","Physio"]
    if not isinstance(cities,list) or not cities:
        cities = [f"City {i+1}" for i in range(5)]
    queries = [f"{n} in {c}" for n in niches for c in cities]
    random.shuffle(queries)
    return queries


def _spam_score(text):
    low = text.lower()
    score = sum(1 for w in SPAM_WORDS if w in low)
    if len(text) > 0:
        caps_ratio = sum(1 for c in text if c.isupper()) / len(text)
        if caps_ratio > 0.3:
            score += 2
        if text.count('!') > 2:
            score += 1
        if text.count('$') > 2:
            score += 1
        urgency = ["urgent", "immediately", "asap", "limited", "instant", "hurry",
                    "don't miss", "last chance", "final notice", "act fast"]
        score += sum(1 for w in urgency if w in low)
        for pattern in SPAM_PATTERNS_COMPILED:
            if pattern.search(low):
                score += 1
    return score


def generate_email(business_name, pain_points_json, source_query, country,
                    sequence_step=1, previous_subject="",
                    decision_maker=None, estimated_monthly_loss=0,
                    ops_pain_points_json=None, audit_page_url=None,
                    audit_preview=None, niche=None, currency_symbol=None,
                    language=None, sender_name=None):
    pain_list = []
    if pain_points_json:
        try:
            loaded = json.loads(pain_points_json)
            if isinstance(loaded, list):
                pain_list = loaded
            else:
                pain_list = [loaded] if isinstance(loaded, str) else []
        except json.JSONDecodeError:
            pain_list = [pain_points_json] if isinstance(pain_points_json, str) else []
    
    ops_list = []
    if ops_pain_points_json:
        try:
            loaded = json.loads(ops_pain_points_json)
            if isinstance(loaded, list):
                ops_list = loaded
            else:
                ops_list = []
        except json.JSONDecodeError:
            ops_list = []

    pain_summary = ", ".join(pain_list[:3]) if pain_list else "digital presence gaps"
    top_pain = pain_list[0] if pain_list else "online visibility gaps"
    estimated_monthly_loss = estimated_monthly_loss or 0
    country = country or ""
    cs = currency_symbol or get_currency_for_country(country).get("symbol", "$")
    lang = language or get_language_for_country(country)
    lang_name = LANG_NAMES.get(lang, "English")
    lang_instruction = "" if lang == "en" else f"\n10. Write the ENTIRE email (subject lines AND body) in {lang_name}. All monetary figures must use the {cs} symbol."

    sign_name = (sender_name or YOUR_NAME).split()[0]

    dollar_line = ""
    if estimated_monthly_loss > 500:
        dollar_line = f"\nESTIMATED MONTHLY REVENUE LOSS: {cs}{estimated_monthly_loss:,}"
        if ops_list:
            top_ops = [f"- {p.get('pain', 'Unknown')}: ~{cs}{p.get('monthly_loss',0):,}/mo" for p in ops_list[:3] if isinstance(p, dict) and p.get('monthly_loss',0) > 0]
            if top_ops:
                 dollar_line += "\nBREAKDOWN:\n" + "\n".join(top_ops)

    audit_section = ""
    if audit_page_url and audit_preview:
        if sequence_step == 1:
            audit_section = f"""
PERSONALIZED AUDIT: I built a custom audit for {business_name}. Key findings: {audit_preview}
LINK: {audit_page_url} (expires in 48 hours)
"""
        else:
            audit_section = f"""
PERSONALIZED AUDIT: The audit I sent highlights: {audit_preview}
LINK: {audit_page_url}
"""

    if decision_maker and (parts := decision_maker.strip().split()):
        greeting_name = parts[0]
    else:
        greeting_name = None

    niche_label = niche or (
        source_query.split(" in ")[0].strip()
        if " in " in source_query
        else source_query.strip()
    )

    if sequence_step == 1:
        task_desc = f"""Write a cold email to the owner/decision-maker of {business_name} ({niche_label} in {country}).

RULES — follow ALL of these:
1. HOOK: Open with a specific, surprising observation about their business — NOT a greeting like "Hi" or "Hey". Examples: "Noticed Leaf Gutter isn't showing up in the Chicago map pack" or "Your Google listing has zero reviews in a city with 200+ gutter jobs/month". Use the PROBLEMS data below to craft this.
2. CREDIBILITY: Mention ONE specific finding from the personalized audit as proof you actually researched them. Work the audit link in naturally mid-sentence — like "I ran a quick audit — {audit_page_url}" or "Pulled together some numbers for you: {audit_page_url}".
3. IMPACT: If there's a monetary figure, weave it in as context — not as a headline. Example: "that gap is costing roughly {cs}3k/mo" not "YOU ARE LOSING {cs}3,000/MONTH!!"
4. ASK: Close with a single low-friction question. NOT "Can we hop on a call?" or "Are you free for a chat?". Instead ask something like "Worth a look?" or "Mind if I send over the full breakdown?" or "Want me to pull the same data for your top 3 competitors?"
5. LENGTH: 50-75 words MAX. Every word must earn its place.
6. NEVER use: "I hope this finds you well", "I came across", "I noticed", "reaching out", "just checking in", "I'd love to", "Let's schedule", "Would you be interested", "synergy", "optimize", "leverage", any buzzword.
7. Do NOT start with "Hi", "Hey", or the person's name as a greeting. Start with the hook.
8. If you know the contact's name ({greeting_name or 'unknown'}), use it once naturally mid-email, not as the opener.
9. Sign off as just {sign_name} — no title, no company name, no signature block.
{lang_instruction}

PROBLEMS: {pain_summary}
{dollar_line}
{audit_section}

Generate 2 subject lines. Subject line rules:
- 2-4 words, mostly lowercase, looks like an internal email or quick note
- NEVER use: question marks, exclamation marks, ALL CAPS, "Re:", "Intro", "Quick question", "Opportunity"
- Good examples: "gutter revenue leak", "chicago map pack", "your audit", "leaf gutter numbers"
- Bad examples: "Quick question for Leaf Gutter", "Grow Your Business!", "FREE Audit Report"

OUTPUT: JSON ONLY: {{"subject_a":"...","subject_b":"...","body":"..."}}"""
        json_structure = '{"subject_a":"...","subject_b":"...","body":"..."}'
    else:
        fu_angle = {
            2: "Add new information — share a specific insight or data point they haven't seen yet. Reference the audit as evidence.",
            3: "Create urgency with a concrete reason — e.g. audit expiring, competitor moved on X, seasonal timing. Be specific, not vague.",
            4: "Final touch — very brief (30-40 words). Acknowledge they're busy. Leave the audit link. No pressure, just an open door."
        }.get(sequence_step, "Be brief and add one new piece of value.")

        task_desc = f"""Write follow-up #{sequence_step-1} for {business_name} ({niche_label} in {country}).
Original subject: '{previous_subject}'

ANGLE: {fu_angle}

RULES:
1. Do NOT repeat the first email. This must add new value or context.
2. Keep under 50 words (step 4: under 40 words).
3. Do NOT say "just checking in", "following up", "bumping this", "circling back", or "touching base".
4. If mentioning the audit, reference a SPECIFIC finding — not "your audit" generically.
5. Close with a different low-friction question than the first email. NOT a meeting request.
6. Sign off as just {sign_name}.
{lang_instruction}

PROBLEMS: {pain_summary}
{dollar_line}
{audit_section}

OUTPUT: JSON ONLY: {{"subject":"Re: {previous_subject}","body":"..."}}"""
        json_structure = '{"subject":"Re: '+previous_subject+'","body":"..."}'

    prompt = f"""SENDER: {YOUR_NAME} ({YOUR_COMPANY})
{task_desc}"""

    result = _call_ai(prompt, expect_json=True)
    if not isinstance(result, dict): return None
    if "subject_a" in result and "subject_b" in result:
        sa, sb = result["subject_a"], result["subject_b"]
        result["subject"] = sa if _spam_score(sa) <= _spam_score(sb) else sb
    if _spam_score(result.get("body","")) > 3: return None
    result.setdefault("model_used","openrouter")
    return result


def generate_whatsapp(business_name, pain_points_json, country):
    try: pain_list = json.loads(pain_points_json) if pain_points_json else []
    except Exception: pain_list = []
    pain = pain_list[0] if pain_list else "digital presence"
    country = country or ""
    prompt = f"SENDER: {YOUR_NAME} from {YOUR_COMPANY}.\nRECIPIENT: {business_name} in {country}.\nPROBLEM: {pain}\nWrite ONE casual WhatsApp message (max 2 sentences). End with question.\nOUTPUT: Plain text ONLY."
    return _call_ai(prompt, expect_json=False)


def generate_executive_summary(lead, seo, competitors, gaps, currency_symbol=None, language=None):
    cs = currency_symbol or get_currency_for_country(lead.get("country","")).get("symbol", "$")
    lang = language or get_language_for_country(lead.get("country",""))
    lang_name = LANG_NAMES.get(lang, "English")
    lang_note = "" if lang == "en" else f"\nWrite the ENTIRE summary in {lang_name}. Use {cs} for all monetary figures."
    prompt = f"""Write a 3-paragraph executive summary for a digital audit.
Business: {lead['business_name']} | Industry: {lead.get('niche','')} | Location: {lead.get('city','')}, {lead.get('country','')}
Issues: {lead.get('pain_points','[]')} | Ops Issues: {lead.get('ops_pain_points','[]')}
Est. monthly loss: {cs}{lead.get('estimated_monthly_loss') or 0:,}
SEO: {json.dumps(seo[:5],default=str)} | Competitors: {len(competitors)} | Gaps: {json.dumps(gaps[:5],default=str)}
Be diagnostic, reference specific financial impacts. End with 3 recommendations. Max 250 words.{lang_note}"""
    return _call_ai(prompt, expect_json=False) or "Summary unavailable."


def generate_recommendations(lead, gaps, currency_symbol=None, language=None):
    cs = currency_symbol or get_currency_for_country(lead.get("country","")).get("symbol", "$")
    lang = language or get_language_for_country(lead.get("country",""))
    lang_name = LANG_NAMES.get(lang, "English")
    lang_note = "" if lang == "en" else f"\nWrite ALL recommendations in {lang_name}. Use {cs} for all monetary figures."
    prompt = f"""5 specific actionable recommendations (numbered, 1-2 sentences each).
Business: {lead['business_name']} ({lead.get('niche','')})
Pain: {lead.get('pain_points','[]')} | Ops: {lead.get('ops_pain_points','[]')}
Gaps: {json.dumps(gaps[:5],default=str)} | Monthly loss: {cs}{lead.get('estimated_monthly_loss') or 0:,}
Reference financial impact where possible.{lang_note}"""
    return _call_ai(prompt, expect_json=False) or "1. Consult a digital marketing specialist."


def generate_warmup_email():
    topics = ["Meeting follow-up","Quick question","Checking in","Budget discussion",
              "Document review","Team update","Lunch Tuesday?","Conference notes",
              "Client feedback","Invoice question"]
    topic = random.choice(topics)
    subject = f"{topic} #{random.randint(1,999)}"
    body = _call_ai(f"Write 2-3 sentence casual business email about '{topic}'. No greeting headers. Plain text.",
                    expect_json=False) or "Quick note about this — can we sync this week?"
    return subject, body


def generate_reply_draft(category: str, raw_reply: str, business_name: str, previous_email_body: str) -> str:
    """Generate a suggested reply based on classified response."""
    category_prompts = {
        "INTERESTED": "The lead is interested in learning more. Write a friendly, professional reply that suggests a next step (e.g., call, demo, audit). Keep it concise.",
        "NOT_INTERESTED": "The lead is not interested. Write a polite, short reply that leaves the door open for future contact.",
        "QUESTION": "The lead has a specific question. Write a helpful, concise answer that addresses their question and moves the conversation forward.",
        "OPT_OUT": "The lead wants to opt out. Write a brief, professional apology and confirm they will be removed from future communications.",
        "REFERRAL": "The lead referred someone else. Write a grateful reply and ask for contact details of the referral.",
        "AUTO_REPLY": "This is an auto-reply. Write a courteous acknowledgment and note that you'll follow up when they're back.",
    }
    prompt_template = """
Previous email sent:
{previous_email}

Lead's reply:
{raw_reply}

Category: {category}
Business: {business_name}

Task: {instruction}
Write a suggested reply (2-3 sentences max). Output plain text only, no quotes.
"""
    instruction = category_prompts.get(category, "Write a professional reply.")
    prompt = prompt_template.format(
        previous_email=previous_email_body[:500],
        raw_reply=raw_reply[:500],
        category=category,
        business_name=business_name,
        instruction=instruction
    )
    return _call_ai(prompt, expect_json=False) or "Thank you for your response. We'll follow up accordingly."