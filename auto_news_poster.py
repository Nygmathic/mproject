#!/usr/bin/env python3
"""
Veridus.space Auto News Poster
- Fetches global news from RSS feeds
- Fetches full article body from source URL for rich, accurate rewrites
- Prioritises LATEST content — each niche has a recency window
- Rewrites entirely in Veridus voice — original, owned content
- AI: Google Gemini 3.6 Flash (free tier) — rotates across up to 6 keys
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

# Gemini model — current stable Flash workhorse (GA July 2026)
# Check https://ai.google.dev/gemini-api/docs/models for updates
GEMINI_MODEL = "gemini-3.6-flash"

CONTENT_DIR = Path("content")
POSTED_LOG  = Path(".posted_articles.json")

# Hard daily cap — max posts per niche per calendar day (UTC)
DAILY_POST_LIMIT = 4

# How many articles to attempt per run (throttles per-run usage)
NICHE_LIMITS = {
    "politics":       2,
    "africa":         2,
    "sports":         2,
    "business":       1,
    "climate":        1,
    "law":            2,
    "curious":        2,
}

# Maximum age of an article to be considered — oldest allowed per niche.
RECENCY_HOURS = {
    "sports":         3,
    "politics":       6,
    "africa":         6,
    "business":       6,
    "curious":        12,
    "climate":        24,
    "law":            48,
}

# ─── RSS FEEDS ────────────────────────────────────────────────────────────────

RSS_FEEDS = {
    "politics": [
        "https://rss.nytimes.com/services/xml/rss/nyt/Politics.xml",
        "https://feeds.npr.org/1014/rss.xml",
        "https://www.theguardian.com/politics/rss",
        "https://www.dw.com/en/politics/rss",
        "https://www.euronews.com/rss?format=mrss&level=theme&name=news",
        "https://www.aljazeera.com/xml/rss/all.xml",
        "https://foreignpolicy.com/feed/",
        "https://rss.nytimes.com/services/xml/rss/nyt/World.xml",
        "https://www.theguardian.com/world/rss",
        "https://www.dw.com/en/world/rss",
        "https://news.un.org/feed/subscribe/en/news/all/feed/rss.xml",
    ],
    "business": [
        "https://rss.nytimes.com/services/xml/rss/nyt/Business.xml",
        "https://feeds.bbci.co.uk/news/business/rss.xml",
        "https://www.dw.com/en/economy/rss",
        "https://www.theafricareport.com/feed/",
        "https://www.premiumtimesng.com/feed",
    ],
    "sports": [
        "https://www.theguardian.com/football/premierleague/rss",
        "https://feeds.bbci.co.uk/sport/football/premier-league/rss.xml",
        "https://www.skysports.com/rss/12040",
        "https://www.fourfourtwo.com/rss",
        "https://supersport.com/rss",
        "https://www.bbc.co.uk/sport/africa/rss.xml",
        "https://www.goal.com/en-ke/rss",
        "https://www.cafonline.com/rss",
        "https://www.espn.com/espn/rss/soccer/news",
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
        "https://news.un.org/feed/subscribe/en/news/topic/africa/feed/rss.xml",
        "https://au.int/en/pressreleases/rss",
        "https://allafrica.com/tools/headlines/rdf/latest/headlines.rdf",
        "https://www.theafricareport.com/feed/",
        "https://www.africanews.com/feed/",
        "https://eastafrican.nation.africa/feed",
        "https://www.monitor.co.ug/rss",
        "https://www.theeastafrican.co.ke/rss",
        "https://www.standardmedia.co.ke/rss",
        "https://nation.africa/kenya/rss.xml",
        "https://www.premiumtimesng.com/feed",
        "https://www.dailymaverick.co.za/feed/",
        "https://www.news24.com/rss",
        "https://www.egyptindependent.com/feed/",
        "https://www.middleeasteye.net/rss",
    ],
    "law": [
        "https://www.judiciary.go.ke/feed/",
        "https://kenyalaw.org/feed/",
        "https://www.standardmedia.co.ke/rss",
        "https://nation.africa/kenya/rss.xml",
        "https://www.judiciary.uk/feed/",
        "https://www.supremecourt.uk/news/rss.xml",
    ],
    "curious": [
        "https://feeds.reuters.com/reuters/oddlyEnoughNews",
        "https://www.theguardian.com/news/series/weird/rss",
        "https://feeds.bbci.co.uk/news/have_your_say/rss.xml",
        "https://www.upi.com/RSS/Odd_News/",
        "https://ripleys.com/feed/",
        "https://www.odditycentral.com/feed",
        "https://www.atlasobscura.com/feeds/latest",
        "https://www.mentalfloss.com/rss.xml",
        "https://www.livescience.com/feeds/all",
        "https://www.iflscience.com/rss.xml",
    ],
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
    MAX_LOG_AGE_DAYS = 14
    cutoff = datetime.now(timezone.utc) - timedelta(days=MAX_LOG_AGE_DAYS)

    if POSTED_LOG.exists():
        try:
            raw = json.loads(POSTED_LOG.read_text())
            if isinstance(raw, list):
                raw = {aid: "2000-01-01T00:00:00Z" for aid in raw}
            pruned = {
                aid: ts for aid, ts in raw.items()
                if datetime.fromisoformat(ts.replace("Z", "+00:00")) >= cutoff
            }
            return pruned
        except Exception:
            return {}
    return {}

def save_posted_log(posted):
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
    today = datetime.now(timezone.utc).strftime("%Y-%m-%d")
    niche_dir = CONTENT_DIR / niche
    if not niche_dir.exists():
        return 0
    return sum(1 for d in niche_dir.iterdir() if d.is_dir() and d.name.startswith(today))

def score_by_virality(articles):
    now = datetime.now(timezone.utc)

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
        pub = article.get("pub_date")
        if not pub:
            return 0
        age_hours = max(0, (now - pub).total_seconds() / 3600)
        return max(0, 12 - int(age_hours / 2))

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

    raw_entries.sort(
        key=lambda x: x["pub_date"] or datetime.min.replace(tzinfo=timezone.utc),
        reverse=True
    )

    NICHE_REQUIRED = {
        "sports":  ["football", "soccer", "match", "league", "cup", "goal", "player",
                    "club", "sport", "game", "tournament", "champion", "coach", "team",
                    "afcon", "premier league", "caf", "fifa", "rugby", "athletics",
                    "cricket", "tennis", "basketball", "racing", "olympic", "score",
                    "fixture", "season", "transfer", "squad", "winger", "striker"],
        "climate": ["climate", "environment", "carbon", "emission", "warming", "fossil",
                    "renewable", "drought", "flood", "weather", "temperature", "glacier",
                    "deforestation", "pollution", "biodiversity", "ecosystem", "net zero",
                    "wildfire", "hurricane", "sea level", "methane", "solar", "wind energy"],
        "law":     ["court", "ruling", "judgment", "judge", "appeal", "tribunal",
                    "supreme court", "high court", "verdict", "sentenced", "convicted",
                    "acquitted", "constitution", "judicial", "injunction", "magistrate",
                    "lawsuit", "prosecution", "acquittal", "bench", "hearing", "petition"],
        "business":["economy", "market", "trade", "gdp", "inflation", "investment",
                    "stock", "bank", "currency", "company", "revenue", "profit",
                    "startup", "merger", "acquisition", "financial", "debt", "growth",
                    "price", "cost", "fund", "budget", "tax", "export", "import",
                    "supply chain", "oil price", "energy", "billion", "million"],
    }

    NICHE_BANNED = {
        "sports":  ["climate", "court ruling", "stock market", "inflation", "election",
                    "parliament", "legislation", "gdp", "treaty", "diplomacy",
                    "how to watch", "live stream", "tv channel", "kick-off time",
                    "where to watch", "free stream", "tv details"],
        "climate": ["football", "premier league", "match result", "goal", "transfer",
                    "election", "parliament", "stock market", "gdp"],
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

        banned = NICHE_BANNED.get(niche, [])
        if any(kw in text for kw in banned):
            print(f"  ⛔ Rejected [{niche}] (banned keyword): {entry['title'][:60]}")
            continue

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
        lines = [" ".join(ln.split()) for ln in raw.splitlines()]
        return "\n".join(ln for ln in lines if ln)

def fetch_full_article(url, min_chars=400, max_chars=6000):
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

        content = resp.content.decode(resp.apparent_encoding or "utf-8", errors="replace")

        extractor = _TextExtractor()
        extractor.feed(content)
        raw_text = extractor.get_text()

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
        "politics":       "Cover political developments AND international affairs — domestic politics, geopolitics, diplomacy, international relations, wars, elections, and global governance. Represent multiple regional perspectives including voices from the Global South, Europe, Russia, China, Africa, and the Middle East.",
        "business":       "Cover business and economic developments with global impact. Include emerging market perspectives from Africa, Asia, and Latin America alongside Western economies.",
        "sports":         "Cover sport with emphasis on African football (CAF, AFCON, PSL, KPL) and the Premier League. Write match reports with energy and precision. Cover athletics, rugby, and other disciplines too. Do not centre only American sport.",
        "climate":        "Emphasise human and economic impact of climate change, especially on the most vulnerable regions. Ground all claims in science. Avoid alarmism.",
        "africa":         "Write from an African-centred perspective. Treat African nations and people as full agents of their own story. Avoid patronising or 'Western saviour' framing entirely. African football and sports stories belong in Sports, not here.",
        "law":            "STRICT: Cover ONLY formal court decisions — judgments, rulings, and orders from the Kenya Supreme Court, Court of Appeal, High Court, and equivalent courts in Commonwealth countries. Do NOT cover legal profession news, bar association events, lawyer appointments, or general legal commentary.",
        "curious":        "Cover genuinely strange, bizarre, or surprising true stories from around the world. The tone should be engaged and intelligent — curious and amused, not mocking. Every claim must be factual and verifiable. No sensationalism, no fabrication.",
    }

    guidance = niche_guidance.get(niche, "Cover this story with global context and balance.")

    full_text = article.get("full_text", "")
    if full_text:
        source_block = f"SOURCE TEXT (full article body — use all facts contained here):\n{full_text}"
    else:
        source_block = f"SUMMARY (RSS excerpt only — base the article strictly on these facts):\n{article['summary']}"

    return f"""You are a senior international correspondent writing for Veridus — an independent African publication with the precision of The Guardian and the voice of a publication that thinks for itself.

