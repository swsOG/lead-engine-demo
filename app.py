import csv
import io
import json
import os
import re
import sqlite3
import threading
import time
from datetime import UTC, datetime
from difflib import SequenceMatcher
from pathlib import Path
from urllib.error import HTTPError, URLError
from urllib.parse import urlencode, urljoin, urlparse
from urllib.request import Request, urlopen
from uuid import uuid4

from flask import Flask, Response, jsonify, redirect, render_template, request, url_for


BASE_DIR = Path(__file__).resolve().parent
DATA_DIR = BASE_DIR / "data"
DB_PATH = BASE_DIR / "lead_engine_demo.sqlite3"
SEED_PATH = DATA_DIR / "seed_leads.json"
CACHED_RESULTS_PATH = DATA_DIR / "cached_demo_results.json"
CACHED_LIVE_RESULTS_PATH = DATA_DIR / "cached_live_results.json"
REQUEST_TIMEOUT = 12
LIVE_LEAD_LIMIT = 5

DEFAULT_CONTEXT = {
    "brand": "Humble Grape / Vivat Bacchus",
    "location": "Farringdon / London Bridge, London",
    "offer": (
        "private dining, wine tastings, corporate events, team socials, "
        "client entertainment, Christmas parties"
    ),
    "icp": (
        "Local businesses that may book client dinners, team socials, Christmas parties, "
        "wine tastings, private dining, networking events, or PA-organised corporate hospitality."
    ),
    "category": "Professional services, finance, property, technology, creative agencies",
    "lead_count": 8,
}

PROGRESS_STEPS = [
    ("queued", "Queued", "Your search is queued."),
    ("preparing", "Preparing search", "Setting up the search details."),
    ("sourcing", "Finding or loading leads", {"demo": "Loading demo leads.", "live": "Searching for local businesses."}),
    ("research", "Researching signals", {"demo": "Reading demo research signals.", "live": "Checking research signals and source URLs."}),
    ("drafting", "Scoring and drafting outreach", "Ranking leads and writing draft outreach."),
    ("saving", "Saving to approval workflow", "Saving leads so they can be reviewed."),
    ("complete", "Ready for review", "Leads are ready."),
]
PROGRESS_STEP_KEYS = [step[0] for step in PROGRESS_STEPS]
LIVE_ENV_KEYS = ("SERPAPI_API_KEY", "TAVILY_API_KEY", "GEMINI_API_KEY", "OPENAI_API_KEY", "ANTHROPIC_API_KEY")

app = Flask(__name__)
app.config["JSON_SORT_KEYS"] = False

job_lock = threading.Lock()
running_jobs = {}
gemini_cooldown_until = 0


def parse_local_env_file():
    env_path = BASE_DIR / ".env"
    if not env_path.exists():
        return {}, False
    values = {}
    try:
        lines = env_path.read_text(encoding="utf-8-sig").splitlines()
    except OSError:
        return {}, False
    for raw_line in lines:
        line = raw_line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        if line.startswith("export "):
            line = line.removeprefix("export ").strip()
        key, value = line.split("=", 1)
        key = key.strip().lstrip("\ufeff")
        value = value.strip().strip('"').strip("'")
        if key:
            values[key] = value
    return values, True


def load_local_env():
    values, _readable = parse_local_env_file()
    for key, value in values.items():
        if value and not os.environ.get(key, "").strip():
            os.environ[key] = value


load_local_env()


def env_key_presence(values=None):
    if values is None:
        values, _readable = parse_local_env_file()
    return {key: bool((values.get(key) or "").strip()) for key in LIVE_ENV_KEYS}


def process_key_presence():
    return {key: bool(os.environ.get(key, "").strip()) for key in LIVE_ENV_KEYS}


def env_diagnostics():
    env_path = BASE_DIR / ".env"
    values, readable = parse_local_env_file()
    return {
        "has_env_file": env_path.exists(),
        "env_file_path": str(env_path),
        "env_file_readable": readable,
        "env_keys_present": env_key_presence(values),
        "process_keys_present": process_key_presence(),
        "base_dir": str(BASE_DIR),
    }


def log_startup_env_status():
    diagnostics = env_diagnostics()
    process_keys = diagnostics["process_keys_present"]
    llm_configured = process_keys["GEMINI_API_KEY"] or process_keys["OPENAI_API_KEY"] or process_keys["ANTHROPIC_API_KEY"]
    print(f"Startup config: BASE_DIR={BASE_DIR}")
    print(f"Startup config: .env found={'yes' if diagnostics['has_env_file'] else 'no'}")
    print(f"Startup config: SERPAPI configured={'yes' if process_keys['SERPAPI_API_KEY'] else 'no'}")
    print(f"Startup config: Tavily configured={'yes' if process_keys['TAVILY_API_KEY'] else 'no'}")
    print(f"Startup config: LLM configured={'yes' if llm_configured else 'no'}")


log_startup_env_status()

SOURCE_LABELS = [
    "Demo research profile",
    "Seeded local proximity signal",
    "Demo event-use-case assumption",
]

CATEGORY_PLAYBOOKS = {
    "law": {
        "offer": "private dining",
        "reason": "{name} is a high-priority legal lead because it {signal}. {moment} points to {angle}, especially when a polished room matters more than a casual bar booking.",
        "subject": "Private client dinners near London Bridge",
        "cta": "Would it be worth sending a short private dining note with room formats that work for partner-hosted client dinners?",
    },
    "finance": {
        "offer": "discreet client dinner",
        "reason": "{name} stands out as a finance lead because it {signal}. The buying moment is less about big events and more about {angle}, where privacy and a steady service style matter.",
        "subject": "Discreet client dinners in central London",
        "cta": "Would a concise note on private dining options for investor or client evenings be useful?",
    },
    "recruitment": {
        "offer": "client and candidate drinks",
        "reason": "{name} has a practical hospitality use case: it {signal}. Recruitment teams often need somewhere nearby for relationship-led meetings, so {angle} is a natural opening.",
        "subject": "Client drinks near Farringdon",
        "cta": "Would it be useful if I sent over a short corporate events pack with private dining and tasting options?",
    },
    "creative": {
        "offer": "post-workshop wine tasting",
        "reason": "{name} is relevant because it {signal}. Their workshop and pitch profile creates a specific moment for {angle}, rather than a broad corporate-events pitch.",
        "subject": "A nearby idea for client workshop days",
        "cta": "Would a few sample post-workshop formats be helpful for your client services team?",
    },
    "property": {
        "offer": "landlord and investor dinners",
        "reason": "{name} is a strong property-sector prospect because it {signal}. The local networking rhythm makes {angle} more credible than a generic team-social message.",
        "subject": "Investor dinners around Farringdon",
        "cta": "Would it be helpful if I shared two or three private dining formats for landlord or investor conversations?",
    },
    "tech": {
        "offer": "team social or offsite dinner",
        "reason": "{name} is worth prioritising because it {signal}. For a growing software team, {angle} connects the venue to hiring, retention and team momentum.",
        "subject": "Team dinner idea near London Bridge",
        "cta": "Would you like a short note with team dinner and tasting options that work after an offsite?",
    },
    "event": {
        "offer": "venue partnership showcase",
        "reason": "{name} is different from a direct corporate buyer: it {signal}. The better angle is {angle}, giving their team another local option for client briefs.",
        "subject": "Venue partner idea for London briefs",
        "cta": "Would a venue partner overview be useful for upcoming private dining or wine-led client briefs?",
    },
    "consultancy": {
        "offer": "peer roundtable dinner",
        "reason": "{name} is relevant because it {signal}. Their advisory model creates repeat moments for {angle}, especially when a small group setting is more useful than a large event.",
        "subject": "Roundtable dinner idea near Farringdon",
        "cta": "Would it be useful to see a simple roundtable dinner format for client or peer sessions?",
    },
}

LIVE_QUERY_CATEGORIES = [
    "law firms",
    "recruitment agencies",
    "creative agencies",
    "finance firms",
    "property companies",
    "tech companies",
]

LIVE_CATEGORY_HINTS = {
    "law": ["law", "legal", "solicitor"],
    "finance": ["finance", "wealth", "investment", "capital", "financial"],
    "recruitment": ["recruitment", "staffing", "talent"],
    "creative": ["creative", "agency", "design", "brand", "architecture"],
    "property": ["property", "real estate", "landlord", "commercial"],
    "tech": ["tech", "software", "technology", "digital"],
    "event": ["event", "events", "production"],
}


def get_db():
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    return conn


def init_db():
    with get_db() as conn:
        conn.execute(
            """
            CREATE TABLE IF NOT EXISTS jobs (
                id TEXT PRIMARY KEY,
                status TEXT NOT NULL,
                progress INTEGER NOT NULL DEFAULT 0,
                message TEXT NOT NULL DEFAULT '',
                params TEXT NOT NULL,
                results TEXT,
                created_at TEXT NOT NULL,
                updated_at TEXT NOT NULL,
                error TEXT
            )
            """
        )
        conn.execute(
            """
            CREATE TABLE IF NOT EXISTS leads (
                id TEXT PRIMARY KEY,
                job_id TEXT NOT NULL,
                position INTEGER NOT NULL DEFAULT 0,
                status TEXT NOT NULL DEFAULT 'generated',
                business_name TEXT NOT NULL,
                category TEXT,
                address TEXT,
                website TEXT,
                phone TEXT,
                rating TEXT,
                lead_source TEXT,
                research_mode TEXT,
                provider_used TEXT,
                result_mode TEXT,
                fit_score INTEGER,
                confidence TEXT,
                reason TEXT,
                research_summary TEXT,
                signals_json TEXT NOT NULL DEFAULT '[]',
                source_urls_json TEXT NOT NULL DEFAULT '[]',
                suggested_contact_role TEXT,
                email_subject TEXT,
                email_body TEXT,
                edited_email_subject TEXT,
                edited_email_body TEXT,
                created_at TEXT NOT NULL,
                updated_at TEXT NOT NULL,
                exported_at TEXT,
                recipient_email TEXT,
                recipient_name TEXT,
                recipient_role TEXT,
                contact_source TEXT NOT NULL DEFAULT 'none',
                contact_status TEXT NOT NULL DEFAULT 'not_started',
                email_verification_status TEXT NOT NULL DEFAULT 'unverified',
                email_verification_reason TEXT,
                instantly_status TEXT NOT NULL DEFAULT 'not_ready',
                instantly_pushed_at TEXT,
                instantly_error TEXT
            )
            """
        )
        conn.execute(
            """
            CREATE TABLE IF NOT EXISTS suppression_list (
                id TEXT PRIMARY KEY,
                business_name TEXT,
                website TEXT,
                domain TEXT,
                email TEXT,
                reason TEXT,
                created_at TEXT NOT NULL
            )
            """
        )
        conn.execute(
            """
            CREATE TABLE IF NOT EXISTS lead_events (
                id TEXT PRIMARY KEY,
                lead_id TEXT NOT NULL,
                event_type TEXT NOT NULL,
                metadata_json TEXT NOT NULL DEFAULT '{}',
                created_at TEXT NOT NULL
            )
            """
        )
        conn.execute(
            """
            CREATE TABLE IF NOT EXISTS audit_log (
                id TEXT PRIMARY KEY,
                lead_id TEXT NOT NULL,
                action TEXT NOT NULL,
                old_status TEXT,
                new_status TEXT,
                timestamp TEXT NOT NULL
            )
            """
        )
        conn.execute(
            """
            CREATE TABLE IF NOT EXISTS exports (
                id TEXT PRIMARY KEY,
                created_at TEXT NOT NULL,
                lead_count INTEGER NOT NULL,
                file_name TEXT NOT NULL,
                export_type TEXT NOT NULL
            )
            """
        )
        ensure_schema(conn)


