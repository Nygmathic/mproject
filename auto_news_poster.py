#!/usr/bin/env python3
"""
Veridus.space Auto News Poster
- Fetches global news exclusively from RSS feeds of established news organizations
- Fetches full article body from source URL for rich, accurate rewrites
- Prioritises LATEST content — each niche has a recency window
- Rewrites entirely in Veridus voice — original, owned content
- Verifies factual accuracy against source material before publishing
- Primary AI: Google Gemini 2.0 Flash (free) — rotates across 3 keys
- Fallback AI: Groq / Llama 3.3 70B (free)
- Runs every 2 hours via GitHub Actions for near-live event coverage
"""

import os
import re
import json
import hashlib
import time
import feedparser
import requests
from datetime import datetime, timezone, timedelta
from pathlib import Path
from html.parser import HTMLParser

# ─── CONFIG ───────────────────────────────────────────────────────────────────

GEMINI_API_KEYS = [
    key for key in [
        os.environ.get("GEMINI_API_KEY_1", ""),
        os.environ.get("GEMINI_API_KEY_2", ""),
        os.environ.get("GEMINI_API_KEY_3", ""),
        os.environ.get("GEMINI_API_KEY_4", ""),
        os.environ.get("GEMINI_API_KEY_5", ""),
        os.environ.get("GEMINI_API_KEY_6", ""),
    ] if key
]

GROQ_API_KEY = os.environ.get("GROQ_API_KEY", "")

CONTENT_DIR = Path("content")
POSTED_LOG  = Path(".posted_articles.json")

# Hard daily cap — max posts per niche per calendar day (UTC)
# Opinion is written by humans — never auto-posted
DAILY_POST_LIMIT = 4

# How many articles to attempt per run (rate-limit AI calls per run)
# The daily cap above is the primary control; this just throttles per-run usage
NICHE_LIMITS = {
    "politics":       2,
    "global-affairs": 2,
    "sports":         2,  # Daily cap of 4 still applies — post-match runs catch up
    "business":       1,
    "climate":        1,
    "curious":        2,
}

# Maximum age of an article to be considered — oldest allowed per niche.
# Kept tight so the poster never re-surfaces yesterday's news.
# The 2-hour cron means a 6h window gives 3 chances to pick up a story.
RECENCY_HOURS = {
    "sports":         3,   # match reports must be same-day
    "politics":       6,   # tight — political news moves fast
    "global-affairs": 6,   # tight — same reason
    "business":       6,   # markets move daily
    "curious":        12,  # weird news is slow-burn; slight slack
    "climate":        24,  # climate stories don't break by the hour
}

# ─── RSS FEEDS ────────────────────────────────────────────────────────────────

RSS_FEEDS = {
    "politics": [
        # ── Domestic & Regional Politics ─────────────────────────────
        "https://rss.nytimes.com/services/xml/rss/nyt/Politics.xml",
        "https://feeds.npr.org/1014/rss.xml",
        "https://www.theguardian.com/politics/rss",
        "https://www.dw.com/en/politics/rss",
        "https://www.euronews.com/rss?format=mrss&level=theme&name=news",
        "https://www.aljazeera.com/xml/rss/all.xml",
        "https://feeds.bbci.co.uk/news/politics/rss.xml",  # replaces dead Reuters feed (Reuters killed public RSS in 2020)
        "https://rss.politico.com/politicopicks.xml",     # Politico
    ],
    "global-affairs": [
        # ── World News, Diplomacy & International Relations ──────────
        "https://foreignpolicy.com/feed/",
        "https://rss.nytimes.com/services/xml/rss/nyt/World.xml",
        "https://www.theguardian.com/world/rss",
        "https://www.dw.com/en/world/rss",
        "https://feeds.bbci.co.uk/news/world/rss.xml",  # replaces dead Reuters feed (Reuters killed public RSS in 2020)
        # ── United Nations ────────────────────────────────────────────
        "https://news.un.org/feed/subscribe/en/news/all/feed/rss.xml",
        # ── Regional perspectives, English-language ───────────────────
        "https://www.themoscowtimes.com/rss/news",       # Russia — independent, operates in exile (Russia effectively outlawed it)
        "https://www.scmp.com/rss/91/feed",               # China — South China Morning Post, Hong Kong-based (Alibaba-owned; editorially separate but worth knowing)
        "https://www.abc.net.au/news/feed/2942460/rss.xml",  # Australia — ABC News
        "https://rss.cbc.ca/lineup/topstories.xml",       # Canada — CBC News
        "https://www.france24.com/en/rss",                # Western Europe — France 24 English
        "https://en.mercopress.com/rss",                  # South America — MercoPress (English-language)
        "https://punchng.com/feed/",                      # West Africa — The Punch (Nigeria)
        "https://www.news24.com/rss",                     # South Africa — News24
        "https://www.politico.eu/feed",                   # Politico Europe
        "https://monocle.com/feed/",                       # Monocle — global affairs, business, culture briefing
    ],
    "business": [
        "https://rss.nytimes.com/services/xml/rss/nyt/Business.xml",
        "https://feeds.bbci.co.uk/news/business/rss.xml",
        "https://www.dw.com/en/economy/rss",
        "https://www.forbes.com/business/feed/",          # Forbes
        # ── Regional business perspectives ─────────────────────────
        "https://www.premiumtimesng.com/feed",            # West Africa (Nigeria)
        "https://www.fin24.com/rss",                      # South Africa — News24's business vertical
    ],
    "sports": [
        # ── Premier League ───────────────────────────────────────────
        "https://www.theguardian.com/football/premierleague/rss",
        "https://feeds.bbci.co.uk/sport/football/premier-league/rss.xml",
        "https://www.skysports.com/rss/12040",
        "https://www.fourfourtwo.com/rss",
        # ── ESPN Soccer ─────────────────────────────────────────────
        "https://www.espn.com/espn/rss/soccer/news",
        # ── Global Sport ────────────────────────────────────────────
        "https://feeds.bbci.co.uk/sport/rss.xml",
        "https://www.theguardian.com/sport/rss",
    ],
    "climate": [
        "https://www.theguardian.com/environment/climate-crisis/rss",
        "https://www.dw.com/en/environment/rss",
        "https://insideclimatenews.org/feed/",
        "https://rss.nytimes.com/services/xml/rss/nyt/Climate.xml",
    ],
    "curious": [
        # ── Verified Weird & Bizarre News ────────────────────────────
        "https://www.theguardian.com/news/series/weird/rss",            # Guardian Weird
        "https://feeds.bbci.co.uk/news/have_your_say/rss.xml",          # BBC HYS / Odd
        "https://www.upi.com/RSS/Odd_News/",                            # UPI Odd News
        "https://ripleys.com/feed/",                                    # Ripley's Believe It or Not
        "https://www.odditycentral.com/feed",                           # Oddity Central
        "https://www.atlasobscura.com/feeds/latest",                    # Atlas Obscura
        "https://www.mentalfloss.com/rss.xml",                          # Mental Floss
        "https://www.livescience.com/feeds/all",                        # Live Science (weird science)
        "https://www.iflscience.com/rss.xml",                           # IFLScience
    ],
}

