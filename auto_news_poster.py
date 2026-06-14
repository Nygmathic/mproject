#!/usr/bin/env python3
"""
Veridus.space Auto News Poster
- Fetches global news from RSS + YouTube channels
- Fetches full article body from source URL for rich, accurate rewrites
- Prioritises LATEST content — each niche has a recency window
- YouTube: pulls transcripts for rich rewrites (falls back to description)
- Rewrites entirely in Veridus voice — original, owned content
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
    "africa":         2,
    "sports":         2,  # Daily cap of 4 still applies — post-match runs catch up
    "business":       1,
    "climate":        1,
    "law":            2,
    "curious":        2,
    # "culture":        2,  # ← uncomment to activate
}

# Maximum age of an article to be considered — oldest allowed per niche.
# Kept tight so the poster never re-surfaces yesterday's news.
# The 2-hour cron means a 6h window gives 3 chances to pick up a story.
RECENCY_HOURS = {
    "sports":         3,   # match reports must be same-day
    "politics":       6,   # tight — political news moves fast
    "africa":         6,   # tight — same reason
    "business":       6,   # markets move daily
    "curious":        12,  # weird news is slow-burn; slight slack
    # "culture":        12,  # ← uncomment to activate
    "climate":        24,  # climate stories don't break by the hour
    "law":            48,  # judgments publish on court schedules
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
        "https://feeds.reuters.com/Reuters/PoliticsNews",
        # ── Global Affairs (merged) ───────────────────────────────────
        "https://foreignpolicy.com/feed/",
        "https://rss.nytimes.com/services/xml/rss/nyt/World.xml",
        "https://www.theguardian.com/world/rss",
        "https://www.dw.com/en/world/rss",
        "https://feeds.reuters.com/Reuters/worldNews",
        # ── United Nations ────────────────────────────────────────────
        "https://news.un.org/feed/subscribe/en/news/all/feed/rss.xml",
    ],
    "business": [
        "https://rss.nytimes.com/services/xml/rss/nyt/Business.xml",
        "https://feeds.bbci.co.uk/news/business/rss.xml",
        "https://www.dw.com/en/economy/rss",
        "https://www.theafricareport.com/feed/",
        "https://www.premiumtimesng.com/feed",
        "https://feeds.reuters.com/reuters/businessNews",
    ],
    "sports": [
        # ── Premier League ───────────────────────────────────────────
        "https://www.theguardian.com/football/premierleague/rss",
        "https://feeds.bbci.co.uk/sport/football/premier-league/rss.xml",
        "https://www.skysports.com/rss/12040",
        "https://www.fourfourtwo.com/rss",
        # ── African Football & Sport ─────────────────────────────────
        "https://supersport.com/rss",
        "https://www.bbc.co.uk/sport/africa/rss.xml",
        "https://www.goal.com/en-ke/rss",
        "https://www.cafonline.com/rss",
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
    "africa": [
        # ── United Nations Africa & African Union ─────────────────────
        "https://news.un.org/feed/subscribe/en/news/topic/africa/feed/rss.xml",
        "https://au.int/en/pressreleases/rss",
        # ── Pan-African ──────────────────────────────────────────────
        "https://allafrica.com/tools/headlines/rdf/latest/headlines.rdf",
        "https://www.theafricareport.com/feed/",
        "https://www.africanews.com/feed/",
        # ── East Africa ──────────────────────────────────────────────
        "https://eastafrican.nation.africa/feed",
        "https://www.monitor.co.ug/rss",
        "https://www.theeastafrican.co.ke/rss",
        "https://www.standardmedia.co.ke/rss",
        "https://nation.africa/kenya/rss.xml",
        # ── West Africa ──────────────────────────────────────────────
        "https://www.premiumtimesng.com/feed",
        # ── Southern Africa ──────────────────────────────────────────
        "https://www.dailymaverick.co.za/feed/",
        "https://www.news24.com/rss",
        # ── North Africa ─────────────────────────────────────────────
        "https://www.egyptindependent.com/feed/",
        "https://www.middleeasteye.net/rss",
    ],
    "curious": [
        # ── Verified Weird & Bizarre News ────────────────────────────
        "https://feeds.reuters.com/reuters/oddlyEnoughNews",            # Reuters Oddly Enough
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
    # ── CULTURE — paused until feature images are ready ─────────────
    # Uncomment this entire block to activate:
    # "culture": [
    #     "https://www.okayafrica.com/feed/",
    #     "https://www.pulse.co.ke/rss",
    #     "https://www.bellanaija.com/feed/",
    #     "https://www.notjustok.com/feed/",
    #     "https://www.theguardian.com/film/rss",
    #     "https://variety.com/feed/",
    #     "https://deadline.com/feed/",
    #     "https://www.indiewire.com/feed/",
    #     "https://pitchfork.com/rss/news/",
    #     "https://www.rollingstone.com/music/feed/",
    #     "https://www.nme.com/feed",
    #     "https://www.billboard.com/feed/",
    #     "https://www.theguardian.com/culture/rss",
    #     "https://www.theguardian.com/music/rss",
    #     "https://www.theatlantic.com/feed/channel/entertainment/",
    # ],
}

# ─── NICHE METADATA ───────────────────────────────────────────────────────────

NICHE_META = {
    "politics":       ("Politics",      '["Politics", "News"]',       '["politics", "world news", "global politics", "geopolitics", "diplomacy"]'),
    "business":       ("Business",      '["Business", "News"]',       '["business", "economy", "markets", "trade"]'),
    "climate":        ("Climate",       '["Climate", "News"]',        '["climate change", "environment", "sustainability", "global warming"]'),
    "sports":         ("Sports",        '["Sports"]',                 '["sports", "football", "premier league", "african football", "athletics"]'),
    "africa":         ("Africa",        '["Africa", "News"]',         '["africa", "african politics", "african business", "world news"]'),
    "law":            ("Law",           '["Law", "News"]',            '["kenya law", "court ruling", "supreme court", "high court", "court of appeal"]'),
    "curious":        ("Curious",       '["Curious", "News"]',        '["bizarre", "unusual", "strange", "odd news", "weird science"]'),
    # "culture": ("Culture", '["Culture", "News"]', '["culture", "music", "film", "african arts", "afrobeats", "cinema"]'),  # ← uncomment to activate
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
    - For YouTube entries, attempts to fetch transcript
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
        # Law: must mention courts or legal proceedings
        "law":     ["court", "ruling", "judgment", "judge", "appeal", "tribunal",
                    "supreme court", "high court", "verdict", "sentenced", "convicted",
                    "acquitted", "constitution", "judicial", "injunction", "magistrate",
                    "lawsuit", "prosecution", "acquittal", "bench", "hearing", "petition"],
        # Business: must be economic/financial
        "business":["economy", "market", "trade", "gdp", "inflation", "investment",
                    "stock", "bank", "currency", "company", "revenue", "profit",
                    "startup", "merger", "acquisition", "financial", "debt", "growth",
                    "price", "cost", "fund", "budget", "tax", "export", "import",
                    "supply chain", "oil price", "energy", "billion", "million"],
        # Curious: very loose — just needs to be factual/interesting non-standard news
        # No required filter — rely on the curious RSS sources to self-select
        # "curious": [],  # no filter needed — sources handle relevance
        # Politics & Africa: no required filter — broad enough categories
        # "politics": [],
        # "africa": [],
    }

    NICHE_BANNED = {
        # Sports feed should never get climate/law/business articles
        "sports":  ["climate", "court ruling", "stock market", "inflation", "election",
                    "parliament", "legislation", "gdp", "treaty", "diplomacy"],
        # Climate feed should never get sports/political articles
        "climate": ["football", "premier league", "match result", "goal", "transfer",
                    "election", "parliament", "stock market", "gdp"],
        # Law feed: only court decisions — no lawyer appointments, bar news
        "law":     ["law society", "lsk", "bar association", "lawyer appointed",
                    "advocate appointed", "elected president of", "bar council",
                    "legal profession", "attorney general appointed"],
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
    source_type = article.get("source_type", "rss")

    niche_guidance = {
        "politics":       "Cover political developments AND international affairs — domestic politics, geopolitics, diplomacy, international relations, wars, elections, and global governance. Represent multiple regional perspectives including voices from the Global South, Europe, Russia, China, Africa, and the Middle East. This section replaces what was formerly 'global affairs'.",
        "business":       "Cover business and economic developments with global impact. Include emerging market perspectives from Africa, Asia, and Latin America alongside Western economies.",
        "sports":         "Cover sport with emphasis on African football (CAF, AFCON, PSL, KPL) and the Premier League. Write match reports with energy and precision. Cover athletics, rugby, and other disciplines too. Do not centre only American sport.",
        "climate":        "Emphasise human and economic impact of climate change, especially on the most vulnerable regions. Ground all claims in science. Avoid alarmism.",
        "africa":         "Write from an African-centred perspective. Treat African nations and people as full agents of their own story. Avoid patronising or 'Western saviour' framing entirely. African football and sports stories belong in Sports, not here.",
        "law":            "STRICT: Cover ONLY formal court decisions — judgments, rulings, and orders from the Kenya Supreme Court, Court of Appeal, High Court, and equivalent courts in Commonwealth countries. Do NOT cover legal profession news, bar association events, lawyer appointments, or general legal commentary. If the story is about a court's actual decision or ruling, cover it. If it is about lawyers or the legal profession generally, skip it.",
        "curious":        "Cover genuinely strange, bizarre, or surprising true stories from around the world. The tone should be engaged and intelligent — curious and amused, not mocking. Every claim must be factual and verifiable. No sensationalism, no fabrication.",
        # "culture":        "Cover culture the way The Guardian does — with intellectual seriousness and genuine passion. Music, film, theatre, art, books. Give priority to African artists, Afrobeats, Nollywood, Kenyan arts. When covering global culture, find the African or Global South angle. Never gossip. Never celebrity trivia. Ask what the work means, what it reveals, why it matters now.",
    }

    guidance = niche_guidance.get(niche, "Cover this story with global context and balance.")

    source_note = ""
    if source_type == "youtube_transcript":
        source_note = "\nSOURCE NOTE: The summary below is a spoken transcript from a video report. Rewrite it entirely as a polished written article — remove all spoken-word patterns, filler phrases, and repetition."
    elif source_type == "youtube_description":
        source_note = "\nSOURCE NOTE: The summary is from a video description. Expand it significantly using your knowledge of the topic."

    # Use the full fetched article body when available; fall back to RSS summary
    full_text = article.get("full_text", "")
    if full_text:
        source_block = f"SOURCE TEXT (full article body — use all facts contained here):\n{full_text}"
    else:
        source_block = f"SUMMARY (RSS excerpt only — base the article strictly on these facts):\n{article['summary']}"

    return f"""You are a senior international correspondent writing for Veridus — an independent African publication with the precision of The Guardian and the voice of a publication that thinks for itself.