def ensure_schema(conn):
    lead_columns = {row["name"] for row in conn.execute("PRAGMA table_info(leads)").fetchall()}
    lead_defaults = {
        "exported_at": "TEXT",
        "recipient_email": "TEXT",
        "recipient_name": "TEXT",
        "recipient_role": "TEXT",
        "contact_source": "TEXT NOT NULL DEFAULT 'none'",
        "contact_status": "TEXT NOT NULL DEFAULT 'not_started'",
        "email_verification_status": "TEXT NOT NULL DEFAULT 'unverified'",
        "email_verification_reason": "TEXT",
        "instantly_status": "TEXT NOT NULL DEFAULT 'not_ready'",
        "instantly_pushed_at": "TEXT",
        "instantly_error": "TEXT",
    }
    for column, definition in lead_defaults.items():
        if column not in lead_columns:
            conn.execute(f"ALTER TABLE leads ADD COLUMN {column} {definition}")
    conn.execute("UPDATE leads SET status = 'in_review' WHERE status = 'reviewed'")
    conn.execute("UPDATE leads SET contact_source = 'none' WHERE contact_source IS NULL")
    conn.execute("UPDATE leads SET contact_status = 'not_started' WHERE contact_status IS NULL")
    conn.execute("UPDATE leads SET email_verification_status = 'unverified' WHERE email_verification_status IS NULL")
    conn.execute("UPDATE leads SET instantly_status = 'not_ready' WHERE instantly_status IS NULL")
    conn.execute("CREATE INDEX IF NOT EXISTS idx_leads_status ON leads(status)")
    conn.execute("CREATE INDEX IF NOT EXISTS idx_leads_job_id ON leads(job_id)")
    conn.execute("CREATE INDEX IF NOT EXISTS idx_leads_instantly_status ON leads(instantly_status)")
    conn.execute("CREATE INDEX IF NOT EXISTS idx_lead_events_lead_id ON lead_events(lead_id)")
    conn.execute("CREATE INDEX IF NOT EXISTS idx_audit_log_lead_id ON audit_log(lead_id)")
    conn.execute("CREATE INDEX IF NOT EXISTS idx_suppression_domain ON suppression_list(domain)")


def load_seed_leads():
    with SEED_PATH.open("r", encoding="utf-8") as handle:
        return json.load(handle)


def get_config():
    return {
        "serpapi_key": os.environ.get("SERPAPI_API_KEY", "").strip(),
        "tavily_key": os.environ.get("TAVILY_API_KEY", "").strip(),
        "gemini_key": os.environ.get("GEMINI_API_KEY", "").strip(),
        "openai_key": os.environ.get("OPENAI_API_KEY", "").strip(),
        "anthropic_key": os.environ.get("ANTHROPIC_API_KEY", "").strip(),
        "hunter_key": os.environ.get("HUNTER_API_KEY", "").strip(),
        "instantly_key": os.environ.get("INSTANTLY_API_KEY", "").strip(),
        "instantly_campaign_id": os.environ.get("INSTANTLY_CAMPAIGN_ID", "").strip(),
        "instantly_list_id": os.environ.get("INSTANTLY_LIST_ID", "").strip(),
        "gemini_model": os.environ.get("GEMINI_MODEL", "gemini-2.5-flash").strip(),
        "openai_model": os.environ.get("OPENAI_MODEL", "gpt-5-mini").strip(),
        "anthropic_model": os.environ.get("ANTHROPIC_MODEL", "claude-haiku-4-5").strip(),
    }


def live_readiness():
    config = get_config()
    has_serpapi = bool(config["serpapi_key"])
    has_tavily = bool(config["tavily_key"])
    has_gemini = bool(config["gemini_key"])
    has_openai = bool(config["openai_key"])
    has_anthropic = bool(config["anthropic_key"])
    has_any_llm = has_gemini or has_openai or has_anthropic
    if not has_serpapi:
        likely_behavior = "Live mode will fall back to seed demo because SERPAPI_API_KEY is missing."
    elif has_tavily and has_any_llm:
        likely_behavior = "Live sourcing is available."
    else:
        likely_behavior = "Live sourcing is available, but research/drafting may use fallback logic."
    return {
        "has_serpapi": has_serpapi,
        "has_tavily": has_tavily,
        "has_gemini": has_gemini,
        "has_openai": has_openai,
        "has_anthropic": has_anthropic,
        "has_any_llm": has_any_llm,
        "live_sourcing_ready": has_serpapi,
        "live_research_ready": has_tavily,
        "live_drafting_ready": has_any_llm,
        "likely_behavior": likely_behavior,
        **env_diagnostics(),
    }


def request_json(url, *, method="GET", payload=None, headers=None, timeout=REQUEST_TIMEOUT):
    body = None
    request_headers = {
        "User-Agent": "LocalHospitalityLeadEngine/1.0",
        "Accept": "application/json",
        **(headers or {}),
    }
    if payload is not None:
        body = json.dumps(payload).encode("utf-8")
        request_headers = {"Content-Type": "application/json", **request_headers}
    request = Request(url, data=body, headers=request_headers, method=method)
    try:
        with urlopen(request, timeout=timeout) as response:
            return json.loads(response.read().decode("utf-8"))
    except HTTPError as exc:
        detail = exc.read().decode("utf-8", errors="replace")
        raise RuntimeError(f"HTTP {exc.code}: {detail}") from exc
    except (URLError, TimeoutError) as exc:
        raise RuntimeError(str(exc)) from exc


def safe_int(value, default=0):
    try:
        return int(value)
    except (TypeError, ValueError):
        return default


def confidence_value(value):
    if isinstance(value, (int, float)):
        return float(value)
    return {"high": 0.88, "medium": 0.68, "low": 0.45}.get(str(value).lower(), 0.65)


def infer_company_type(text):
    lowered = text.lower()
    for company_type, hints in LIVE_CATEGORY_HINTS.items():
        if any(hint in lowered for hint in hints):
            return company_type
    return "consultancy"


def domain_from_url(url):
    if not url:
        return ""
    parsed = urlparse(url if "://" in url else f"https://{url}")
    return parsed.netloc.replace("www.", "")


def now_iso():
    return datetime.now(UTC).isoformat(timespec="seconds")


def active_email_subject(lead):
    return lead.get("edited_email_subject") or lead.get("email_subject") or ""


def active_email_body(lead):
    return lead.get("edited_email_body") or lead.get("email_body") or ""


def split_name(full_name):
    parts = (full_name or "").strip().split()
    if not parts:
        return "", ""
    if len(parts) == 1:
        return parts[0], ""
    return parts[0], " ".join(parts[1:])


def update_job(job_id, **updates):
    updates["updated_at"] = now_iso()
    columns = ", ".join(f"{key} = ?" for key in updates)
    values = list(updates.values()) + [job_id]
    with get_db() as conn:
        conn.execute(f"UPDATE jobs SET {columns} WHERE id = ?", values)


def update_job_step(job_id, step_key, **updates):
    with get_db() as conn:
        row = conn.execute("SELECT params FROM jobs WHERE id = ?", (job_id,)).fetchone()
    params = json.loads(row["params"]) if row and row["params"] else {}
    params["current_step"] = step_key
    update_job(job_id, params=json.dumps(params), **updates)


def progress_mode(params):
    mode = params.get("mode", "seed_demo")
    return "live" if mode in {"live_if_available", "live_required"} else "demo"


def progress_step_detail(detail, params):
    if isinstance(detail, dict):
        return detail[progress_mode(params)]
    return detail


def progress_step_label(key, label, params):
    if params.get("mode") != "live_required":
        return label
    return {
        "preparing": "Preparing live search",
        "sourcing": "Searching live local businesses",
        "research": "Researching live signals",
        "drafting": "Scoring and drafting outreach",
        "saving": "Saving live leads to approval workflow",
    }.get(key, label)


def derive_current_step(status, progress, params):
    current_step = params.get("current_step")
    if current_step in PROGRESS_STEP_KEYS:
        return current_step
    if status == "complete":
        return "complete"
    if status == "queued":
        return "queued"
    if progress >= 92:
        return "saving"
    if progress >= 82:
        return "drafting"
    if progress >= 60:
        return "research"
    if progress >= 35:
        return "sourcing"
    if progress >= 15:
        return "preparing"
    return "queued"


def progress_metadata(status, progress, params):
    current_step = derive_current_step(status, progress, params)
    current_index = PROGRESS_STEP_KEYS.index(current_step)
    steps = []
    for index, (key, label, detail) in enumerate(PROGRESS_STEPS):
        if status == "failed":
            if index < current_index:
                state = "complete"
            elif index == current_index:
                state = "failed"
            else:
                state = "pending"
        elif status == "complete":
            state = "complete"
        elif index < current_index:
            state = "complete"
        elif index == current_index:
            state = "current"
        else:
            state = "pending"
        steps.append(
            {
                "key": key,
                "label": progress_step_label(key, label, params),
                "detail": progress_step_detail(detail, params),
                "state": state,
            }
        )
    return current_step, steps


def score_lead(lead, params):
    text = " ".join(
        [
            lead.get("name", lead.get("business_name", "")),
            lead.get("category", ""),
            lead.get("company_type", ""),
            lead.get("likely_event_use_case", ""),
            lead.get("business_signal", ""),
            lead.get("buying_moment", ""),
            lead.get("relevance_angle", ""),
            lead.get("likely_pain_or_need", ""),
            lead.get("why_now", ""),
            " ".join(lead.get("signals", [])),
        ]
    ).lower()
    offer_terms = [
        "corporate",
        "client",
        "team",
        "event",
        "networking",
        "christmas",
        "private",
        "dining",
        "wine",
        "pa",
        "office",
    ]
    location_terms = [
        term.strip().lower()
        for term in params.get("location", "").replace("/", ",").split(",")
        if term.strip()
    ]
    category_terms = [
        term.strip().lower()
        for term in params.get("category", "").replace("/", ",").split(",")
        if term.strip()
    ]

    signal_score = min(len(lead.get("signals", [])) * 3, 18)
    offer_score = sum(3 for term in offer_terms if term in text)
    location_score = sum(5 for term in location_terms if term and term in lead.get("address", "").lower())
    category_score = sum(4 for term in category_terms if term and term in text)
    confidence_score = int(confidence_value(lead.get("confidence", 0.75)) * 10)
    base_score = int(lead.get("base_fit_score", 48))

    return max(1, min(100, base_score + signal_score + offer_score + location_score + category_score + confidence_score))