Write a complete, original news article. This content must be entirely Veridus's own — do not reproduce or closely paraphrase the source material. Transform it into something new.

ACCURACY — NON-NEGOTIABLE (violations mean the article must not be published):
- Every fact, figure, date, name, statistic, and quote you write must come directly and explicitly from the headline or summary provided below. If the source material does not state it, do not write it.
- Do NOT invent, infer, or extrapolate any fact. If you do not have enough source material to confirm a detail, omit it entirely.
- Do NOT fabricate or paraphrase quotes. If a quote is not present word-for-word in the source material, do not include it.
- Do NOT speculate about causes, outcomes, or motivations unless the source explicitly states them.
- If the headline and summary provide limited facts, write a shorter, accurate article rather than a long, padded, inaccurate one.
- Numbers matter: do not round, inflate, or alter any figures.

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

ACCEPTED_LICENSES = {
    "cc0", "cc-by", "cc-by-sa", "cc-by-2.0", "cc-by-3.0", "cc-by-4.0",
    "cc-by-sa-2.0", "cc-by-sa-3.0", "cc-by-sa-4.0",
    "public domain", "pd", "cc-pd",
}

ACCEPTED_MIMES = {"image/jpeg", "image/png", "image/webp"}

_IMAGE_STOPWORDS = {
    "the","a","an","and","or","but","in","on","at","to","for","of","with",
    "by","from","as","is","was","are","were","be","been","has","have","had",
    "that","this","these","those","it","its","after","before","over","under",
    "how","why","what","who","when","where","will","would","could","should",
    "says","said","after","amid","into","than","about","against","during",
    "live","update","updates","latest","breaking","new","report","reports",
}