# ─── NICHE METADATA ───────────────────────────────────────────────────────────

NICHE_META = {
    "politics":       ("Politics",      '["Politics", "News"]',       '["politics", "domestic politics", "elections", "governance"]'),
    "global-affairs": ("Global Affairs", '["Global Affairs", "News"]', '["world news", "diplomacy", "geopolitics", "international relations", "united nations"]'),
    "business":       ("Business",      '["Business", "News"]',       '["business", "economy", "markets", "trade"]'),
    "climate":        ("Climate",       '["Climate", "News"]',        '["climate change", "environment", "sustainability", "global warming"]'),
    "sports":         ("Sports",        '["Sports"]',                 '["sports", "football", "premier league", "athletics"]'),
    "curious":        ("Curious",       '["Curious", "News"]',        '["bizarre", "unusual", "strange", "odd news", "weird science"]'),
}

# ─── ENGLISH DETECTION ────────────────────────────────────────────────────────

NON_ENGLISH_PATTERN = re.compile(
    r"[\u0400-\u04FF"
    r"\u4E00-\u9FFF"
    r"\u3040-\u30FF"
    r"\u0600-\u06FF"
    r"\u0900-\u097F"
    r"\uAC00-\uD7AF"
    r"\u0370-\u03FF"
    r"\u0590-\u05FF]"
)

def is_english(text):
    if not text:
        return False
    if NON_ENGLISH_PATTERN.search(text):
        return False
    return sum(1 for c in text if ord(c) < 128) / max(len(text), 1) >= 0.60

# ─── HELPERS ──────────────────────────────────────────────────────────────────

def load_posted_log():
    """
    Load the posted-articles log.
    Format: dict of {article_id: iso_timestamp} — entries older than
    MAX_LOG_AGE_DAYS are pruned on load so the log never grows unbounded
    and IDs never silently age out before an article is old enough to repost.
    """
    MAX_LOG_AGE_DAYS = 14  # keep IDs for 2 weeks — far longer than any recency window
    cutoff = datetime.now(timezone.utc) - timedelta(days=MAX_LOG_AGE_DAYS)

    if POSTED_LOG.exists():
        try:
            raw = json.loads(POSTED_LOG.read_text())
            # Legacy format: plain list of IDs — migrate to dict with sentinel timestamp
            if isinstance(raw, list):
                raw = {aid: "2000-01-01T00:00:00Z" for aid in raw}
            # Prune entries older than MAX_LOG_AGE_DAYS
            pruned = {
                aid: ts for aid, ts in raw.items()
                if datetime.fromisoformat(ts.replace("Z", "+00:00")) >= cutoff
            }
            return pruned
        except Exception:
            return {}
    return {}

def save_posted_log(posted):
    """Save posted log dict {id: timestamp}. No arbitrary size cap."""
    POSTED_LOG.write_text(json.dumps(posted, indent=2))

def article_id(url):
    return hashlib.md5(url.encode()).hexdigest()

def slugify(text):
    text = text.lower()
    text = re.sub(r"[^\w\s-]", "", text)
    text = re.sub(r"[\s_]+", "-", text)
    text = re.sub(r"-+", "-", text).strip("-")
    return text[:70]

def count_today_posts(niche):
    """Count how many auto-posts already exist for this niche today (UTC)."""
    today = datetime.now(timezone.utc).strftime("%Y-%m-%d")
    niche_dir = CONTENT_DIR / niche
    if not niche_dir.exists():
        return 0
    return sum(1 for d in niche_dir.iterdir() if d.is_dir() and d.name.startswith(today))

def score_by_virality(articles):
    """
    Score each article by cross-feed frequency — stories covered by multiple
    sources are more widely talked about and score higher.

    Sorting key: (recency_bucket, viral_score) — both descending.
    Recency bucket divides age into 2-hour slots so a very fresh story always
    beats an equally-viral older one, and a story more than 4h old can only
    win if its viral score is significantly higher.
    """
    now = datetime.now(timezone.utc)

    # Build word sets for each article (significant words only, len > 3)
    stop = {"this","that","with","from","have","will","been","were","they",
            "their","more","than","over","after","into","about","says","said"}
    def sig_words(title):
        return {w.lower() for w in re.findall(r"[a-zA-Z]{4,}", title) if w.lower() not in stop}

    word_sets = [sig_words(a["title"]) for a in articles]

    scores = []
    for i, ws_i in enumerate(word_sets):
        if not ws_i:
            scores.append(0)
            continue
        score = sum(
            1 for j, ws_j in enumerate(word_sets)
            if i != j and len(ws_i & ws_j) >= 3
        )
        scores.append(score)

    def recency_bucket(article):
        """Lower bucket = older. Each bucket = 2 hours. Fresh articles get higher bucket."""
        pub = article.get("pub_date")
        if not pub:
            return 0
        age_hours = max(0, (now - pub).total_seconds() / 3600)
        # Invert: 0h old → bucket 12, 2h old → bucket 11, … 24h+ → bucket 0
        return max(0, 12 - int(age_hours / 2))

    # Sort: recency_bucket first (DESC), viral score second (DESC)
    scored = sorted(
        zip(scores, articles),
        key=lambda x: (recency_bucket(x[1]), x[0]),
        reverse=True,
    )
    if any(s > 0 for s, _ in scored):
        top = [(s, a["title"][:60]) for s, a in scored[:5] if s > 0]
        print(f"   🔥 Top viral stories: {top}")
    return [a for _, a in scored]

def parse_entry_date(entry):
    """Parse feed entry publish date. Returns timezone-aware datetime or None."""
    for field in ("published_parsed", "updated_parsed"):
        t = entry.get(field)
        if t:
            try:
                return datetime(*t[:6], tzinfo=timezone.utc)
            except Exception:
                pass
    return None



# ─── RSS FETCHING ─────────────────────────────────────────────────────────────