def score_live_lead(lead, params, research):
    text = " ".join(
        [
            lead.get("name", ""),
            lead.get("category", ""),
            lead.get("address", ""),
            research.get("summary", ""),
            " ".join(research.get("signals", [])),
        ]
    ).lower()
    score = 45
    if any(place.strip().lower() in text for place in params.get("location", "").replace("/", ",").split(",")):
        score += 10
    for term in ["client", "clients", "corporate", "services", "advisory", "consulting"]:
        if term in text:
            score += 6
            break
    for term in ["team", "careers", "hiring", "people"]:
        if term in text:
            score += 6
            break
    for term in ["event", "events", "dinner", "hospitality", "meeting", "workshop"]:
        if term in text:
            score += 5
            break
    if lead.get("website"):
        score += 4
    if lead.get("rating"):
        score += 2
    score += int(confidence_value(research.get("confidence", "medium")) * 10)
    return max(1, min(100, score))


def playbook_for(lead):
    return CATEGORY_PLAYBOOKS.get(lead.get("company_type", "").lower(), CATEGORY_PLAYBOOKS["consultancy"])


def build_reason(lead):
    playbook = playbook_for(lead)
    return playbook["reason"].format(
        name=lead["business_name"],
        signal=lead["business_signal"],
        moment=lead["buying_moment"],
        angle=lead["relevance_angle"],
    )


def build_research_summary(lead):
    return (
        f"{lead['company_type'].title()} profile: {lead['business_signal']}. "
        f"{lead['distance_hint']}. Likely need: {lead['likely_pain_or_need']}. "
        f"Timing note: {lead['why_now']}."
    )


def build_email(lead, params):
    brand = params["brand"]
    playbook = playbook_for(lead)
    subject = playbook["subject"]
    body = (
        f"Hi {lead['suggested_contact_role']},\n\n"
        f"{lead['business_signal'].capitalize()}, so the useful angle is not a broad venue pitch.\n\n"
        f"For {brand}, I would lead with {lead['relevance_angle']}. It gives your team "
        f"{playbook['offer']} for {lead['likely_event_use_case']} while helping with "
        f"{lead['likely_pain_or_need']}.\n\n"
        f"{playbook['cta']}\n\n"
        "Best,\n"
        f"Events Team - on behalf of {brand}"
    )
    return subject, body


def build_live_queries(params):
    location = params.get("location", DEFAULT_CONTEXT["location"])
    location_bits = [bit.strip() for bit in location.replace("/", ",").split(",") if bit.strip()]
    useful_locations = location_bits[:2] or ["Farringdon London", "London Bridge London"]
    category_text = params.get("category", "")
    category_matches = [category for category in LIVE_QUERY_CATEGORIES if category.split()[0] in category_text.lower()]
    categories = category_matches or LIVE_QUERY_CATEGORIES
    queries = []
    for category in categories:
        for place in useful_locations:
            query_place = place if "london" in place.lower() else f"{place} London"
            queries.append(f"{category} near {query_place}")
    return queries


def normalize_serpapi_result(item):
    links = item.get("links") or {}
    website = item.get("website") or links.get("website") or ""
    source_urls = [
        value
        for value in [
            website,
            item.get("place_id_search"),
            item.get("reviews_link"),
            item.get("gps_coordinates") and item.get("maps_url"),
        ]
        if isinstance(value, str) and value.startswith("http")
    ]
    return {
        "name": item.get("title", "").strip(),
        "business_name": item.get("title", "").strip(),
        "category": item.get("type") or ", ".join(item.get("types", [])[:2]) or "Local business",
        "address": item.get("address", ""),
        "website": website,
        "phone": item.get("phone", ""),
        "rating": item.get("rating", ""),
        "source": "SerpAPI Google Maps",
        "lead_source": "SerpAPI Google Maps",
        "source_urls": source_urls,
        "company_type": infer_company_type(f"{item.get('title', '')} {item.get('type', '')} {' '.join(item.get('types', []))}"),
    }


def source_live_leads(input_data):
    config = get_config()
    if not config["serpapi_key"]:
        raise RuntimeError("SERPAPI_API_KEY is not configured")

    leads = []
    seen = set()
    for query in build_live_queries(input_data):
        if len(leads) >= LIVE_LEAD_LIMIT:
            break
        print(f"API call: SerpAPI Google Maps query={query!r}")
        url = "https://serpapi.com/search?" + urlencode(
            {
                "engine": "google_maps",
                "type": "search",
                "q": query,
                "api_key": config["serpapi_key"],
            }
        )
        data = request_json(url)
        for item in data.get("local_results", []):
            lead = normalize_serpapi_result(item)
            if not lead["name"]:
                continue
            key = (lead["name"].lower(), lead.get("address", "").lower())
            if key in seen:
                continue
            seen.add(key)
            leads.append(lead)
            if len(leads) >= LIVE_LEAD_LIMIT:
                break
    if not leads:
        raise RuntimeError("SerpAPI returned no usable local results")
    return leads


def extract_research_signals(tavily_results, lead):
    signals = []
    keywords = [
        "clients",
        "services",
        "team",
        "careers",
        "events",
        "corporate",
        "advisory",
        "workshops",
        "partners",
        "projects",
    ]
    for result in tavily_results:
        content = (result.get("content") or "").strip()
        lowered = content.lower()
        for keyword in keywords:
            if keyword in lowered and content:
                signals.append(content[:180])
                break
        if len(signals) >= 3:
            break
    if not signals:
        category = lead.get("category", "business")
        signals.append(f"Inference: as a {category}, the company may have client or team hosting moments.")
    return signals


def research_live_lead(lead, input_data):
    config = get_config()
    if not config["tavily_key"]:
        return fallback_research_for_live_lead(lead)

    domain = domain_from_url(lead.get("website", ""))
    query_parts = [
        lead.get("name", ""),
        domain,
        lead.get("category", ""),
        input_data.get("location", ""),
        "team clients events about services careers news",
    ]
    query = " ".join(part for part in query_parts if part).strip()
    print(f"API call: Tavily search lead={lead.get('name')!r}")
    payload = {
        "query": query,
        "search_depth": "basic",
        "include_answer": True,
        "include_raw_content": False,
        "max_results": 5,
    }
    try:
        data = request_json(
            "https://api.tavily.com/search",
            method="POST",
            payload=payload,
            headers={"Authorization": f"Bearer {config['tavily_key']}"},
        )
    except RuntimeError as exc:
        print(f"WARNING: Tavily failed for {lead.get('name')}: {exc}")
        return fallback_research_for_live_lead(lead)

    results = data.get("results", [])
    urls = [item.get("url") for item in results if item.get("url")]
    summary = (data.get("answer") or "").strip()
    if not summary and results:
        summary = " ".join((item.get("content") or "").strip() for item in results[:2]).strip()[:600]
    if not summary:
        summary = f"Limited live research found for {lead.get('name')}; use this as a low-confidence prospect."
    return {
        "summary": summary,
        "signals": extract_research_signals(results, lead),
        "source_urls": urls or lead.get("source_urls", []),
        "confidence": "high" if len(urls) >= 3 else "medium" if urls else "low",
        "research_mode": "live",
    }


def fallback_research_for_live_lead(lead):
    category = lead.get("category", "local business")
    return {
        "summary": (
            f"Inference: {lead.get('name')} appears to be a {category} based on the sourced local listing. "
            "Use live web research before treating this as a confirmed buying signal."
        ),
        "signals": [
            f"Inference: the listing category '{category}' may indicate client or team meetings.",
            "Inference: central London proximity may make nearby hospitality practical.",
        ],
        "source_urls": lead.get("source_urls", []),
        "confidence": "low",
        "research_mode": "fallback",
    }


def live_offer_for(lead, research):
    text = f"{lead.get('category', '')} {research.get('summary', '')} {' '.join(research.get('signals', []))}".lower()
    company_type = infer_company_type(text)
    return CATEGORY_PLAYBOOKS.get(company_type, CATEGORY_PLAYBOOKS["consultancy"])["offer"]


def deterministic_live_email(lead, research, params):
    brand = params["brand"]
    signal = research.get("signals", ["Inference: central London proximity may make nearby hospitality practical."])[0]
    offer = live_offer_for(lead, research)
    subject = f"{offer.title()} for {lead['name']}"
    role = "Office Manager"
    body = (
        f"Hi {role},\n\n"
        f"{lead['name']} came up in local research around {lead.get('category', 'professional services')}. "
        f"One useful signal is: {signal}\n\n"
        f"For {brand}, I would keep the angle narrow: {offer} for a small client or team moment, rather than a broad events pitch. "
        "It could suit occasions where the venue needs to feel considered, central and easy to organise.\n\n"
        "Would a short venue note with one or two relevant formats be useful?\n\n"
        f"Best,\nEvents Team — on behalf of {brand}"
    )
    return subject, trim_email_to_word_window(body)


def trim_email_to_word_window(body):
    words = body.split()
    if len(words) <= 120:
        return body
    return " ".join(words[:116]) + "\n\nBest,\nEvents Team — on behalf of Humble Grape / Vivat Bacchus"


def deterministic_live_output(lead, research, params, provider="deterministic"):
    subject, body = deterministic_live_email(lead, research, params)
    signal = research.get("signals", ["Inference: live research was limited, so this is a low-confidence prospect."])[0]
    return {
        "fit_score": score_live_lead(lead, params, research),
        "reason": (
            f"{lead['name']} is relevant because the live research points to this signal: {signal} "
            f"The outreach angle should stay focused on {live_offer_for(lead, research)}."
        ),
        "suggested_contact_role": "Office Manager",
        "email_subject": subject,
        "email_body": body,
        "confidence": research.get("confidence", "medium"),
        "llm_provider": provider,
        "provider_used": "Fallback",
    }


def parse_llm_json(text):
    stripped = text.strip()
    if stripped.startswith("```"):
        stripped = stripped.strip("`")
        stripped = stripped.removeprefix("json").strip()
    start = stripped.find("{")
    end = stripped.rfind("}")
    if start == -1 or end == -1:
        raise ValueError("No JSON object found")
    return json.loads(stripped[start : end + 1])


def llm_prompt(lead, research, params):
    return (
        "Return strict JSON only with keys: fit_score, reason, suggested_contact_role, "
        "email_subject, email_body, confidence. Use only the supplied research. Label inferences. "
        "Email body must be 80-120 words, mention one relevant offer only, avoid generic lines, "
        "and sign off exactly as 'Events Team — on behalf of Humble Grape / Vivat Bacchus'.\n\n"
        f"Brand: {params['brand']}\nOffer: {params['offer']}\nICP: {params['icp']}\n"
        f"Lead: {json.dumps(lead)}\nResearch: {json.dumps(research)}"
    )


GEMINI_SCORE_SCHEMA = {
    "type": "object",
    "properties": {
        "score": {"type": "integer"},
        "reason": {"type": "string"},
        "contact_role": {"type": "string"},
    },
    "required": ["score", "reason", "contact_role"],
}

GEMINI_DRAFT_SCHEMA = {
    "type": "object",
    "properties": {
        "subject": {"type": "string"},
        "body": {"type": "string"},
        "angle": {"type": "string"},
    },
    "required": ["subject", "body", "angle"],
}


def gemini_json(prompt, schema):
    config = get_config()
    if not config["gemini_key"]:
        raise RuntimeError("GEMINI_API_KEY is not configured")
    try:
        from google import genai
    except ImportError as exc:
        raise RuntimeError("google-genai is not installed") from exc

    client = genai.Client(api_key=config["gemini_key"])
    last_error = None
    for attempt in range(1, 4):
        try:
            response = client.models.generate_content(
                model=config["gemini_model"],
                contents=prompt,
                config={
                    "response_mime_type": "application/json",
                    "response_schema": schema,
                },
            )
            return parse_llm_json(response.text)
        except Exception as exc:
            last_error = exc
            message = str(exc)
            retryable = "503" in message or "UNAVAILABLE" in message or "high demand" in message
            if not retryable or attempt == 3:
                break
            wait_seconds = attempt * 2
            print(f"WARNING: Gemini temporary failure, retrying in {wait_seconds}s: {message[:180]}")
            time.sleep(wait_seconds)
    raise last_error