Write a complete, original news article. This content must be entirely Veridus's own — do not reproduce or closely paraphrase the source material. Transform it into something new.{source_note}

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
- Use two or three descriptive H2 subheadings (## Heading) to break the article into sections
- Tone: Authoritative, measured, internationally minded
- Vocabulary: Precise journalistic English. No clichés. No sensationalism.
- Do NOT mention or reference any news outlet, wire service, or publication
- Do NOT include the main headline — body text and subheadings only
- Do NOT use bullet points or numbered lists — flowing prose only
- Write in English only

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
# Accepted licenses: CC0, CC BY, CC BY-SA, Public Domain.
# Saves image locally into the Hugo page bundle so it is permanently owned.
# If no suitable image is found the article posts without one — graceful fallback.

# Licenses we trust — reject anything else (e.g. CC BY-NC, fair use, unknown)
ACCEPTED_LICENSES = {
    "cc0", "cc-by", "cc-by-sa", "cc-by-2.0", "cc-by-3.0", "cc-by-4.0",
    "cc-by-sa-2.0", "cc-by-sa-3.0", "cc-by-sa-4.0",
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
    "africa":         "Africa continent map",
    "law":            "supreme court building",
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
                or "creative commons" in license_short
                or "public domain" in license_short
            ) and "nc" not in lic_normalised and "nd" not in lic_normalised

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
            "africa":         "Africa map",
            "sports":         "football stadium",
            "business":       "stock exchange trading floor",
            "climate":        "climate change flooding",
            "law":            "courtroom gavel",
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
        if words >= 600:
            return text
        print(f"  ⚠️  Too short ({words} words) — trying Gemini for a fuller rewrite")

    # Gemini fallback
    print(f"  🔄 Trying Gemini fallback...")
    text = call_gemini(prompt, max_tokens=2048)
    if text:
        words = len(text.split())
        print(f"  ✅ Gemini: {words} words")
        if words >= 600:
            return text
        print(f"  ⚠️  Too short ({words} words)")

    print("  ❌ All AIs failed — skipping article")
    return None

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

    all_niches    = ["sports", "africa", "politics", "business", "climate", "law", "curious"]  # "culture" paused — add back when feature images ready
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