def fetch_rss_articles(niche, already_posted):
    """
    Fetch articles from all RSS feeds for a niche.
    - Sorts ALL entries newest-first across all feeds
    - Filters out anything older than RECENCY_HOURS[niche]
    """
    max_age_hours = RECENCY_HOURS.get(niche, 24)
    cutoff        = datetime.now(timezone.utc) - timedelta(hours=max_age_hours)
    raw_entries   = []

    for feed_url in RSS_FEEDS.get(niche, []):
        try:
            feed = feedparser.parse(feed_url)
            for entry in feed.entries:
                url = entry.get("link", "")
                if not url:
                    continue
                aid = article_id(url)
                if aid in already_posted:
                    continue

                pub_date = parse_entry_date(entry)

                # Skip articles older than the recency window
                if pub_date and pub_date < cutoff:
                    continue

                title   = entry.get("title", "").strip()
                summary = entry.get("summary", entry.get("description", "")).strip()
                summary = re.sub(r"<[^>]+>", "", summary).strip()

                if not title:
                    continue
                if not is_english(title):
                    print(f"  ⏭️  Non-English: {title[:50]}")
                    continue

                raw_entries.append({
                    "id":       aid,
                    "title":    title,
                    "summary":  summary,
                    "url":      url,
                    "niche":    niche,
                    "pub_date": pub_date,
                })
        except Exception as e:
            print(f"  ⚠️  Feed error {feed_url}: {e}")

    # Sort newest first — most recent content wins
    raw_entries.sort(
        key=lambda x: x["pub_date"] or datetime.min.replace(tzinfo=timezone.utc),
        reverse=True
    )

    # ── Relevance filter — reject obvious miscategorisations ────────────
    # Each niche has REQUIRED keywords (at least one must appear in title+summary)
    # and BANNED keywords (if any appear, article is rejected for this niche)
    NICHE_REQUIRED = {
        # Sports: strict — must be about sport
        "sports":  ["football", "soccer", "match", "league", "cup", "goal", "player",
                    "club", "sport", "game", "tournament", "champion", "coach", "team",
                    "afcon", "premier league", "caf", "fifa", "rugby", "athletics",
                    "cricket", "tennis", "basketball", "racing", "olympic", "score",
                    "fixture", "season", "transfer", "squad", "winger", "striker"],
        # Climate: must be environment-related
        "climate": ["climate", "environment", "carbon", "emission", "warming", "fossil",
                    "renewable", "drought", "flood", "weather", "temperature", "glacier",
                    "deforestation", "pollution", "biodiversity", "ecosystem", "net zero",
                    "wildfire", "hurricane", "sea level", "methane", "solar", "wind energy"],
        # Business: must be economic/financial
        "business":["economy", "market", "trade", "gdp", "inflation", "investment",
                    "stock", "bank", "currency", "company", "revenue", "profit",
                    "startup", "merger", "acquisition", "financial", "debt", "growth",
                    "price", "cost", "fund", "budget", "tax", "export", "import",
                    "supply chain", "oil price", "energy", "billion", "million"],
        # Curious: very loose — just needs to be factual/interesting non-standard news
        # No required filter — rely on the curious RSS sources to self-select
        # "curious": [],  # no filter needed — sources handle relevance
        # Politics: no required filter — broad enough category
        # "politics": [],
    }

    NICHE_BANNED = {
        # Sports feed should never get climate/political/business articles
        "sports":  ["climate", "court ruling", "stock market", "inflation", "election",
                    "parliament", "legislation", "gdp", "treaty", "diplomacy"],
        # Climate feed should never get sports/political articles
        "climate": ["football", "premier league", "match result", "goal", "transfer",
                    "election", "parliament", "stock market", "gdp"],
    }

    articles = []
    for entry in raw_entries:
        if len(entry.get("summary", "")) < 80:
            continue

        niche = entry["niche"]
        text  = (entry["title"] + " " + entry["summary"]).lower()

        # Check banned keywords — hard reject
        banned = NICHE_BANNED.get(niche, [])
        if any(kw in text for kw in banned):
            print(f"  ⛔ Rejected [{niche}] (banned keyword): {entry['title'][:60]}")
            continue

        # Check required keywords — must match at least one
        required = NICHE_REQUIRED.get(niche, [])
        if required and not any(kw in text for kw in required):
            print(f"  ⛔ Rejected [{niche}] (off-topic): {entry['title'][:60]}")
            continue

        articles.append(entry)

    age_info = f"(recency window: {max_age_hours}h)"
    print(f"   {len(articles)} fresh articles found {age_info}")
    return articles

# ─── FULL ARTICLE FETCHER ─────────────────────────────────────────────────────

class _TextExtractor(HTMLParser):
    """Minimal HTML-to-text extractor using only the stdlib."""
    SKIP_TAGS = {"script", "style", "nav", "header", "footer", "aside",
                 "noscript", "form", "button", "iframe", "figure", "figcaption"}

    def __init__(self):
        super().__init__()
        self._skip_depth = 0
        self._chunks = []

    def handle_starttag(self, tag, attrs):
        if tag in self.SKIP_TAGS:
            self._skip_depth += 1

    def handle_endtag(self, tag):
        if tag in self.SKIP_TAGS and self._skip_depth > 0:
            self._skip_depth -= 1
        if tag in ("p", "h1", "h2", "h3", "h4", "li", "br", "div"):
            self._chunks.append("\n")

    def handle_data(self, data):
        if self._skip_depth == 0:
            self._chunks.append(data)

    def get_text(self):
        raw = "".join(self._chunks)
        # Collapse whitespace runs but keep paragraph breaks
        lines = [" ".join(ln.split()) for ln in raw.splitlines()]
        return "\n".join(ln for ln in lines if ln)


def fetch_full_article(url, min_chars=400, max_chars=6000):
    """
    Fetch the full body text of a news article from its source URL.

    Strategy:
      1. Download raw HTML with a browser-like User-Agent (avoids most 403s).
      2. Strip boilerplate (nav, scripts, ads) with _TextExtractor.
      3. Keep only paragraphs that look like prose (≥40 chars, not navigation).
      4. Return up to max_chars of clean text, or None if fetching fails /
         the extracted text is shorter than min_chars (page was paywalled,
         JS-rendered, or returned noise).

    Returns str or None.
    """
    try:
        headers = {
            "User-Agent": (
                "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 "
                "(KHTML, like Gecko) Chrome/124.0 Safari/537.36"
            ),
            "Accept": "text/html,application/xhtml+xml",
            "Accept-Language": "en-US,en;q=0.9",
        }
        resp = requests.get(url, headers=headers, timeout=15, allow_redirects=True)
        if not resp.ok:
            print(f"  ⚠️  Full-fetch HTTP {resp.status_code} — falling back to RSS summary")
            return None

        # Decode safely
        content = resp.content.decode(resp.apparent_encoding or "utf-8", errors="replace")

        extractor = _TextExtractor()
        extractor.feed(content)
        raw_text = extractor.get_text()

        # Keep only paragraphs that look like real prose
        paragraphs = [
            ln for ln in raw_text.splitlines()
            if len(ln) >= 40 and not ln.strip().startswith(("©", "Cookie", "Subscribe", "Sign in", "Log in"))
        ]
        body = "\n\n".join(paragraphs)

        if len(body) < min_chars:
            print(f"  ⚠️  Full-fetch yielded too little text ({len(body)} chars) — likely paywalled or JS-rendered")
            return None

        truncated = body[:max_chars]
        print(f"  📄 Full article fetched: {len(truncated)} chars from source")
        return truncated

    except Exception as e:
        print(f"  ⚠️  Full-fetch failed: {e}")
        return None