def call_gemini_score_lead(lead, research, params):
    print(f"API call: Gemini score lead={lead.get('name')!r}")
    prompt = (
        "You are scoring a hospitality lead for Humble Grape / Vivat Bacchus. "
        "Return strict JSON only with score, reason, contact_role. "
        "Score must be an integer from 1 to 10. Use only supplied research. "
        "If something is inferred, label it as an inference. Avoid generic phrases like strong fit.\n\n"
        f"Brand: {params['brand']}\nOffer: {params['offer']}\nICP: {params['icp']}\n"
        f"Lead: {json.dumps(lead)}\nResearch: {json.dumps(research)}"
    )
    output = gemini_json(prompt, GEMINI_SCORE_SCHEMA)
    return {
        "score": max(1, min(10, safe_int(output.get("score"), 6))),
        "reason": str(output.get("reason", "")).strip(),
        "contact_role": str(output.get("contact_role", "Office Manager")).strip() or "Office Manager",
    }


def call_gemini_draft_email(lead, research, params, score_output):
    print(f"API call: Gemini draft lead={lead.get('name')!r}")
    prompt = (
        "Write a concise sales email for a local hospitality lead. Return strict JSON only with subject, body, angle. "
        "Email body rules: 80-120 words; specific to the lead; use one real signal from research or clearly labelled inference; "
        "mention one relevant offer only; do not say I hope you're well; do not say I wanted to reach out; "
        "do not use generic phrases like strong fit; include one clear CTA; sign off exactly as: "
        "Events Team — on behalf of Humble Grape / Vivat Bacchus.\n\n"
        f"Brand: {params['brand']}\nOffer: {params['offer']}\nICP: {params['icp']}\n"
        f"Lead: {json.dumps(lead)}\nResearch: {json.dumps(research)}\nScore output: {json.dumps(score_output)}"
    )
    output = gemini_json(prompt, GEMINI_DRAFT_SCHEMA)
    return {
        "subject": str(output.get("subject", "")).strip(),
        "body": str(output.get("body", "")).strip(),
        "angle": str(output.get("angle", "")).strip(),
    }


def validate_email_body(body):
    lowered = body.lower()
    forbidden = ["i hope you're well", "i hope you are well", "i wanted to reach out", "strong fit"]
    if any(phrase in lowered for phrase in forbidden):
        raise ValueError("Email contains forbidden generic phrasing")
    word_count = len(body.split())
    if word_count < 80 or word_count > 120:
        raise ValueError("Email word count outside 80-120")
    if "Events Team — on behalf of Humble Grape / Vivat Bacchus" not in body:
        raise ValueError("Email missing required sign-off")


def call_gemini_llm(lead, research, params):
    score_output = call_gemini_score_lead(lead, research, params)
    draft_output = call_gemini_draft_email(lead, research, params, score_output)
    validate_email_body(draft_output["body"])
    if not score_output["reason"] or not draft_output["subject"]:
        raise ValueError("Gemini returned incomplete structured output")
    return {
        "fit_score": score_output["score"] * 10,
        "reason": score_output["reason"],
        "suggested_contact_role": score_output["contact_role"],
        "email_subject": draft_output["subject"],
        "email_body": draft_output["body"],
        "confidence": research.get("confidence", "medium"),
        "angle": draft_output["angle"],
        "llm_provider": "Gemini",
        "provider_used": "Gemini",
    }


def call_openai_llm(lead, research, params):
    config = get_config()
    if not config["openai_key"]:
        raise RuntimeError("OPENAI_API_KEY is not configured")
    print(f"API call: OpenAI Responses lead={lead.get('name')!r}")
    data = request_json(
        "https://api.openai.com/v1/responses",
        method="POST",
        payload={
            "model": config["openai_model"],
            "input": llm_prompt(lead, research, params),
            "text": {"format": {"type": "json_object"}},
        },
        headers={"Authorization": f"Bearer {config['openai_key']}"},
        timeout=20,
    )
    text = data.get("output_text") or ""
    if not text:
        for item in data.get("output", []):
            for content in item.get("content", []):
                if content.get("type") in {"output_text", "text"}:
                    text += content.get("text", "")
    return parse_llm_json(text)


def call_anthropic_llm(lead, research, params):
    config = get_config()
    if not config["anthropic_key"]:
        raise RuntimeError("ANTHROPIC_API_KEY is not configured")
    print(f"API call: Anthropic Messages lead={lead.get('name')!r}")
    data = request_json(
        "https://api.anthropic.com/v1/messages",
        method="POST",
        payload={
            "model": config["anthropic_model"],
            "max_tokens": 900,
            "messages": [{"role": "user", "content": llm_prompt(lead, research, params)}],
        },
        headers={
            "x-api-key": config["anthropic_key"],
            "anthropic-version": "2023-06-01",
        },
        timeout=20,
    )
    text = "".join(part.get("text", "") for part in data.get("content", []) if part.get("type") == "text")
    return parse_llm_json(text)


def validate_llm_output(output):
    required = {"fit_score", "reason", "suggested_contact_role", "email_subject", "email_body", "confidence"}
    if not required.issubset(output):
        raise ValueError("LLM response missing required keys")
    validate_email_body(str(output["email_body"]))
    output["fit_score"] = max(1, min(100, safe_int(output["fit_score"], 60)))
    return output


def llm_or_deterministic_output(lead, research, params):
    global gemini_cooldown_until
    config = get_config()
    attempted_llm = False
    if config["gemini_key"] and time.time() < gemini_cooldown_until:
        print(f"WARNING: Gemini skipped during quota cooldown lead={lead.get('name')!r}")
    elif config["gemini_key"]:
        attempted_llm = True
        try:
            output = validate_llm_output(call_gemini_llm(lead, research, params))
            print(f"LLM provider used: Gemini lead={lead.get('name')!r}")
            return output
        except Exception as exc:
            message = str(exc)
            if "429" in message or "RESOURCE_EXHAUSTED" in message or "quota" in message.lower():
                gemini_cooldown_until = time.time() + 60
                print("WARNING: Gemini quota exhausted; skipping Gemini briefly before fallback")
            print(f"WARNING: Gemini drafting failed for {lead.get('name')}: {exc}")

    for provider, caller in [("OpenAI", call_openai_llm), ("Anthropic", call_anthropic_llm)]:
        if provider == "OpenAI" and not config["openai_key"]:
            continue
        if provider == "Anthropic" and not config["anthropic_key"]:
            continue
        attempted_llm = True
        try:
            output = validate_llm_output(caller(lead, research, params))
            output["llm_provider"] = provider
            output["provider_used"] = provider
            print(f"LLM provider used: {provider} lead={lead.get('name')!r}")
            return output
        except Exception as exc:
            print(f"WARNING: {provider} drafting failed for {lead.get('name')}: {exc}")
    output = deterministic_live_output(
        lead,
        research,
        params,
        provider="deterministic_after_llm_failure" if attempted_llm else "deterministic_no_llm_key",
    )
    print(f"LLM provider used: Fallback lead={lead.get('name')!r}")
    return output


def normalize_for_similarity(value):
    return " ".join(
        "".join(character.lower() if character.isalnum() or character.isspace() else " " for character in value).split()
    )


def warn_if_too_similar(items, field, threshold):
    for index, item in enumerate(items):
        left = normalize_for_similarity(item[field])
        for other in items[index + 1 :]:
            right = normalize_for_similarity(other[field])
            ratio = SequenceMatcher(None, left, right).ratio()
            if ratio >= threshold:
                print(
                    "WARNING: similar demo output "
                    f"field={field} ratio={ratio:.2f} "
                    f"left={item['business_name']} right={other['business_name']}"
                )


def quality_guard(results):
    warn_if_too_similar(results, "reason", 0.72)
    warn_if_too_similar(results, "email_body", 0.68)


def enrich_lead(lead, params):
    result = dict(lead)
    result["business_name"] = result.get("business_name") or result["name"]
    result["fit_score"] = score_lead(result, params)
    result["reason"] = build_reason(result)
    result["research_summary"] = build_research_summary(result)
    result["signals"] = [
        result["business_signal"],
        result["buying_moment"],
        result["relevance_angle"],
        result["why_now"],
    ]
    result["source_urls"] = SOURCE_LABELS
    result["lead_source"] = "Seed data"
    result["source"] = "Seed data"
    result["research_mode"] = "seed"
    result["result_mode"] = "seed"
    result["fallback_used"] = False
    result["badges"] = ["Seed"]
    subject, body = build_email(result, params)
    result["email_subject"] = subject
    result["email_body"] = body
    return result


def refresh_cached_demo_results(results):
    cached = {
        "description": "Cached fixture generated from local seed data only. Sources are labelled as demo signals.",
        "results": results,
    }
    with CACHED_RESULTS_PATH.open("w", encoding="utf-8") as handle:
        json.dump(cached, handle, indent=2)


def add_lead_event(conn, lead_id, event_type, metadata=None):
    conn.execute(
        """
        INSERT INTO lead_events (id, lead_id, event_type, metadata_json, created_at)
        VALUES (?, ?, ?, ?, ?)
        """,
        (uuid4().hex, lead_id, event_type, json.dumps(metadata or {}), now_iso()),
    )


def add_audit_log(conn, lead_id, action, old_status=None, new_status=None):
    conn.execute(
        """
        INSERT INTO audit_log (id, lead_id, action, old_status, new_status, timestamp)
        VALUES (?, ?, ?, ?, ?, ?)
        """,
        (uuid4().hex, lead_id, action, old_status, new_status, now_iso()),
    )


def lead_from_row(row):
    lead = dict(row)
    lead["signals"] = json.loads(lead.pop("signals_json") or "[]")
    lead["source_urls"] = json.loads(lead.pop("source_urls_json") or "[]")
    lead["email_subject"] = lead.get("edited_email_subject") or lead.get("email_subject")
    lead["email_body"] = lead.get("edited_email_body") or lead.get("email_body")
    lead["badges"] = [lead.get("status", "generated").replace("_", " ").title()]
    if lead.get("provider_used"):
        lead["badges"].append(lead["provider_used"])
    return lead