_NICHE_IMAGE_FALLBACKS = {
    "politics":       "parliament building",
    "business":       "stock exchange trading floor",
    "sports":         "football stadium",
    "climate":        "climate change flooding",
    "africa":         "Africa continent map",
    "law":            "supreme court building",
    "curious":        "magnifying glass mystery",
}

def build_image_query(article, seo):
    niche   = article["niche"]
    title   = article["title"]
    keyword = (seo.get("focus_keyword", "") if seo else "").strip()

    if keyword and (len(keyword.split()) >= 2 or len(keyword) >= 10):
        return keyword[:80]

    words = [
        w for w in re.sub(r"[^a-zA-Z0-9 ]", " ", title).split()
        if len(w) > 3 and w.lower() not in _IMAGE_STOPWORDS
    ]

    if len(words) >= 2:
        return " ".join(words[:4])

    return _NICHE_IMAGE_FALLBACKS.get(niche, "world news")

def fetch_wikimedia_image(search_query):
    try:
        search_resp = requests.get(
            "https://commons.wikimedia.org/w/api.php",
            params={
                "action":      "query",
                "generator":   "search",
                "gsrnamespace": 6,
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

        candidates = []
        for page in pages.values():
            info_list = page.get("imageinfo", [])
            if not info_list:
                continue
            info = info_list[0]

            mime = info.get("mime", "")
            if mime not in ACCEPTED_MIMES:
                continue

            width  = info.get("width", 0)
            height = info.get("height", 0)
            if width < 400 or height < 250:
                continue

            meta     = info.get("extmetadata", {})
            license_short = meta.get("LicenseShortName", {}).get("value", "").lower()
            license_url   = meta.get("LicenseUrl", {}).get("value", "")
            artist        = meta.get("Artist", {}).get("value", "")
            artist        = re.sub(r"<[^>]+>", "", artist).strip()[:100]

            lic_normalised = re.sub(r'[\s\.]+', '-', license_short).strip('-')
            lic_normalised = re.sub(r'-\d+\.\d+$', '', lic_normalised)
            accepted = (
                lic_normalised in ACCEPTED_LICENSES
                or any(a in lic_normalised for a in ACCEPTED_LICENSES)
                or "creative commons" in license_short
                or "public domain" in license_short
            ) and "nc" not in lic_normalised and "nd" not in lic_normalised

            if not accepted:
                print(f"  ⛔ Rejected license: {license_short}")
                continue

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

        candidates.sort(key=lambda x: x["width"], reverse=True)
        return candidates[0]

    except Exception as e:
        print(f"  ⚠️  Wikimedia search error: {e}")
        return None

def download_wikimedia_image(image_info, dest_dir):
    try:
        ext_map = {"image/jpeg": "jpg", "image/png": "png", "image/webp": "webp"}
        ext     = ext_map.get(image_info["mime"], "jpg")
        dest    = dest_dir / f"cover.{ext}"

        resp = requests.get(image_info["url"], timeout=30, stream=True)
        if not resp.ok:
            print(f"  ⚠️  Image download failed: HTTP {resp.status_code}")
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
    print(f"  🔎 Searching Wikimedia Commons for image...")
    query = build_image_query(article, seo)
    print(f"  🔍 Query: '{query}'")

    image_info = fetch_wikimedia_image(query)
    if not image_info:
        fallback_queries = {
            "politics":  "parliament building",
            "africa":    "Africa map",
            "sports":    "football stadium",
            "business":  "stock exchange trading floor",
            "climate":   "climate change flooding",
            "law":       "courtroom gavel",
            "curious":   "question mark abstract",
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

# ─── GEMINI AI ────────────────────────────────────────────────────────────────

def call_gemini_with_key(prompt, api_key, max_tokens=4096):
    """
    Call Gemini with a single key.
    Returns (text, should_try_next_key).
    - 429 (rate limited) → rotate to next key
    - 404 / 400 (bad model or request) → fatal, don't rotate
    - network/other → fatal, don't rotate
    """
    try:
        resp = requests.post(
            f"https://generativelanguage.googleapis.com/v1beta/models/{GEMINI_MODEL}:generateContent",
            params={"key": api_key},
            json={
                "contents": [{"parts": [{"text": prompt}]}],
                "generationConfig": {
                    # Note: temperature, top_p, top_k are deprecated in Gemini 3.x
                    # and have been intentionally removed.
                    "maxOutputTokens": max_tokens,
                },
            },
            headers={"Content-Type": "application/json"},
            timeout=90,
        )

        if resp.status_code == 429:
            print(f"  ⚠️  Gemini key rate limited (429) — trying next key...")
            return None, True

        if resp.status_code in (400, 403, 404):
            print(f"  ❌ Gemini config error {resp.status_code}: {resp.text[:250]}")
            return None, False

        if not resp.ok:
            print(f"  ❌ Gemini error {resp.status_code}: {resp.text[:250]}")
            return None, False

        data = resp.json()
        candidates = data.get("candidates", [])
        if not candidates:
            print(f"  ⚠️  Gemini returned no candidates — prompt may be blocked")
            return None, False

        text = candidates[0].get("content", {}).get("parts", [{}])[0].get("text", "").strip()
        if not text:
            print(f"  ⚠️  Gemini returned empty text")
            return None, False

        return text, False

    except Exception as e:
        print(f"  ❌ Gemini exception: {e}")
        return None, False

def call_gemini(prompt, max_tokens=4096):
    """Try each Gemini key in rotation until one succeeds."""
    if not GEMINI_API_KEYS:
        print("  ❌ No Gemini API keys configured")
        return None

    for i, key in enumerate(GEMINI_API_KEYS):
        print(f"  🤖 Trying Gemini key {i + 1}/{len(GEMINI_API_KEYS)}...")
        result, try_next = call_gemini_with_key(prompt, key, max_tokens)
        if result:
            return result
        if not try_next:
            return None

    print(f"  ❌ All {len(GEMINI_API_KEYS)} Gemini keys exhausted")
    return None

# ─── ARTICLE REWRITE ──────────────────────────────────────────────────────────

def rewrite_article(article):
    if not article.get("full_text") and article.get("url"):
        print(f"  🌐 Fetching full article from source...")
        full_text = fetch_full_article(article["url"])
        if full_text:
            article = {**article, "full_text": full_text}

    prompt = build_article_prompt(article)

    print(f"  🤖 Generating with Gemini...")
    text = call_gemini(prompt, max_tokens=4096)
    if not text:
        print("  ❌ Gemini failed — skipping article")
        return None

    words = len(text.split())
    print(f"  ✅ Gemini: {words} words")

    if words < 600:
        print(f"  ⚠️  Too short ({words} words) — skipping article")
        return None

    return text

# ─── SEO METADATA GENERATION ──────────────────────────────────────────────────

def generate_seo(article, body):
    prompt = build_seo_prompt(article, body)
    raw = call_gemini(prompt, max_tokens=600)
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
    niche_dir = CONTENT_DIR / article["niche"]
    date_str  = datetime.now(timezone.utc).strftime("%Y-%m-%d")
    slug      = slugify(seo["seo_slug"]) if seo and seo.get("seo_slug") else slugify(article["title"])

    bundle_dir = niche_dir / f"{date_str}-{slug}"
    counter = 1
    while bundle_dir.exists():
        bundle_dir = niche_dir / f"{date_str}-{slug}-{counter}"
        counter += 1
    bundle_dir.mkdir(parents=True, exist_ok=True)

    image_file, image_info = get_feature_image(article, seo, bundle_dir)

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

    if not GEMINI_API_KEYS:
        print("❌ No Gemini API keys set. Add GEMINI_API_KEY_1 to GitHub Secrets.")
        return

    print(f"✅ Gemini keys loaded: {len(GEMINI_API_KEYS)} key(s)")
    print(f"✅ Model: {GEMINI_MODEL}")

    posted_log  = load_posted_log()
    posted_ids  = set(posted_log.keys())
    total_saved = 0

    all_niches    = ["sports", "africa", "politics", "business", "climate", "law", "curious"]
    active_niches = ["sports"] if sports_only else all_niches

    for niche in active_niches:
        print(f"\n📰 [{niche.upper()}]")

        already_today = count_today_posts(niche)
        remaining_today = max(0, DAILY_POST_LIMIT - already_today)
        if remaining_today == 0:
            print(f"   📊 Daily cap reached ({DAILY_POST_LIMIT}/day) — skipping {niche}")
            continue
        print(f"   📊 {already_today}/{DAILY_POST_LIMIT} posts today — {remaining_today} slot(s) remaining")

        articles = fetch_rss_articles(niche, posted_ids)
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

            if saved_count < limit:
                print(f"  ⏳ Pausing 8s before next article...")
                time.sleep(8)

    save_posted_log(posted_log)
    print(f"\n{'=' * 65}")
    print(f"✨ Done — {total_saved} articles posted.")
    print(f"{'=' * 65}\n")

if __name__ == "__main__":
    main()