# ─── PROMPT: ARTICLE BODY ─────────────────────────────────────────────────────

def build_article_prompt(article):
    niche = article["niche"]

    niche_guidance = {
        "politics":       "Cover domestic and national politics — elections, legislation, governance, political parties, and domestic policy debates. Keep the lens on a single country's internal politics rather than international relations.",
        "global-affairs": "Cover international relations, diplomacy, geopolitics, wars, treaties, and global governance — the United Nations, multilateral institutions, and cross-border developments. Represent multiple regional perspectives evenly rather than centring any single region.",
        "business":       "Cover business and economic developments with global impact. Represent perspectives from multiple emerging and established economies alongside one another, rather than centring any single region.",
        "sports":         "Cover sport across major leagues and competitions — football (including the Premier League and major international tournaments), athletics, rugby, and other disciplines. Write match reports with energy and precision. Do not centre only one country's or region's sport.",
        "climate":        "Emphasise human and economic impact of climate change, especially on the most vulnerable regions. Ground all claims in science. Avoid alarmism.",
        "curious":        "Cover genuinely strange, bizarre, or surprising true stories from around the world. The tone should be engaged and intelligent — curious and amused, not mocking. Every claim must be factual and verifiable. No sensationalism, no fabrication.",
    }

    guidance = niche_guidance.get(niche, "Cover this story with global context and balance.")

    # Use the full fetched article body when available; fall back to RSS summary
    full_text = article.get("full_text", "")
    if full_text:
        source_block = f"SOURCE TEXT (full article body — use all facts contained here):\n{full_text}"
    else:
        source_block = f"SUMMARY (RSS excerpt only — base the article strictly on these facts):\n{article['summary']}"

    return f"""You are a senior international correspondent writing for Veridus — an independent global publication with the precision of The Guardian and the voice of a publication that thinks for itself.

Write a complete, original news article. This content must be entirely Veridus's own — do not reproduce or closely paraphrase the source material. Transform it into something new.

ACCURACY — NON-NEGOTIABLE (violations mean the article must not be published):
- Every fact, figure, date, name, statistic, and quote you write must come directly and explicitly from the headline or summary provided below. If the source material does not state it, do not write it.
- Do NOT invent, infer, or extrapolate any fact. If you do not have enough source material to confirm a detail, omit it entirely.
- Do NOT fabricate or paraphrase quotes. If a quote is not present word-for-word in the source material, do not include it. Use attributed paraphrase only when the source material clearly supports it.
- Do NOT speculate about causes, outcomes, or motivations unless the source explicitly states them — and if you include speculation present it clearly as speculation ("analysts suggest…", "officials have indicated…") only if those exact words appear in the source.
- Do NOT fill gaps with background knowledge that contradicts, embellishes, or goes beyond what the source says. General context (e.g. established historical facts) is permitted only when it is unambiguously true and does not misrepresent the specific story.
- If the headline and summary provide limited facts, write a shorter, accurate article rather than a long, padded, inaccurate one. Accuracy comes before word count.
- Numbers matter: do not round, inflate, or alter any figures. If the source says "at least 12", write "at least 12" — not "dozens".

STRICT REQUIREMENTS:
- TARGET: 800 words. Write between 750 and 850 words where the source material supports it; if source material is thin, write as many accurate words as the facts allow and do not pad.
- 6 to 8 substantial paragraphs — no thin or short paragraphs
- Opening paragraph: Compelling and immediate — draws the reader in without starting with "In a" or "The"
- Second paragraph: Expand on the key facts and the stakes of the story
- Middle paragraphs: Context, background, analysis, multiple perspectives, historical parallels where relevant
- Penultimate paragraph: Reactions, implications, what different stakeholders are doing or saying
- Final paragraph: Forward-looking — what happens next and what readers should watch
- Use two or three descriptive H2 subheadings (## Heading) to break the article into sections. Each subheading must be distinct from the others and from the article's own topic sentence — do not reuse the same words or phrasing across subheadings, and do not simply restate the headline as a subheading.
- Vary your language throughout: do not repeat the same distinctive word, phrase, or sentence construction more than once in the article (ordinary connective words like "the," "said," "also" are fine — this is about avoidable repetition of distinctive phrasing, e.g. don't use "significant development" or "stakeholders are closely watching" more than once). If you need to refer to the same person, place, or concept repeatedly, vary how you refer to it (name, title, role, pronoun) rather than repeating the identical phrase each time.
- Tone: Authoritative, measured, internationally minded
- Vocabulary and register: Write in advanced, sophisticated English — the level of The Economist or The Atlantic. Use precise, elevated diction over simple synonyms where it sharpens meaning (e.g. "exacerbate" rather than "make worse," "untenable" rather than "not workable"), and vary sentence structure and length rather than defaulting to short, simple sentences throughout. This is about precision and command of language, not obscurity — every word should still be immediately clear to an educated general reader. Do not reach for a fancier word if it makes the meaning less exact. Do not lean on any single elevated word or phrase repeatedly as a crutch — draw from a genuinely varied vocabulary rather than favoring one or two "impressive" words throughout the piece. No clichés. No sensationalism.
- Do NOT mention or reference any news outlet, wire service, or publication
- Do NOT include the main headline — body text and subheadings only
- Do NOT use bullet points or numbered lists — flowing prose only
- Write in English only
- NO BYLINE OR CORRESPONDENT NAME, EVER: Veridus articles carry no personal byline within the body text. Do NOT write phrases like "our correspondent," "our reporter," "Veridus's [name]," or any variation naming a writer. Write in an unattributed third-person reporting voice throughout.
- If the source material names the journalist(s) who originally reported the story, that name belongs to them and their outlet — do NOT carry it into this article, do NOT present them as a Veridus staff member, and do NOT reference them at all unless they are themselves a subject of the news event (e.g. being quoted as an official or expert in their own right, not as the story's author).
- NO IMPLIED FIELD PRESENCE, EVER: Veridus has no reporters on the ground and does not conduct original interviews — this article is a desk rewrite of published reporting. Do NOT write or imply otherwise. Banned phrasing includes (but is not limited to): "our team on the ground," "when this reporter visited," "sources told Veridus," "speaking to Veridus," "in an interview with Veridus," "Veridus witnessed," "Veridus can confirm," or any construction that implies Veridus itself gathered information firsthand. Attribute reporting neutrally to what happened or what was said/reported, without naming who is doing the reporting at all (e.g. "Officials said..." or "According to reports..." rather than "Veridus spoke to officials" or "our sources say").

EDITORIAL FOCUS: {guidance}

NICHE: {niche.upper()}
HEADLINE: {article['title']}
{source_block}
Write the full article now:"""