def persist_job_leads(job_id, results):
    now = now_iso()
    persisted = []
    with get_db() as conn:
        conn.execute("DELETE FROM leads WHERE job_id = ?", (job_id,))
        for position, lead in enumerate(results, start=1):
            lead_id = lead.get("id") or uuid4().hex
            values = {
                "id": lead_id,
                "job_id": job_id,
                "position": position,
                "status": lead.get("status", "generated"),
                "business_name": lead.get("business_name") or lead.get("name", ""),
                "category": lead.get("category", ""),
                "address": lead.get("address", ""),
                "website": lead.get("website", ""),
                "phone": lead.get("phone", ""),
                "rating": str(lead.get("rating", "")),
                "lead_source": lead.get("lead_source") or lead.get("source", ""),
                "research_mode": lead.get("research_mode", ""),
                "provider_used": lead.get("provider_used", ""),
                "result_mode": lead.get("result_mode", ""),
                "fit_score": safe_int(lead.get("fit_score"), 0),
                "confidence": str(lead.get("confidence", "")),
                "reason": lead.get("reason", ""),
                "research_summary": lead.get("research_summary", ""),
                "signals_json": json.dumps(lead.get("signals", [])),
                "source_urls_json": json.dumps(lead.get("source_urls", [])),
                "suggested_contact_role": lead.get("suggested_contact_role", ""),
                "email_subject": lead.get("email_subject", ""),
                "email_body": lead.get("email_body", ""),
                "edited_email_subject": lead.get("edited_email_subject"),
                "edited_email_body": lead.get("edited_email_body"),
                "created_at": now,
                "updated_at": now,
            }
            conn.execute(
                """
                INSERT INTO leads (
                    id, job_id, position, status, business_name, category, address, website,
                    phone, rating, lead_source, research_mode, provider_used, result_mode,
                    fit_score, confidence, reason, research_summary, signals_json,
                    source_urls_json, suggested_contact_role, email_subject, email_body,
                    edited_email_subject, edited_email_body, created_at, updated_at
                )
                VALUES (
                    :id, :job_id, :position, :status, :business_name, :category, :address, :website,
                    :phone, :rating, :lead_source, :research_mode, :provider_used, :result_mode,
                    :fit_score, :confidence, :reason, :research_summary, :signals_json,
                    :source_urls_json, :suggested_contact_role, :email_subject, :email_body,
                    :edited_email_subject, :edited_email_body, :created_at, :updated_at
                )
                """,
                values,
            )
            add_lead_event(conn, lead_id, "generated", {"job_id": job_id})
            add_audit_log(conn, lead_id, "generated", None, values["status"])
            lead["id"] = lead_id
            lead["status"] = values["status"]
            persisted.append(lead)
    return persisted


def get_lead(lead_id):
    with get_db() as conn:
        row = conn.execute("SELECT * FROM leads WHERE id = ?", (lead_id,)).fetchone()
    return lead_from_row(row) if row else None


def rank_leads(seed_leads, params):
    ranked = [enrich_lead(lead, params) for lead in seed_leads]

    ranked.sort(key=lambda item: (item["fit_score"], item.get("confidence", 0)), reverse=True)
    results = ranked[: int(params["lead_count"])]
    quality_guard(results)
    refresh_cached_demo_results(results)
    return results


def seed_results(params, fallback_used=False):
    results = rank_leads(load_seed_leads(), params)
    for result in results:
        result["fallback_used"] = fallback_used
        result["badges"] = ["Seed"] + (["Fallback used"] if fallback_used else [])
    return results


def cached_results(path, mode_label):
    if not path.exists():
        raise RuntimeError(f"No cached {mode_label} results are available yet")
    with path.open("r", encoding="utf-8") as handle:
        payload = json.load(handle)
    results = payload.get("results", payload if isinstance(payload, list) else [])
    if not results:
        raise RuntimeError(f"No cached {mode_label} results are available yet")
    for result in results:
        result.setdefault("result_mode", mode_label)
        result.setdefault("badges", ["Cached"])
        if "Cached" not in result["badges"]:
            result["badges"].append("Cached")
    return results


def save_cached_live_results(results, params):
    payload = {
        "description": "Last successful live result. Generated from optional external APIs and safe fallbacks.",
        "cached_at": now_iso(),
        "params": params,
        "results": results,
    }
    with CACHED_LIVE_RESULTS_PATH.open("w", encoding="utf-8") as handle:
        json.dump(payload, handle, indent=2)


def unique_urls(*url_lists):
    urls = []
    seen = set()
    for url_list in url_lists:
        for url in url_list or []:
            if url and url not in seen:
                seen.add(url)
                urls.append(url)
    return urls


def live_lead_result(lead, research, params):
    output = llm_or_deterministic_output(lead, research, params)
    source_urls = unique_urls(lead.get("source_urls", []), research.get("source_urls", []))
    provider_used = output.get("provider_used", output.get("llm_provider", "Fallback"))
    if provider_used.startswith("deterministic"):
        provider_used = "Fallback"
    fallback_used = (
        research.get("research_mode") != "live"
        or provider_used == "Fallback"
    )
    return {
        "name": lead.get("name", ""),
        "business_name": lead.get("name", ""),
        "category": lead.get("category", "Local business"),
        "address": lead.get("address", ""),
        "website": lead.get("website", ""),
        "phone": lead.get("phone", ""),
        "rating": lead.get("rating", ""),
        "fit_score": output["fit_score"],
        "reason": output["reason"],
        "suggested_contact_role": output["suggested_contact_role"],
        "research_summary": research["summary"],
        "signals": research["signals"],
        "source_urls": source_urls,
        "source": lead.get("source", "SerpAPI Google Maps"),
        "lead_source": lead.get("lead_source", "SerpAPI Google Maps"),
        "research_mode": research.get("research_mode", "live"),
        "confidence": output.get("confidence", research.get("confidence", "medium")),
        "email_subject": output["email_subject"],
        "email_body": output["email_body"],
        "provider_used": provider_used,
        "angle": output.get("angle", ""),
        "result_mode": "live",
        "fallback_used": fallback_used,
        "badges": ["Live", provider_used] + (["Fallback used"] if fallback_used else []),
    }


def run_live_if_available(params):
    try:
        live_leads = source_live_leads(params)
    except Exception as exc:
        print(f"WARNING: live sourcing failed, falling back to seed mode: {exc}")
        return seed_results(params, fallback_used=True), "seed", True

    results = []
    fallback_used = False
    for lead in live_leads[:LIVE_LEAD_LIMIT]:
        try:
            research = research_live_lead(lead, params)
            result = live_lead_result(lead, research, params)
            fallback_used = fallback_used or result["fallback_used"]
            results.append(result)
        except Exception as exc:
            fallback_used = True
            print(f"WARNING: live lead failed for {lead.get('name')}: {exc}")

    if not results:
        return seed_results(params, fallback_used=True), "seed", True

    results.sort(key=lambda item: item["fit_score"], reverse=True)
    quality_guard(results)
    save_cached_live_results(results, params)
    return results, "live", fallback_used


def run_live_required(params):
    try:
        live_leads = source_live_leads(params)
    except Exception as exc:
        raise RuntimeError(f"Live search failed: {exc}") from exc

    results = []
    fallback_used = False
    for lead in live_leads[:LIVE_LEAD_LIMIT]:
        try:
            research = research_live_lead(lead, params)
            result = live_lead_result(lead, research, params)
            fallback_used = fallback_used or result["fallback_used"]
            results.append(result)
        except Exception as exc:
            print(f"WARNING: live lead failed for {lead.get('name')}: {exc}")

    if not results:
        raise RuntimeError("Live search failed: no usable live results remained after processing.")

    results.sort(key=lambda item: item["fit_score"], reverse=True)
    quality_guard(results)
    save_cached_live_results(results, params)
    return results, "live", fallback_used


def run_mode(params):
    mode = params.get("mode", "seed_demo")
    if mode == "cached_demo":
        return cached_results(CACHED_RESULTS_PATH, "cached_demo"), "cached", False
    if mode == "cached_live":
        return cached_results(CACHED_LIVE_RESULTS_PATH, "cached_live"), "cached", False
    if mode == "live_required":
        return run_live_required(params)
    if mode == "live_if_available":
        return run_live_if_available(params)
    return seed_results(params), "seed", False


def run_job(job_id, params):
    current_step = "queued"
    try:
        if params.get("mode") == "live_required":
            steps = [
                ("preparing", 15, "Preparing live search"),
                ("sourcing", 35, "Searching live local businesses"),
                ("research", 60, "Researching live signals"),
                ("drafting", 82, "Scoring and drafting outreach"),
            ]
            saving_message = "Saving live leads to approval workflow"
        else:
            steps = [
                ("preparing", 15, "Preparing lead source"),
                ("sourcing", 35, "Sourcing or loading leads"),
                ("research", 60, "Researching lead signals"),
                ("drafting", 82, "Scoring and drafting outreach"),
            ]
            saving_message = "Saving leads to approval workflow"
        for current_step, progress, message in steps:
            update_job_step(job_id, current_step, status="running", progress=progress, message=message)
            time.sleep(0.45)

        results, result_mode, fallback_used = run_mode(params)
        current_step = "saving"
        update_job_step(job_id, current_step, status="running", progress=92, message=saving_message)
        results = persist_job_leads(job_id, results)
        completed_params = dict(params)
        completed_params["current_step"] = "complete"
        completed_params["result_mode"] = result_mode
        completed_params["fallback_used"] = fallback_used
        completed_params["badges"] = (
            ["Cached"]
            if result_mode == "cached"
            else ["Live"] if result_mode == "live" else ["Seed"]
        ) + (["Fallback used"] if fallback_used else [])
        update_job(
            job_id,
            status="complete",
            progress=100,
            message="Lead results ready",
            params=json.dumps(completed_params),
            results=json.dumps(results),
            error=None,
        )
    except Exception as exc:
        message = "Live search could not complete" if params.get("mode") == "live_required" else "Job failed"
        update_job_step(job_id, current_step, status="failed", progress=100, message=message, error=str(exc))
    finally:
        with job_lock:
            running_jobs.pop(job_id, None)


def create_job(params):
    job_id = uuid4().hex[:12]
    now = now_iso()
    queued_params = dict(params)
    queued_params["current_step"] = "queued"
    with get_db() as conn:
        conn.execute(
            """
            INSERT INTO jobs (id, status, progress, message, params, created_at, updated_at)
            VALUES (?, ?, ?, ?, ?, ?, ?)
            """,
            (
                job_id,
                "queued",
                5,
                "Queued seed demo job",
                json.dumps(queued_params),
                now,
                now,
            ),
        )
    thread = threading.Thread(target=run_job, args=(job_id, params), daemon=True)
    with job_lock:
        running_jobs[job_id] = thread
    thread.start()
    return job_id


def parse_lead_count(raw_value):
    try:
        value = int(raw_value)
    except (TypeError, ValueError):
        value = DEFAULT_CONTEXT["lead_count"]
    return max(1, min(20, value))


def parse_mode(raw_value):
    mode = (raw_value or "seed_demo").strip()
    if mode in {"seed_demo", "live_required", "live_if_available", "cached_demo", "cached_live"}:
        return mode
    return "seed_demo"


def get_job(job_id):
    with get_db() as conn:
        row = conn.execute("SELECT * FROM jobs WHERE id = ?", (job_id,)).fetchone()
    return dict(row) if row else None


VALID_LEAD_STATUSES = {"generated", "in_review", "approved", "rejected", "do_not_contact", "exported"}
CONTACT_STATUSES = {"not_started", "searching", "found", "not_found", "manual", "failed"}
VERIFICATION_STATUSES = {"unverified", "valid", "risky", "invalid", "unknown", "failed"}
INSTANTLY_STATUSES = {"not_ready", "ready", "pushed", "failed"}
PREFERRED_EMAIL_PREFIXES = ("events", "corporate", "bookings", "hello", "info", "office")
EMAIL_RE = re.compile(r"\b[A-Z0-9._%+-]+@[A-Z0-9.-]+\.[A-Z]{2,}\b", re.IGNORECASE)
REVIEW_STATUSES = {"generated", "in_review"}
QUEUE_STATUSES = {
    "review": REVIEW_STATUSES,
    "approved": {"approved"},
    "rejected": {"rejected"},
    "do_not_contact": {"do_not_contact"},
    "suppression": {"do_not_contact"},
    "exported": {"exported"},
}
ALLOWED_STATUS_TRANSITIONS = {
    "generated": {"in_review", "approved", "rejected", "do_not_contact"},
    "in_review": {"approved", "rejected", "do_not_contact"},
    "approved": {"in_review", "exported", "rejected", "do_not_contact"},
    "rejected": {"in_review", "do_not_contact"},
    "do_not_contact": {"in_review"},
    "exported": set(),
}


def normalize_status(status):
    return "in_review" if status == "reviewed" else status


def suppress_lead(conn, lead, reason):
    website = lead.get("website", "")
    domain = domain_from_url(website)
    existing = conn.execute(
        """
        SELECT id FROM suppression_list
        WHERE (domain != '' AND domain = ?)
           OR (website != '' AND website = ?)
           OR (business_name != '' AND business_name = ?)
        LIMIT 1
        """,
        (domain, website, lead.get("business_name", "")),
    ).fetchone()
    if existing:
        return existing["id"]
    conn.execute(
        """
        INSERT INTO suppression_list (id, business_name, website, domain, email, reason, created_at)
        VALUES (?, ?, ?, ?, ?, ?, ?)
        """,
        (
            uuid4().hex,
            lead.get("business_name", ""),
            website,
            domain,
            "",
            reason,
            now_iso(),
        ),
    )


def transition_lead_status(lead_id, status, action="status_changed"):
    status = normalize_status(status)
    if status not in VALID_LEAD_STATUSES:
        raise ValueError("Invalid lead status")
    with get_db() as conn:
        row = conn.execute("SELECT * FROM leads WHERE id = ?", (lead_id,)).fetchone()
        if not row:
            return None
        old_status = normalize_status(row["status"])
        if status != old_status and status not in ALLOWED_STATUS_TRANSITIONS.get(old_status, set()):
            raise ValueError(f"Cannot move lead from {old_status} to {status}")
        timestamp = now_iso()
        exported_at = timestamp if status == "exported" else None
        conn.execute(
            """
            UPDATE leads
            SET status = ?, updated_at = ?, exported_at = COALESCE(?, exported_at)
            WHERE id = ?
            """,
            (status, timestamp, exported_at, lead_id),
        )
        lead = lead_from_row(row)
        if status == "do_not_contact":
            suppress_lead(conn, lead, "Marked do not contact from approval workflow")
        add_lead_event(conn, lead_id, action, {"previous_status": old_status, "new_status": status})
        add_audit_log(conn, lead_id, action, old_status, status)
        updated = conn.execute("SELECT * FROM leads WHERE id = ?", (lead_id,)).fetchone()
    return lead_from_row(updated)


def update_lead_status(lead_id, status):
    return transition_lead_status(lead_id, status)


def restore_lead(lead_id):
    return transition_lead_status(lead_id, "in_review", "restored")


def update_lead_email(lead_id, subject, body):
    with get_db() as conn:
        row = conn.execute("SELECT * FROM leads WHERE id = ?", (lead_id,)).fetchone()
        if not row:
            return None
        old_status = normalize_status(row["status"])
        new_status = "in_review" if old_status == "generated" else old_status
        if new_status != old_status and new_status not in ALLOWED_STATUS_TRANSITIONS.get(old_status, set()):
            raise ValueError(f"Cannot move lead from {old_status} to {new_status}")
        conn.execute(
            """
            UPDATE leads
            SET edited_email_subject = ?, edited_email_body = ?, status = ?,
                updated_at = ?
            WHERE id = ?
            """,
            (subject.strip(), body.strip(), new_status, now_iso(), lead_id),
        )
        add_lead_event(conn, lead_id, "email_edited", {"subject_changed": subject.strip() != row["email_subject"]})
        add_audit_log(conn, lead_id, "email_edited", old_status, new_status)
        updated = conn.execute("SELECT * FROM leads WHERE id = ?", (lead_id,)).fetchone()
    return lead_from_row(updated)


def list_leads(status=None, queue=None, job_id=None):
    query = "SELECT * FROM leads"
    values = []
    clauses = []
    if queue:
        queue_statuses = QUEUE_STATUSES.get(queue)
        if queue_statuses is None:
            raise ValueError("Invalid queue")
        placeholders = ", ".join("?" for _ in queue_statuses)
        clauses.append(f"status IN ({placeholders})")
        values.extend(sorted(queue_statuses))
    if status:
        status = normalize_status(status)
        clauses.append("status = ?")
        values.append(status)
    if job_id:
        clauses.append("job_id = ?")
        values.append(job_id)
    if clauses:
        query += " WHERE " + " AND ".join(clauses)
    normalized_status = normalize_status(status) if status else None
    if queue == "review" or normalized_status == "approved":
        query += " ORDER BY position ASC, created_at ASC"
    else:
        query += " ORDER BY updated_at DESC, position ASC"
    with get_db() as conn:
        rows = conn.execute(query, values).fetchall()
    return [lead_from_row(row) for row in rows]


def lead_counts():
    counts = {status: 0 for status in VALID_LEAD_STATUSES}
    with get_db() as conn:
        rows = conn.execute("SELECT status, COUNT(*) AS count FROM leads GROUP BY status").fetchall()
    for row in rows:
        status = normalize_status(row["status"])
        if status in counts:
            counts[status] += row["count"]
    counts["review"] = counts["generated"] + counts["in_review"]
    counts["export_ready"] = counts["approved"]
    return counts


def setup_warnings():
    config = get_config()
    warnings = []
    if not config["hunter_key"]:
        warnings.append("Hunter is not configured. Use website extraction or manual contact entry.")
    if not config["instantly_key"]:
        warnings.append("Instantly API key is not configured.")
    if config["instantly_key"] and not (config["instantly_campaign_id"] or config["instantly_list_id"]):
        warnings.append("Instantly target is missing. Set INSTANTLY_CAMPAIGN_ID or INSTANTLY_LIST_ID.")
    return warnings


def is_basic_email(value):
    return bool(EMAIL_RE.fullmatch((value or "").strip()))


def fetch_website_text(url):
    request = Request(url, headers={"User-Agent": "LocalHospitalityLeadEngine/1.0"})
    with urlopen(request, timeout=6) as response:
        content_type = response.headers.get("Content-Type", "")
        if "text" not in content_type and "html" not in content_type:
            return ""
        return response.read(300000).decode("utf-8", errors="ignore")


def candidate_contact_urls(website):
    if not website:
        return []
    base = website if "://" in website else f"https://{website}"
    parsed = urlparse(base)
    if not parsed.netloc:
        return []
    paths = ["", "/contact", "/contact-us", "/about", "/about-us", "/events", "/bookings"]
    return [urljoin(base, path) for path in paths]


def rank_email(email):
    local = email.split("@", 1)[0].lower()
    for index, prefix in enumerate(PREFERRED_EMAIL_PREFIXES):
        if local == prefix or local.startswith(f"{prefix}.") or local.startswith(f"{prefix}-"):
            return index
    if local in {"admin", "sales", "enquiries", "reservations"}:
        return len(PREFERRED_EMAIL_PREFIXES)
    return len(PREFERRED_EMAIL_PREFIXES) + 5


def extract_emails_from_website(website):
    domain = domain_from_url(website)
    found = set()
    for url in candidate_contact_urls(website):
        try:
            text = fetch_website_text(url)
        except Exception:
            continue
        for email in EMAIL_RE.findall(text):
            clean = email.strip(".,;:()[]<>").lower()
            if domain and clean.endswith(f"@{domain}"):
                found.add(clean)
            elif not domain:
                found.add(clean)
    return sorted(found, key=rank_email)


def hunter_domain_search(domain):
    key = get_config()["hunter_key"]
    if not key or not domain:
        return []
    url = "https://api.hunter.io/v2/domain-search?" + urlencode({"domain": domain, "api_key": key})
    data = request_json(url)
    emails = []
    for item in data.get("data", {}).get("emails", []):
        value = (item.get("value") or "").strip().lower()
        if value:
            emails.append(
                {
                    "email": value,
                    "name": item.get("first_name", "") + (" " + item.get("last_name", "") if item.get("last_name") else ""),
                    "role": item.get("position", ""),
                    "confidence": item.get("confidence", 0),
                }
            )
    return sorted(emails, key=lambda item: (rank_email(item["email"]), -safe_int(item.get("confidence"), 0)))


def save_contact_fields(conn, lead_id, *, email="", name="", role="", source="none", status="not_started", verification=None, reason=None, instantly_status=None, instantly_error=None):
    updates = {
        "recipient_email": email.strip().lower() if email else "",
        "recipient_name": name.strip() if name else "",
        "recipient_role": role.strip() if role else "",
        "contact_source": source,
        "contact_status": status,
        "updated_at": now_iso(),
    }
    if verification is not None:
        updates["email_verification_status"] = verification
    if reason is not None:
        updates["email_verification_reason"] = reason
    if instantly_status is not None:
        updates["instantly_status"] = instantly_status
    if instantly_error is not None:
        updates["instantly_error"] = instantly_error
    columns = ", ".join(f"{key} = ?" for key in updates)
    conn.execute(f"UPDATE leads SET {columns} WHERE id = ?", list(updates.values()) + [lead_id])


def discover_contact(lead_id):
    with get_db() as conn:
        row = conn.execute("SELECT * FROM leads WHERE id = ?", (lead_id,)).fetchone()
        if not row:
            return None, None
        lead = lead_from_row(row)
        if lead["status"] in {"rejected", "do_not_contact"}:
            raise ValueError("Rejected and do-not-contact leads cannot be prepared for outreach")
        conn.execute("UPDATE leads SET contact_status = 'searching', updated_at = ? WHERE id = ?", (now_iso(), lead_id))
        best_email = ""
        source = "none"
        name = ""
        role = lead.get("suggested_contact_role", "")
        reason = ""
        website_emails = extract_emails_from_website(lead.get("website", ""))
        if website_emails:
            best_email = website_emails[0]
            source = "website"
            reason = "Found on lead website."
        if not best_email and get_config()["hunter_key"]:
            hunter_results = hunter_domain_search(domain_from_url(lead.get("website", "")))
            if hunter_results:
                match = hunter_results[0]
                best_email = match["email"]
                source = "hunter"
                name = match.get("name", "").strip()
                role = match.get("role", "") or role
                reason = "Found with Hunter domain search."
        if best_email:
            save_contact_fields(
                conn,
                lead_id,
                email=best_email,
                name=name,
                role=role,
                source=source,
                status="found",
                verification="unverified",
                reason=reason,
                instantly_status="not_ready",
                instantly_error="",
            )
            add_lead_event(conn, lead_id, "contact_discovered", {"source": source, "email": best_email})
            add_audit_log(conn, lead_id, "contact_discovered", lead["status"], lead["status"])
        else:
            save_contact_fields(
                conn,
                lead_id,
                source="none",
                status="not_found",
                verification="unverified",
                reason="No visible or provider email found.",
                instantly_status="not_ready",
            )
        updated = conn.execute("SELECT * FROM leads WHERE id = ?", (lead_id,)).fetchone()
    return lead_from_row(updated), reason or "No email found."