# ─── PROMPT: SEO METADATA ─────────────────────────────────────────────────────

def build_seo_prompt(article, body):
    return f"""You are an SEO specialist for an international news website.

Given the article headline, summary, and body below, generate SEO metadata to maximise Google search traffic.

Return ONLY a valid JSON object with these exact keys — no extra text, no markdown, no explanation:

{{
  "seo_title": "A compelling, keyword-rich title for Google (50-60 characters max). Different from the original headline — optimised for search clicks.",
  "meta_description": "An engaging meta description for Google (150-160 characters max). Include the main keyword naturally. Make it compelling enough to click.",
  "focus_keyword": "The single most important search keyword or phrase for this article (2-5 words).",
  "secondary_keywords": ["keyword 2", "keyword 3", "keyword 4", "keyword 5"],
  "seo_slug": "url-friendly-slug-with-main-keyword-no-stopwords-max-8-words"
}}

ORIGINAL HEADLINE: {article['title']}
NICHE: {article['niche'].upper()}
ARTICLE BODY (first 400 words): {' '.join(body.split()[:400])}

Return only the JSON object:"""

# ─── WIKIMEDIA COMMONS IMAGE FETCHER ─────────────────────────────────────────
#
# Fetches a free, correctly-licensed image from Wikimedia Commons.
# Accepted licenses: CC0, Public Domain only — no attribution requirement,
# so nothing needs to be credited anywhere on the site.
# Saves image locally into the Hugo page bundle so it is permanently owned.
# If no suitable image is found the article posts without one — graceful fallback.

# Licenses we trust — reject anything else (e.g. CC BY, CC BY-SA, CC BY-NC, fair use, unknown).
# Deliberately excludes CC BY / CC BY-SA: those require visible attribution,
# which this site does not display.
ACCEPTED_LICENSES = {
    "cc0",
    "public domain", "pd", "cc-pd",
}

# Preferred image formats
ACCEPTED_MIMES = {"image/jpeg", "image/png", "image/webp"}

# Stop-words to strip when building image queries from titles
_IMAGE_STOPWORDS = {
    "the","a","an","and","or","but","in","on","at","to","for","of","with",
    "by","from","as","is","was","are","were","be","been","has","have","had",
    "that","this","these","those","it","its","after","before","over","under",
    "how","why","what","who","when","where","will","would","could","should",
    "says","said","after","amid","into","than","about","against","during",
    "live","update","updates","latest","breaking","new","report","reports",
}

# Niche-specific fallback queries when title parsing fails
_NICHE_IMAGE_FALLBACKS = {
    "politics":       "parliament building",
    "business":       "stock exchange trading floor",
    "sports":         "football stadium",
    "climate":        "climate change flooding",
    "curious":        "magnifying glass mystery",
    "global-affairs": "United Nations headquarters",
}

def build_image_query(article, seo):
    """
    Build a Wikimedia search query from article metadata — NO AI call needed.
    Priority: focus_keyword → meaningful title words → niche fallback.
    """
    niche   = article["niche"]
    title   = article["title"]
    keyword = (seo.get("focus_keyword", "") if seo else "").strip()

    # Use focus keyword if it's specific enough (more than 2 words or 10 chars)
    if keyword and (len(keyword.split()) >= 2 or len(keyword) >= 10):
        return keyword[:80]

    # Extract meaningful words from title — strip stopwords and short words
    words = [
        w for w in re.sub(r"[^a-zA-Z0-9 ]", " ", title).split()
        if len(w) > 3 and w.lower() not in _IMAGE_STOPWORDS
    ]

    if len(words) >= 2:
        return " ".join(words[:4])

    # Last resort: niche fallback
    return _NICHE_IMAGE_FALLBACKS.get(niche, "world news")

def fetch_wikimedia_image(search_query):
    """
    Search Wikimedia Commons for a free image matching the query.
    Returns dict with {url, filename, license, attribution} or None.
    """
    try:
        # Step 1: Search for matching files
        search_resp = requests.get(
            "https://commons.wikimedia.org/w/api.php",
            params={
                "action":      "query",
                "generator":   "search",
                "gsrnamespace": 6,        # File namespace
                "gsrsearch":   f"File:{search_query}",
                "gsrlimit":    10,
                "prop":        "imageinfo",
                "iiprop":      "url|mime|extmetadata|size",
                "iiurlwidth":  1200,
                "format":      "json",
            },
            timeout=15,
        )
        if not search_resp.ok:
            return None

        pages = search_resp.json().get("query", {}).get("pages", {})
        if not pages:
            print(f"  ⚠️  Wikimedia: no results for '{search_query}'")
            return None

        # Step 2: Filter by license and mime type
        candidates = []
        for page in pages.values():
            info_list = page.get("imageinfo", [])
            if not info_list:
                continue
            info = info_list[0]

            mime = info.get("mime", "")
            if mime not in ACCEPTED_MIMES:
                continue

            # Check dimensions — skip tiny images
            width  = info.get("width", 0)
            height = info.get("height", 0)
            if width < 400 or height < 250:
                continue

            meta     = info.get("extmetadata", {})
            license_short = meta.get("LicenseShortName", {}).get("value", "").lower()
            license_url   = meta.get("LicenseUrl", {}).get("value", "")
            artist        = meta.get("Artist", {}).get("value", "")
            artist        = re.sub(r"<[^>]+>", "", artist).strip()[:100]

            # Validate license — strip version numbers for flexible matching
            # e.g. "CC BY-SA 4.0" → normalise to "cc-by-sa" for lookup
            lic_normalised = re.sub(r'[\s\.]+', '-', license_short).strip('-')
            lic_normalised = re.sub(r'-\d+\.\d+$', '', lic_normalised)  # strip trailing version
            accepted = (
                lic_normalised in ACCEPTED_LICENSES
                or any(a in lic_normalised for a in ACCEPTED_LICENSES)
                or "public domain" in license_short
            ) and "nc" not in lic_normalised and "nd" not in lic_normalised and "by" not in lic_normalised

            if not accepted:
                print(f"  ⛔ Rejected license: {license_short}")
                continue

            # Prefer images that have been on Wikimedia longer (more stable)
            # thumburl is only present if image is larger than iiurlwidth
            # fall back to full url if thumb not available
            img_url = info.get("thumburl") or info.get("url", "")
            if not img_url:
                continue
            candidates.append({
                "url":         img_url,
                "full_url":    info.get("url"),
                "filename":    page.get("title", "").replace("File:", "").strip(),
                "mime":        mime,
                "license":     license_short,
                "license_url": license_url,
                "attribution": artist or "Wikimedia Commons",
                "width":       width,
                "height":      height,
            })

        if not candidates:
            return None

        # Pick the widest image (best quality)
        candidates.sort(key=lambda x: x["width"], reverse=True)
        return candidates[0]

    except Exception as e:
        print(f"  ⚠️  Wikimedia search error: {e}")
        return None