def save_manual_contact(lead_id, email, name="", role=""):
    email = (email or "").strip().lower()
    if email and not is_basic_email(email):
        raise ValueError("Enter a valid email address")
    with get_db() as conn:
        row = conn.execute("SELECT * FROM leads WHERE id = ?", (lead_id,)).fetchone()
        if not row:
            return None
        lead = lead_from_row(row)
        if lead["status"] in {"rejected", "do_not_contact"}:
            raise ValueError("Rejected and do-not-contact leads cannot be prepared for outreach")
        save_contact_fields(
            conn,
            lead_id,
            email=email,
            name=name,
            role=role,
            source="manual" if email else "none",
            status="manual" if email else "not_found",
            verification="unverified",
            reason="Manual email saved; verification still needed." if email else "Manual contact cleared.",
            instantly_status="not_ready",
            instantly_error="",
        )
        add_lead_event(conn, lead_id, "contact_saved_manual", {"email": email})
        add_audit_log(conn, lead_id, "contact_saved_manual", lead["status"], lead["status"])
        updated = conn.execute("SELECT * FROM leads WHERE id = ?", (lead_id,)).fetchone()
    return lead_from_row(updated)


def map_hunter_verification(data):
    result = data.get("data", {})
    status = (result.get("status") or "").lower()
    score = safe_int(result.get("score"), 0)
    if status == "valid":
        mapped = "valid"
    elif status in {"invalid", "disposable"}:
        mapped = "invalid"
    elif result.get("accept_all") or status == "accept_all":
        mapped = "risky"
    elif status:
        mapped = "unknown" if score >= 50 else "invalid"
    else:
        mapped = "unknown"
    reason = f"Hunter status: {status or 'unknown'}; score: {score}."
    return mapped, reason


def verify_lead_email(lead_id):
    with get_db() as conn:
        row = conn.execute("SELECT * FROM leads WHERE id = ?", (lead_id,)).fetchone()
        if not row:
            return None, None, 404
        lead = lead_from_row(row)
        email = (lead.get("recipient_email") or "").strip()
        if not email:
            return lead, "Add a recipient email before verification.", 400
        if not get_config()["hunter_key"]:
            reason = "Hunter is not configured; email remains unverified."
            conn.execute(
                "UPDATE leads SET email_verification_status = 'unverified', email_verification_reason = ?, instantly_status = 'not_ready', updated_at = ? WHERE id = ?",
                (reason, now_iso(), lead_id),
            )
            updated = conn.execute("SELECT * FROM leads WHERE id = ?", (lead_id,)).fetchone()
            return lead_from_row(updated), reason, 200
        try:
            url = "https://api.hunter.io/v2/email-verifier?" + urlencode({"email": email, "api_key": get_config()["hunter_key"]})
            mapped, reason = map_hunter_verification(request_json(url))
        except Exception as exc:
            mapped, reason = "failed", f"Hunter verification failed: {exc}"
        instantly_status = "ready" if mapped == "valid" else "not_ready"
        conn.execute(
            """
            UPDATE leads
            SET email_verification_status = ?, email_verification_reason = ?, instantly_status = ?, updated_at = ?
            WHERE id = ?
            """,
            (mapped, reason, instantly_status, now_iso(), lead_id),
        )
        add_lead_event(conn, lead_id, "email_verified", {"status": mapped, "reason": reason})
        add_audit_log(conn, lead_id, "email_verified", lead["status"], lead["status"])
        updated = conn.execute("SELECT * FROM leads WHERE id = ?", (lead_id,)).fetchone()
    return lead_from_row(updated), reason, 200


def can_push_to_instantly(lead, confirm_unverified=False):
    if lead.get("status") != "approved":
        return False, "Lead is not approved."
    if lead.get("instantly_status") == "pushed":
        return False, "Lead is already pushed."
    if not lead.get("recipient_email"):
        return False, "Lead is missing recipient email."
    verification = lead.get("email_verification_status") or "unverified"
    if verification in {"invalid", "failed"}:
        return False, "Email is invalid or verification failed."
    if verification == "valid":
        return True, "Ready."
    if verification == "unverified" and lead.get("contact_source") == "manual" and confirm_unverified:
        return True, "Manual unverified email confirmed."
    return False, "Email must be valid or manually confirmed."


def outreach_readiness():
    approved = list_leads("approved")
    counts = {
        "approved": len(approved),
        "emails_found": 0,
        "verified_valid": 0,
        "missing_emails": 0,
        "invalid_emails": 0,
        "manual_unverified": 0,
        "ready_to_push": 0,
        "already_pushed": 0,
    }
    for lead in approved:
        if lead.get("recipient_email"):
            counts["emails_found"] += 1
        else:
            counts["missing_emails"] += 1
        verification = lead.get("email_verification_status") or "unverified"
        if verification == "valid":
            counts["verified_valid"] += 1
        if verification in {"invalid", "failed"}:
            counts["invalid_emails"] += 1
        if verification == "unverified" and lead.get("contact_source") == "manual":
            counts["manual_unverified"] += 1
        if lead.get("instantly_status") == "pushed":
            counts["already_pushed"] += 1
        can_push, _reason = can_push_to_instantly(lead, confirm_unverified=True)
        if can_push:
            counts["ready_to_push"] += 1
    return {"counts": counts, "warnings": setup_warnings()}


def instantly_payload_for_lead(lead):
    first_name, last_name = split_name(lead.get("recipient_name", ""))
    return {
        "email": lead.get("recipient_email", ""),
        "first_name": first_name,
        "last_name": last_name,
        "company_name": lead.get("business_name", ""),
        "website": lead.get("website", ""),
        "job_title": lead.get("recipient_role") or lead.get("suggested_contact_role", ""),
        "custom_variables": {
            "fit_score": lead.get("fit_score", ""),
            "fit_reason": lead.get("reason", ""),
            "outreach_angle": lead.get("relevance_angle") or lead.get("angle", ""),
            "email_subject": active_email_subject(lead),
            "email_body": active_email_body(lead),
            "suggested_contact_role": lead.get("suggested_contact_role", ""),
            "source_urls": " | ".join(lead.get("source_urls", [])),
        },
    }


def instantly_config_status():
    config = get_config()
    target = "campaign" if config["instantly_campaign_id"] else "list" if config["instantly_list_id"] else ""
    return {
        "has_api_key": bool(config["instantly_key"]),
        "has_campaign_id": bool(config["instantly_campaign_id"]),
        "has_list_id": bool(config["instantly_list_id"]),
        "target": target,
        "ready": bool(config["instantly_key"] and target),
    }


def friendly_instantly_error(error):
    if "HTTP 403" in error or "code: 1010" in error or '"code":1010' in error:
        return (
            "Instantly rejected the request before processing. "
            "Check API key scope, campaign access, or gateway block. "
            f"Original error: {error}"
        )
    return f"Instantly push failed: {error}"


def push_approved_to_instantly(lead_ids=None, confirm_unverified=False):
    config = get_config()
    if not config["instantly_key"]:
        raise ValueError("Instantly API key is not configured.")
    target_field = "campaign_id" if config["instantly_campaign_id"] else "list_id"
    target_value = config["instantly_campaign_id"] or config["instantly_list_id"]
    if not target_value:
        raise ValueError("Set INSTANTLY_CAMPAIGN_ID or INSTANTLY_LIST_ID before pushing.")
    approved = list_leads("approved")
    if lead_ids:
        lead_id_set = set(lead_ids)
        approved = [lead for lead in approved if lead["id"] in lead_id_set]
    ready = []
    blocked = []
    for lead in approved:
        can_push, reason = can_push_to_instantly(lead, confirm_unverified)
        if can_push:
            ready.append(lead)
        else:
            blocked.append({"id": lead["id"], "business_name": lead.get("business_name", ""), "reason": reason})
    if not ready:
        return {"pushed": [], "blocked": blocked, "message": "No approved leads are ready to push."}, 400
    payload = {
        target_field: target_value,
        "verify_leads_on_import": False,
        "skip_if_in_workspace": True,
        "leads": [instantly_payload_for_lead(lead) for lead in ready],
    }
    headers = {"Authorization": f"Bearer {config['instantly_key']}"}
    with get_db() as conn:
        for lead in ready:
            add_lead_event(conn, lead["id"], "instantly_push_attempted", {"target": target_field})
    try:
        response = request_json("https://api.instantly.ai/api/v2/leads/add", method="POST", payload=payload, headers=headers)
    except Exception as exc:
        error = friendly_instantly_error(str(exc))
        with get_db() as conn:
            for lead in ready:
                conn.execute(
                    "UPDATE leads SET instantly_status = 'failed', instantly_error = ?, updated_at = ? WHERE id = ?",
                    (error, now_iso(), lead["id"]),
                )
                add_lead_event(conn, lead["id"], "instantly_push_failed", {"error": error})
                add_audit_log(conn, lead["id"], "instantly_push_failed", lead["status"], lead["status"])
        raise ValueError(error)
    pushed_at = now_iso()
    pushed = []
    with get_db() as conn:
        for lead in ready:
            conn.execute(
                "UPDATE leads SET instantly_status = 'pushed', instantly_pushed_at = ?, instantly_error = '', updated_at = ? WHERE id = ?",
                (pushed_at, pushed_at, lead["id"]),
            )
            add_lead_event(conn, lead["id"], "instantly_push_success", {"response": response})
            add_audit_log(conn, lead["id"], "instantly_push_success", lead["status"], lead["status"])
            pushed.append(lead["id"])
    return {"pushed": pushed, "blocked": blocked, "response": response}, 200


def approved_csv_response():
    leads = list_leads("approved")
    output = io.StringIO()
    fieldnames = [
        "company_name",
        "category",
        "address",
        "website",
        "suggested_contact_role",
        "recipient_email",
        "fit_score",
        "fit_reason",
        "outreach_angle",
        "email_subject",
        "email_body",
        "source_urls",
        "confidence",
        "status",
    ]
    writer = csv.DictWriter(output, fieldnames=fieldnames)
    writer.writeheader()
    for lead in leads:
        writer.writerow(
            {
                "company_name": lead.get("business_name", ""),
                "category": lead.get("category", ""),
                "address": lead.get("address", ""),
                "website": lead.get("website", ""),
                "suggested_contact_role": lead.get("suggested_contact_role", ""),
                "recipient_email": lead.get("recipient_email", ""),
                "email_subject": active_email_subject(lead),
                "email_body": active_email_body(lead),
                "fit_score": lead.get("fit_score", ""),
                "fit_reason": lead.get("reason", ""),
                "outreach_angle": lead.get("angle", ""),
                "source_urls": " | ".join(lead.get("source_urls", [])),
                "confidence": lead.get("confidence", ""),
                "status": lead.get("status", ""),
            }
        )
    export_id = uuid4().hex
    with get_db() as conn:
        conn.execute(
            "INSERT INTO exports (id, created_at, lead_count, file_name, export_type) VALUES (?, ?, ?, ?, ?)",
            (export_id, now_iso(), len(leads), "approved_leads.csv", "csv"),
        )
        exported_at = now_iso()
        for lead in leads:
            conn.execute(
                "UPDATE leads SET status = 'exported', updated_at = ?, exported_at = ? WHERE id = ?",
                (exported_at, exported_at, lead["id"]),
            )
            add_lead_event(conn, lead["id"], "exported", {"export_id": export_id})
            add_audit_log(conn, lead["id"], "exported", "approved", "exported")
    return Response(
        output.getvalue(),
        mimetype="text/csv",
        headers={"Content-Disposition": "attachment; filename=approved_leads.csv"},
    )