def download_wikimedia_image(image_info, dest_dir):
    """
    Download image to dest_dir/cover.jpg (or .png).
    Returns local filename string or None.
    """
    try:
        ext_map = {"image/jpeg": "jpg", "image/png": "png", "image/webp": "webp"}
        ext     = ext_map.get(image_info["mime"], "jpg")
        dest    = dest_dir / f"cover.{ext}"

        resp = requests.get(image_info["url"], timeout=30, stream=True)
        if not resp.ok:
            print(f"  ⚠️  Image download failed: HTTP {resp.status_code} — {image_info['url'][:60]}")
            return None

        with open(dest, "wb") as f:
            for chunk in resp.iter_content(8192):
                f.write(chunk)

        size_kb = dest.stat().st_size // 1024
        print(f"  🖼️  Image saved: cover.{ext} ({size_kb}KB) — {image_info['license']}")
        print(f"  ©  Attribution: {image_info['attribution']}")
        return f"cover.{ext}"

    except Exception as e:
        print(f"  ⚠️  Image download failed: {e}")
        return None

def get_feature_image(article, seo, dest_dir):
    """
    Full pipeline: generate search query → search Wikimedia → download.
    Returns (local_filename, image_info) or (None, None).
    """
    print(f"  🔎 Searching Wikimedia Commons for image...")
    query = build_image_query(article, seo)
    print(f"  🔍 Query: '{query}'")

    image_info = fetch_wikimedia_image(query)
    if not image_info:
        # Try a simpler fallback query using just niche keyword
        fallback_queries = {
            "politics":       "parliament building",
            "global-affairs": "United Nations headquarters",
            "sports":         "football stadium",
            "business":       "stock exchange trading floor",
            "climate":        "climate change flooding",
            "curious":        "question mark abstract",
        }
        fallback = fallback_queries.get(article["niche"])
        if fallback:
            print(f"  🔄 Trying fallback query: '{fallback}'")
            image_info = fetch_wikimedia_image(fallback)

    if not image_info:
        print(f"  ⚠️  No suitable image found — article will post without feature image")
        return None, None

    filename = download_wikimedia_image(image_info, dest_dir)
    return filename, image_info


# ─── AI CALLS ─────────────────────────────────────────────────────────────────

def call_gemini_with_key(prompt, api_key, max_tokens=2048):
    try:
        resp = requests.post(
            "https://generativelanguage.googleapis.com/v1beta/models/gemini-2.0-flash:generateContent",
            params={"key": api_key},
            json={
                "contents": [{"parts": [{"text": prompt}]}],
                "generationConfig": {"temperature": 0.7, "maxOutputTokens": max_tokens, "topP": 0.9},
            },
            headers={"Content-Type": "application/json"},
            timeout=60,
        )
        if resp.status_code == 429:
            print(f"  ⚠️  Gemini key rate limited (429) — trying next key...")
            return None, True
        if not resp.ok:
            print(f"  ❌ Gemini error {resp.status_code}: {resp.text[:250]}")
            return None, False
        return resp.json()["candidates"][0]["content"]["parts"][0]["text"].strip(), False
    except Exception as e:
        print(f"  ❌ Gemini exception: {e}")
        return None, False

def call_gemini(prompt, max_tokens=2048):
    if not GEMINI_API_KEYS:
        return None
    for i, key in enumerate(GEMINI_API_KEYS):
        print(f"  🤖 Trying Gemini key {i + 1}/{len(GEMINI_API_KEYS)}...")
        result, quota_exceeded = call_gemini_with_key(prompt, key, max_tokens)
        if result:
            return result
        if not quota_exceeded:
            return None
    print(f"  ❌ All {len(GEMINI_API_KEYS)} Gemini keys exhausted")
    return None

def call_groq(prompt, max_tokens=2048):
    if not GROQ_API_KEY:
        return None
    try:
        resp = requests.post(
            "https://api.groq.com/openai/v1/chat/completions",
            headers={"Authorization": f"Bearer {GROQ_API_KEY}", "Content-Type": "application/json"},
            json={
                "model":       "llama-3.1-8b-instant",  # 500k tokens/day free vs 100k for 70b
                "messages":    [{"role": "user", "content": prompt}],
                "temperature": 0.7,
                "max_tokens":  max_tokens,
            },
            timeout=60,
        )
        if not resp.ok:
            print(f"  ❌ Groq error {resp.status_code}: {resp.text[:250]}")
            return None
        return resp.json()["choices"][0]["message"]["content"].strip()
    except Exception as e:
        print(f"  ❌ Groq exception: {e}")
        return None

# ─── ARTICLE REWRITE ──────────────────────────────────────────────────────────

# ─── ACCURACY VERIFICATION ─────────────────────────────────────────────────────
# A second, independent AI call that cross-checks the generated article
# against its actual source material, and flags anything invented — names,
# numbers, quotes, dates, claims — that isn't actually supported by the
# source. This runs after generation but before the article is saved, so a
# flagged article is skipped entirely rather than published with fabricated
# details. It costs one extra API call per article, but catches a category
# of error the word-count/byline checks can't: the model getting a fact
# wrong or inventing a detail while still writing fluently and at length.