def reset_demo_data():
    tables = ["lead_events", "audit_log", "exports", "suppression_list", "leads", "jobs"]
    with get_db() as conn:
        for table in tables:
            conn.execute(f"DELETE FROM {table}")
    return {
        "ok": True,
        "message": "Demo data reset.",
        "counts": lead_counts(),
    }


@app.route("/", methods=["GET", "POST"])
def index():
    if request.method == "POST":
        params = {
            "brand": request.form.get("brand", DEFAULT_CONTEXT["brand"]).strip() or DEFAULT_CONTEXT["brand"],
            "location": request.form.get("location", DEFAULT_CONTEXT["location"]).strip()
            or DEFAULT_CONTEXT["location"],
            "offer": request.form.get("offer", DEFAULT_CONTEXT["offer"]).strip() or DEFAULT_CONTEXT["offer"],
            "icp": request.form.get("icp", DEFAULT_CONTEXT["icp"]).strip() or DEFAULT_CONTEXT["icp"],
            "category": request.form.get("category", DEFAULT_CONTEXT["category"]).strip()
            or DEFAULT_CONTEXT["category"],
            "lead_count": parse_lead_count(request.form.get("lead_count", DEFAULT_CONTEXT["lead_count"])),
            "mode": "cached_live" if request.form.get("load_cached_live") else parse_mode(request.form.get("mode")),
        }
        job_id = create_job(params)
        return redirect(url_for("results", job_id=job_id))
    return render_template("index.html", defaults=DEFAULT_CONTEXT)


@app.route("/results/<job_id>")
def results(job_id):
    job = get_job(job_id)
    if not job:
        return render_template("results.html", job_id=job_id, missing=True), 404
    return render_template("results.html", job_id=job_id, missing=False)


@app.route("/approvals")
def approvals():
    return render_template("queue.html")


@app.route("/queue")
def queue():
    return redirect(url_for("approvals"))


@app.route("/api/live-readiness")
def api_live_readiness():
    return jsonify(live_readiness())


@app.route("/api/status/<job_id>")
def api_status(job_id):
    job = get_job(job_id)
    if not job:
        return jsonify({"status": "missing", "error": "Job not found"}), 404

    params = json.loads(job["params"])
    current_step, steps = progress_metadata(job["status"], job["progress"], params)
    payload = {
        "job_id": job["id"],
        "status": job["status"],
        "progress": job["progress"],
        "message": job["message"],
        "params": params,
        "current_step": current_step,
        "steps": steps,
        "results": json.loads(job["results"]) if job["results"] else [],
        "error": job["error"],
    }
    if payload["status"] == "complete":
        persisted = {lead["id"]: lead for lead in list_leads(job_id=job["id"])}
        merged_results = []
        for lead in payload["results"]:
            saved = persisted.get(lead.get("id"))
            if saved:
                lead = {
                    **lead,
                    "status": saved.get("status", lead.get("status", "generated")),
                    "email_subject": saved.get("email_subject", lead.get("email_subject", "")),
                    "email_body": saved.get("email_body", lead.get("email_body", "")),
                    "edited_email_subject": saved.get("edited_email_subject"),
                    "edited_email_body": saved.get("edited_email_body"),
                    "updated_at": saved.get("updated_at"),
                    "exported_at": saved.get("exported_at"),
                    "recipient_email": saved.get("recipient_email"),
                    "recipient_name": saved.get("recipient_name"),
                    "recipient_role": saved.get("recipient_role"),
                    "contact_source": saved.get("contact_source"),
                    "contact_status": saved.get("contact_status"),
                    "email_verification_status": saved.get("email_verification_status"),
                    "email_verification_reason": saved.get("email_verification_reason"),
                    "instantly_status": saved.get("instantly_status"),
                    "instantly_pushed_at": saved.get("instantly_pushed_at"),
                    "instantly_error": saved.get("instantly_error"),
                }
            merged_results.append(lead)
        payload["results"] = merged_results
    payload["requested_mode"] = payload["params"].get("mode", "seed_demo")
    payload["result_mode"] = payload["params"].get("result_mode", "")
    payload["fallback_used"] = payload["params"].get("fallback_used", False)
    payload["badges"] = payload["params"].get("badges", [])
    return jsonify(payload)


@app.route("/api/leads")
def api_leads():
    status = request.args.get("status", "").strip()
    queue = request.args.get("queue", "").strip()
    status = normalize_status(status) if status else ""
    if status and status not in VALID_LEAD_STATUSES:
        return jsonify({"error": "Invalid status"}), 400
    try:
        leads = list_leads(status or None, queue or None)
    except ValueError as exc:
        return jsonify({"error": str(exc)}), 400
    payload = {"leads": leads, "counts": lead_counts()}
    if status == "approved" or queue == "approved":
        payload["outreach"] = outreach_readiness()
    return jsonify(payload)


@app.route("/api/leads/counts")
def api_lead_counts():
    return jsonify({"counts": lead_counts()})


def lead_action_response(lead_id, status=None, action=None):
    try:
        lead = restore_lead(lead_id) if action == "restore" else transition_lead_status(lead_id, status)
    except ValueError as exc:
        return jsonify({"error": str(exc)}), 400
    if not lead:
        return jsonify({"error": "Lead not found"}), 404
    return jsonify({"lead": lead, "counts": lead_counts()})


@app.route("/api/leads/<lead_id>/approve", methods=["POST"])
def api_lead_approve(lead_id):
    return lead_action_response(lead_id, "approved")


@app.route("/api/leads/<lead_id>/reject", methods=["POST"])
def api_lead_reject(lead_id):
    return lead_action_response(lead_id, "rejected")


@app.route("/api/leads/<lead_id>/do-not-contact", methods=["POST"])
def api_lead_do_not_contact(lead_id):
    return lead_action_response(lead_id, "do_not_contact")


@app.route("/api/leads/<lead_id>/restore", methods=["POST"])
def api_lead_restore(lead_id):
    return lead_action_response(lead_id, action="restore")


@app.route("/api/leads/<lead_id>/status", methods=["POST"])
def api_lead_status(lead_id):
    data = request.get_json(silent=True) or {}
    try:
        lead = update_lead_status(lead_id, data.get("status", ""))
    except ValueError as exc:
        return jsonify({"error": str(exc)}), 400
    if not lead:
        return jsonify({"error": "Lead not found"}), 404
    return jsonify({"lead": lead, "counts": lead_counts()})


def lead_email_response(lead_id):
    data = request.get_json(silent=True) or {}
    subject = data.get("email_subject", "")
    body = data.get("email_body", "")
    if not subject.strip() or not body.strip():
        return jsonify({"error": "Email subject and body are required"}), 400
    try:
        lead = update_lead_email(lead_id, subject, body)
    except ValueError as exc:
        return jsonify({"error": str(exc)}), 400
    if not lead:
        return jsonify({"error": "Lead not found"}), 404
    return jsonify({"lead": lead, "counts": lead_counts()})


@app.route("/api/leads/<lead_id>/email", methods=["POST"])
def api_lead_email(lead_id):
    return lead_email_response(lead_id)


@app.route("/api/leads/<lead_id>/edit-email", methods=["POST"])
def api_lead_edit_email(lead_id):
    return lead_email_response(lead_id)


@app.route("/api/outreach/readiness")
def api_outreach_readiness():
    return jsonify(outreach_readiness())


@app.route("/api/leads/<lead_id>/discover-contact", methods=["POST"])
def api_discover_contact(lead_id):
    try:
        lead, message = discover_contact(lead_id)
    except ValueError as exc:
        return jsonify({"error": str(exc)}), 400
    if not lead:
        return jsonify({"error": "Lead not found"}), 404
    return jsonify({"lead": lead, "message": message, "outreach": outreach_readiness()})


@app.route("/api/leads/discover-approved", methods=["POST"])
def api_discover_approved_contacts():
    leads = list_leads("approved")
    results = []
    for lead in leads:
        if lead.get("recipient_email"):
            continue
        try:
            updated, message = discover_contact(lead["id"])
            results.append({"id": lead["id"], "status": updated.get("contact_status") if updated else "missing", "message": message})
        except Exception as exc:
            results.append({"id": lead["id"], "status": "failed", "message": str(exc)})
    return jsonify({"results": results, "outreach": outreach_readiness()})


@app.route("/api/leads/<lead_id>/save-contact", methods=["POST"])
def api_save_contact(lead_id):
    data = request.get_json(silent=True) or {}
    try:
        lead = save_manual_contact(
            lead_id,
            data.get("recipient_email", ""),
            data.get("recipient_name", ""),
            data.get("recipient_role", ""),
        )
    except ValueError as exc:
        return jsonify({"error": str(exc)}), 400
    if not lead:
        return jsonify({"error": "Lead not found"}), 404
    return jsonify({"lead": lead, "outreach": outreach_readiness()})


@app.route("/api/leads/<lead_id>/verify-email", methods=["POST"])
def api_verify_email(lead_id):
    lead, message, status_code = verify_lead_email(lead_id)
    if not lead and status_code == 404:
        return jsonify({"error": "Lead not found"}), 404
    payload = {"lead": lead, "message": message, "outreach": outreach_readiness()}
    if status_code >= 400:
        payload["error"] = message
    return jsonify(payload), status_code


@app.route("/api/leads/verify-approved", methods=["POST"])
def api_verify_approved_emails():
    results = []
    for lead in list_leads("approved"):
        if not lead.get("recipient_email") or lead.get("email_verification_status") == "valid":
            continue
        updated, message, status_code = verify_lead_email(lead["id"])
        results.append(
            {
                "id": lead["id"],
                "status": updated.get("email_verification_status") if updated else "missing",
                "message": message,
                "ok": status_code < 400,
            }
        )
    return jsonify({"results": results, "outreach": outreach_readiness()})


@app.route("/api/instantly/config")
def api_instantly_config():
    return jsonify(instantly_config_status())


@app.route("/api/instantly/push-approved", methods=["POST"])
def api_instantly_push_approved():
    data = request.get_json(silent=True) or {}
    try:
        result, status_code = push_approved_to_instantly(
            data.get("lead_ids"),
            bool(data.get("confirm_unverified", False)),
        )
    except ValueError as exc:
        return jsonify({"error": str(exc), "outreach": outreach_readiness()}), 400
    return jsonify({**result, "outreach": outreach_readiness()}), status_code


@app.route("/export/approved")
def export_approved():
    return approved_csv_response()


@app.route("/export/approved.csv")
def export_approved_csv():
    return approved_csv_response()


@app.route("/api/demo-results")
def api_demo_results():
    with CACHED_RESULTS_PATH.open("r", encoding="utf-8") as handle:
        return jsonify(json.load(handle))


@app.route("/api/demo/reset", methods=["POST"])
def api_demo_reset():
    return jsonify(reset_demo_data())


DATA_DIR.mkdir(exist_ok=True)
init_db()


if __name__ == "__main__":
    app.run(debug=True, use_reloader=False)