def build_verification_prompt(article, body):
    full_text = article.get("full_text", "")
    if full_text:
        source_block = f"SOURCE TEXT:\n{full_text}"
    else:
        source_block = f"SOURCE SUMMARY (RSS excerpt):\n{article['summary']}"

    return f"""You are a rigorous, skeptical fact-checker reviewing a news article before publication.

{source_block}

ARTICLE TO CHECK:
{body}

Your only job: compare the ARTICLE against the SOURCE. Identify any specific factual claim in the ARTICLE — a name, number, statistic, date, quote, title, location, or event detail — that is NOT directly stated in or reasonably inferable from the SOURCE. Ignore paraphrasing, rewording, reordering, and stylistic differences — those are expected and fine. Only flag genuine invented or unsupported facts.

Respond with EXACTLY one of these two formats, nothing else:
- If the article stays fully grounded in the source: CLEAN
- If you find unsupported claims: FLAGGED: <comma-separated list of the specific unsupported claims, each under 15 words>"""

def verify_accuracy(article, body):
    prompt = build_verification_prompt(article, body)
    result = call_groq(prompt, max_tokens=300)
    if not result:
        result = call_gemini(prompt, max_tokens=300)

    if not result:
        # Verification itself failed (both AIs down) — don't block publishing
        # on an infrastructure failure; log it and let the article through.
        print("  ⚠️  Accuracy check unavailable (both AIs failed) — publishing without verification")
        return True

    result = result.strip()
    if result.upper().startswith("CLEAN"):
        print("  ✅ Accuracy check passed")
        return True

    print(f"  🚫 Accuracy check FLAGGED unsupported claims: {result[:300]}")
    return False

def rewrite_article(article):
    # Attempt to fetch the full article body from source URL so the AI has
    # enough real facts to write a complete 800-word piece without inventing.
    # Falls back gracefully to the RSS summary if the page is paywalled/fails.
    if not article.get("full_text") and article.get("url"):
        print(f"  🌐 Fetching full article from source...")
        full_text = fetch_full_article(article["url"])
        if full_text:
            article = {**article, "full_text": full_text}

    prompt = build_article_prompt(article)

    # Groq first — faster and more reliable on free tier
    print(f"  🤖 Trying Groq (primary)...")
    text = call_groq(prompt, max_tokens=2048)
    if text:
        words = len(text.split())
        print(f"  ✅ Groq: {words} words")
        if words >= 700 and not has_fabricated_byline(text):
            return text
        if words >= 700:
            print(f"  ⚠️  Fabricated byline/correspondent detected — trying Gemini for a clean rewrite")
        else:
            print(f"  ⚠️  Too short ({words} words) — trying Gemini for a fuller rewrite")

    # Gemini fallback
    print(f"  🔄 Trying Gemini fallback...")
    text = call_gemini(prompt, max_tokens=2048)
    if text:
        words = len(text.split())
        print(f"  ✅ Gemini: {words} words")
        if words >= 700 and not has_fabricated_byline(text):
            return text
        if words >= 700:
            print(f"  ⚠️  Fabricated byline/correspondent detected in Gemini output too — skipping article")
        else:
            print(f"  ⚠️  Too short ({words} words)")

    print("  ❌ All AIs failed — skipping article")
    return None

# Safety net beyond the prompt instruction: catches cases where the model
# names a "correspondent"/"reporter" anyway — e.g. carrying over a real
# journalist's name from the source material — or implies Veridus has a
# field presence / conducted original interviews, which it does not (this
# is a desk rewrite of published reporting, no reporters on the ground).
# Any match means the article is rejected outright rather than published
# with a fabricated byline or implied firsthand reporting.
_BYLINE_PATTERN = re.compile(
    r"\b(our|veridus'?s?)\s+(senior\s+)?(correspondent|reporter|journalist|team)\b"
    r"|\bcorrespondent\s+[A-Z][a-z]+\s+[A-Z][a-z]+\b"
    r"|\breporting\s+for\s+veridus\b"
    r"|\b(told|speaking\s+to|in\s+an?\s+interview\s+with|spoke\s+to)\s+veridus\b"
    r"|\bveridus\s+(witnessed|can\s+confirm|visited|travell?ed\s+to)\b"
    r"|\bwhen\s+this\s+reporter\s+visited\b",
    re.IGNORECASE,
)

def has_fabricated_byline(text):
    return bool(_BYLINE_PATTERN.search(text))

# ─── SEO METADATA GENERATION ──────────────────────────────────────────────────

def generate_seo(article, body):
    prompt = build_seo_prompt(article, body)

    # Groq first
    raw = call_groq(prompt, max_tokens=400)
    if not raw:
        raw = call_gemini(prompt, max_tokens=400)
    if not raw:
        return None

    raw = re.sub(r"```json|```", "", raw).strip()

    try:
        seo = json.loads(raw)
        required = ["seo_title", "meta_description", "focus_keyword", "secondary_keywords", "seo_slug"]
        if all(k in seo for k in required):
            return seo
    except Exception as e:
        print(f"  ⚠️  SEO JSON parse failed: {e}")

    return None

# ─── HUGO MARKDOWN ────────────────────────────────────────────────────────────

def build_hugo_markdown(article, body, seo, image_file=None, image_info=None):
    now   = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
    niche = article["niche"]
    _, categories, base_tags = NICHE_META.get(niche, ("World", '["News"]', '["world news"]'))

    display_title = article["title"].replace('"', '\\"')
    seo_title     = seo["seo_title"].replace('"', '\\"')        if seo else display_title
    meta_desc     = seo["meta_description"].replace('"', '\\"') if seo else article["summary"][:155].replace('"', '\\"')
    focus_kw      = seo["focus_keyword"].replace('"', '\\"')    if seo else ""
    sec_kws       = json.dumps(seo["secondary_keywords"])       if seo else "[]"

    try:
        base_list = json.loads(base_tags)
        sec_list  = seo["secondary_keywords"] if seo else []
        all_tags  = list(dict.fromkeys(base_list + sec_list))[:8]
        tags_str  = json.dumps(all_tags)
    except Exception:
        tags_str = base_tags

    # Build image front matter fields
    image_fm = ""
    if image_file and image_info:
        attr  = image_info.get("attribution", "Wikimedia Commons").replace('"', "'")
        lic   = image_info.get("license", "").replace('"', "'")
        lic_url = image_info.get("license_url", "").replace('"', "'")
        image_fm = f"""feature_image: "{image_file}"
image_attribution: "{attr}"
image_license: "{lic}"
image_license_url: "{lic_url}"
"""

    return f"""---
title: "{display_title}"
seo_title: "{seo_title}"
date: {now}
lastmod: {now}
draft: false
featured: false
categories: {categories}
tags: {tags_str}
description: "{meta_desc}"
focus_keyword: "{focus_kw}"
keywords: {sec_kws}
{image_fm}---

{body}
"""

# ─── INTERNAL LINKING ─────────────────────────────────────────────────────────
# Links a few mentions of this article's own tags to that tag's taxonomy
# archive page (/tags/{tag}/) instead of one specific past article. Hugo
# automatically populates that page with every article sharing the tag, so
# clicking a linked term shows a reader every relevant piece, not just one —
# and the list grows on its own as more articles get published, with no
# separate index to build or maintain here.

def insert_internal_links(body, tags, max_links=3):
    """
    Replaces the first mention of each candidate tag with a link to that
    tag's archive page. Skips headings, skips lines that already contain a
    link, requires a real word (4+ characters) to avoid linking on noise,
    and caps the total links inserted so articles don't end up over-linked.
    """
    if not tags:
        return body

    candidates = [(t.strip(), f"/tags/{slugify(t)}/") for t in tags if len(t.strip()) >= 4]

    lines = body.split("\n")
    links_added = 0

    for i, line in enumerate(lines):
        if links_added >= max_links:
            break
        if not line.strip() or line.strip().startswith("#"):
            continue
        if "](" in line:
            continue  # a markdown link already lives on this line — leave it alone

        for phrase, permalink in candidates:
            if links_added >= max_links:
                break
            pattern = re.compile(r'\b' + re.escape(phrase) + r'\b', re.IGNORECASE)
            match = pattern.search(line)
            if match:
                matched_text = match.group(0)
                new_line = line[:match.start()] + f"[{matched_text}]({permalink})" + line[match.end():]
                lines[i] = new_line
                line = new_line
                links_added += 1

    if links_added:
        print(f"  🔗 Inserted {links_added} tag-archive link(s)")
    return "\n".join(lines)

def save_hugo_post(article, body, seo):
    """
    Saves article as a Hugo page bundle (directory with index.md + cover image).
    Page bundles allow Hugo to find the feature image as a page resource.
    Structure: content/news/{niche}/{date}-{slug}/index.md
                                                  cover.jpg  (if found)
    """
    niche_dir = CONTENT_DIR / article["niche"]
    date_str  = datetime.now().strftime("%Y-%m-%d")
    slug      = slugify(seo["seo_slug"]) if seo and seo.get("seo_slug") else slugify(article["title"])

    # Make unique bundle directory
    bundle_dir = niche_dir / f"{date_str}-{slug}"
    counter = 1
    while bundle_dir.exists():
        bundle_dir = niche_dir / f"{date_str}-{slug}-{counter}"
        counter += 1
    bundle_dir.mkdir(parents=True, exist_ok=True)

    # Fetch feature image from Wikimedia into the bundle directory
    image_file, image_info = get_feature_image(article, seo, bundle_dir)

    # Link a few mentions of this article's own tags to their tag-archive pages
    tag_candidates = seo.get("secondary_keywords", []) if seo else []
    body = insert_internal_links(body, tag_candidates)

    # Build and write markdown with image front matter
    md_content = build_hugo_markdown(article, body, seo, image_file, image_info)
    index_file = bundle_dir / "index.md"
    index_file.write_text(md_content, encoding="utf-8")

    print(f"  💾 Saved: {index_file}")
    if seo:
        print(f"  🔑 Focus keyword: {seo.get('focus_keyword', 'n/a')}")
        print(f"  📄 Meta: {seo.get('meta_description', '')[:80]}...")

# ─── MAIN ─────────────────────────────────────────────────────────────────────

def main():
    import sys
    sports_only = "--sports-only" in sys.argv

    print(f"\n{'=' * 65}")
    print(f"🚀 Veridus Auto News Poster{'  [SPORTS ONLY]' if sports_only else ''}")
    print(f"   {datetime.now(timezone.utc).strftime('%Y-%m-%d %H:%M UTC')}")
    print(f"{'=' * 65}")

    if not GEMINI_API_KEYS and not GROQ_API_KEY:
        print("❌ No AI API keys set. Add GEMINI_API_KEY_1 or GROQ_API_KEY to GitHub Secrets.")
        return

    print(f"✅ Gemini keys loaded: {len(GEMINI_API_KEYS)} key(s)")
    if GROQ_API_KEY:
        print(f"✅ Groq key loaded (length: {len(GROQ_API_KEY)})")

    posted_log  = load_posted_log()          # dict: {article_id: iso_timestamp}
    posted_ids  = set(posted_log.keys())     # set used for fast membership checks
    total_saved = 0

    all_niches    = ["sports", "politics", "global-affairs", "business", "climate", "curious"]
    active_niches = ["sports"] if sports_only else all_niches
    for niche in active_niches:
        print(f"\n📰 [{niche.upper()}]")

        # ── Daily cap check ────────────────────────────────────────────
        already_today = count_today_posts(niche)
        remaining_today = max(0, DAILY_POST_LIMIT - already_today)
        if remaining_today == 0:
            print(f"   📊 Daily cap reached ({DAILY_POST_LIMIT}/day) — skipping {niche}")
            continue
        print(f"   📊 {already_today}/{DAILY_POST_LIMIT} posts today — {remaining_today} slot(s) remaining")

        articles = fetch_rss_articles(niche, posted_ids)

        # ── Viral sort — most cross-covered stories first ──────────────
        articles = score_by_virality(articles)

        limit       = min(NICHE_LIMITS.get(niche, 1), remaining_today)
        saved_count = 0

        for article in articles:
            if saved_count >= limit:
                break

            age_str = ""
            if article.get("pub_date"):
                mins_ago = int((datetime.now(timezone.utc) - article["pub_date"]).total_seconds() / 60)
                age_str = f" [{mins_ago}m ago]" if mins_ago < 60 else f" [{mins_ago // 60}h ago]"

            print(f"\n  📝 {article['title'][:70]}{age_str}")

            body = rewrite_article(article)
            if not body:
                continue

            print(f"  🔎 Verifying accuracy against source...")
            if not verify_accuracy(article, body):
                print(f"  ❌ Skipping article — failed accuracy check")
                continue

            print(f"  🔍 Generating SEO metadata...")
            seo = generate_seo(article, body)
            if seo:
                print(f"  ✅ SEO generated")
            else:
                print(f"  ⚠️  SEO generation failed — using defaults")

            save_hugo_post(article, body, seo)

            posted_log[article["id"]] = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
            posted_ids.add(article["id"])
            saved_count += 1
            total_saved += 1

            # Pause between articles to avoid hitting Gemini RPM limit
            if saved_count < limit:
                print(f"  ⏳ Pausing 8s before next article...")
                time.sleep(8)

    save_posted_log(posted_log)   # auto-pruned to 14 days on next load
    print(f"\n{'=' * 65}")
    print(f"✨ Done — {total_saved} articles posted.")
    print(f"{'=' * 65}\n")

if __name__ == "__main__":
    main()