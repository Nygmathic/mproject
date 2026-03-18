#!/usr/bin/env python3
"""
Veridus.space Auto News Poster
- Fetches global news from RSS + YouTube channels
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
import feedparser
import requests
from datetime import datetime, timezone, timedelta
from pathlib import Path

# ─── CONFIG ───────────────────────────────────────────────────────────────────

GEMINI_API_KEYS = [
    key for key in [
        os.environ.get("GEMINI_API_KEY_1", ""),
        os.environ.get("GEMINI_API_KEY_2", ""),
        os.environ.get("GEMINI_API_KEY_3", ""),
    ] if key
]

GROQ_API_KEY = os.environ.get("GROQ_API_KEY", "")

CONTENT_DIR = Path("content/news")
POSTED_LOG  = Path(".posted_articles.json")

# How many articles to post per niche per run
NICHE_LIMITS = {
    "politics":       2,
    "global-affairs": 2,
    "africa":         2,
    "sports":         3,  # Higher — post-match reports, live events
    "business":       1,
    "climate":        1,
    "law":            2,
    "curious":        2,
    # "culture":        2,  # ← uncomment to activate
}

# Maximum age of an article to be considered — oldest allowed per niche
# Sports is tight (3 hrs) so post-match content goes up immediately
# Law is loose (48 hrs) since court judgments don't break by the minute
RECENCY_HOURS = {
    "sports":         3,
    "politics":       6,
    "global-affairs": 6,
    "africa":         8,
    "business":       8,
    "curious":        12,
    # "culture":        12,  # ← uncomment to activate
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
        "https://meduza.io/en/rss/all",
        "https://www.themoscowtimes.com/rss",
        "https://www.scmp.com/rss/91/feed",
        "https://www.sixthtone.com/feed",
        "https://www.aljazeera.com/xml/rss/all.xml",
        "https://feeds.reuters.com/Reuters/PoliticsNews",
    ],
    "business": [
        "https://rss.nytimes.com/services/xml/rss/nyt/Business.xml",
        "https://feeds.bbci.co.uk/news/business/rss.xml",
        "https://www.dw.com/en/economy/rss",
        "https://www.scmp.com/rss/92/feed",
        "https://www.caixinglobal.com/rss/",
        "https://www.theafricareport.com/feed/",
        "https://www.premiumtimesng.com/feed",
        "https://feeds.reuters.com/reuters/businessNews",
    ],
    "global-affairs": [
        "https://foreignpolicy.com/feed/",
        "https://rss.nytimes.com/services/xml/rss/nyt/World.xml",
        "https://www.theguardian.com/world/rss",
        "https://www.dw.com/en/world/rss",
        "https://www.euronews.com/rss?format=mrss&level=theme&name=news",
        "https://www.themoscowtimes.com/rss",
        "https://www.scmp.com/rss/91/feed",
        "https://www.aljazeera.com/xml/rss/all.xml",
        "https://feeds.reuters.com/Reuters/worldNews",
        # ── United Nations ────────────────────────────────────────────
        "https://news.un.org/feed/subscribe/en/news/all/feed/rss.xml",     # UN News — all topics
        "https://news.un.org/feed/subscribe/en/news/topic/peace-and-security/feed/rss.xml",  # UN Peace & Security
        "https://peacekeeping.un.org/en/rss.xml",                          # UN Peacekeeping
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
        "https://www.soccernet.ng/feed",
        "https://www.kickoff.com/feeds/rss.xml",
        "https://www.cafonline.com/rss",
        # ── ESPN ────────────────────────────────────────────────────
        "https://www.espn.com/espn/rss/soccer/news",                       # ESPN Soccer (global)
        "https://www.espn.com/espn/rss/news",                              # ESPN Top Headlines
        # ── Global Sport ────────────────────────────────────────────
        "https://feeds.bbci.co.uk/sport/rss.xml",
        "https://www.theguardian.com/sport/rss",
        "https://www.scmp.com/rss/95/feed",
    ],
    "climate": [
        "https://www.theguardian.com/environment/climate-crisis/rss",
        "https://www.dw.com/en/environment/rss",
        "https://insideclimatenews.org/feed/",
        "https://www.climatecentral.org/feed",
        "https://rss.nytimes.com/services/xml/rss/nyt/Climate.xml",
        "https://www.aljazeera.com/xml/rss/all.xml",
    ],
    "africa": [
        # ── United Nations Africa & African Union ─────────────────────
        "https://news.un.org/feed/subscribe/en/news/topic/africa/feed/rss.xml",   # UN News — Africa
        "https://au.int/en/rss",                                                   # African Union official
        "https://au.int/en/pressreleases/rss",                                     # AU Press Releases
        # ── Pan-African ──────────────────────────────────────────────
        "https://allafrica.com/tools/headlines/rdf/latest/headlines.rdf",
        "https://www.theafricareport.com/feed/",
        "https://www.africanews.com/feed/",
        "https://www.africatimes.com/feed/",
        # ── East Africa ──────────────────────────────────────────────
        "https://eastafrican.nation.africa/feed",
        "https://www.monitor.co.ug/rss",
        "https://www.theeastafrican.co.ke/rss",
        "https://www.standardmedia.co.ke/rss",
        "https://nation.africa/kenya/rss.xml",
        # ── West Africa ──────────────────────────────────────────────
        "https://www.premiumtimesng.com/feed",
        "https://www.ghanaweb.com/GhanaHomePage/NewsArchive/rss.php",
        "https://www.pulse.ng/rss",
        # ── Southern Africa ──────────────────────────────────────────
        "https://www.dailymaverick.co.za/feed/",
        "https://www.news24.com/rss",
        # ── North Africa ─────────────────────────────────────────────
        "https://www.egyptindependent.com/feed/",
        "https://www.middleeasteye.net/rss",
    ],
    "law": [
        # ── Kenya ────────────────────────────────────────────────────
        "https://kenyalaw.org/kenyalawblog/feed",
        "https://www.lawsociety.or.ke/feed/",
        "https://kenyalaw.org/kl/fileadmin/rss/CaseLaw.xml",
        # ── East & Southern Africa ───────────────────────────────────
        "https://legalbrief.co.za/feed/",
        "https://www.saflii.org/rss/zar.xml",
        "https://www.namibialii.org/rss/namiLII.xml",
        # ── UK Courts (Commonwealth persuasive authority) ────────────
        "https://www.judiciary.gov.uk/judgments/feed/",
        "https://www.judiciary.gov.uk/feed/",
        "https://www.legalrss.co.uk/journal/feed/",
        # ── Commonwealth & International ────────────────────────────
        "https://www.lawbreed.blog/feed",
        "https://www.lawglobalhub.com/feed",
        "https://www.ejiltalk.org/feed/",
        "https://opiniojuris.org/feed/",
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
    "politics":       ("Politics",      '["Politics", "News"]',       '["politics", "world news", "global politics"]'),
    "business":       ("Business",      '["Business", "News"]',       '["business", "economy", "markets", "trade"]'),
    "global-affairs": ("Global Affairs",'["Global Affairs", "News"]', '["global affairs", "geopolitics", "diplomacy", "international relations"]'),
    "climate":        ("Climate",       '["Climate", "News"]',        '["climate change", "environment", "sustainability", "global warming"]'),
    "sports":         ("Sports",        '["Sports"]',                 '["sports", "football", "premier league", "african football", "athletics"]'),
    "africa":         ("Africa",        '["Africa", "News"]',         '["africa", "african politics", "african business", "world news"]'),
    "law":            ("Law",           '["Law", "News"]',            '["kenya law", "court ruling", "supreme court", "high court", "commonwealth law"]'),
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
    if POSTED_LOG.exists():
        try:
            return json.loads(POSTED_LOG.read_text())
        except Exception:
            return []
    return []

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

    # Filter: skip entries with too-short summaries
    articles = []
    for entry in raw_entries:
        if len(entry.get("summary", "")) < 80:
            continue
        articles.append(entry)

    age_info = f"(recency window: {max_age_hours}h)"
    print(f"   {len(articles)} fresh articles found {age_info}")
    return articles

# ─── PROMPT: ARTICLE BODY ─────────────────────────────────────────────────────

def build_article_prompt(article):
    niche = article["niche"]
    source_type = article.get("source_type", "rss")

    niche_guidance = {
        "politics":       "Cover political developments with global context. Represent multiple regional perspectives including voices from the Global South, Europe, Russia, China, and Africa.",
        "business":       "Cover business and economic developments with global impact. Include emerging market perspectives from Africa, Asia, and Latin America alongside Western economies.",
        "global-affairs": "Cover geopolitics from a balanced, multi-polar perspective. Give equal weight to European, African, Asian, Russian, and Chinese perspectives — not only Western ones.",
        "sports":         "Cover sport with emphasis on African football (CAF, AFCON, PSL, KPL) and the Premier League. Write match reports with energy and precision. Cover athletics, rugby, and other disciplines too. Do not centre only American sport.",
        "climate":        "Emphasise human and economic impact of climate change, especially on the most vulnerable regions. Ground all claims in science. Avoid alarmism.",
        "africa":         "Write from an African-centred perspective. Treat African nations and people as full agents of their own story. Avoid patronising or 'Western saviour' framing entirely.",
        "law":            "Cover court decisions and legal developments with focus on Kenya (Supreme Court, Court of Appeal, High Court) and Commonwealth countries. Explain legal principles clearly for a general educated audience. Contextualise rulings within Kenyan and African constitutional law.",
        "curious":        "Cover genuinely strange, bizarre, or surprising true stories from around the world. The tone should be engaged and intelligent — curious and amused, not mocking. Every claim must be factual and verifiable. No sensationalism, no fabrication.",
        # "culture":        "Cover culture the way The Guardian does — with intellectual seriousness and genuine passion. Music, film, theatre, art, books. Give priority to African artists, Afrobeats, Nollywood, Kenyan arts. When covering global culture, find the African or Global South angle. Never gossip. Never celebrity trivia. Ask what the work means, what it reveals, why it matters now.",
    }

    guidance = niche_guidance.get(niche, "Cover this story with global context and balance.")

    source_note = ""
    if source_type == "youtube_transcript":
        source_note = "\nSOURCE NOTE: The summary below is a spoken transcript from a video report. Rewrite it entirely as a polished written article — remove all spoken-word patterns, filler phrases, and repetition."
    elif source_type == "youtube_description":
        source_note = "\nSOURCE NOTE: The summary is from a video description. Expand it significantly using your knowledge of the topic."

    return f"""You are a senior international correspondent writing for Veridus — an independent African publication with the precision of The Guardian and the voice of a publication that thinks for itself.

Write a complete, original news article. This content must be entirely Veridus's own — do not reproduce or closely paraphrase the source material. Transform it into something new.{source_note}

STRICT REQUIREMENTS:
- MINIMUM 800 words. MAXIMUM 1,200 words. Non-negotiable.
- 6 to 8 substantial paragraphs — no thin or short paragraphs
- Opening paragraph: Compelling and immediate — draws the reader in without starting with "In a" or "The"
- Second paragraph: Expand on the key facts and the stakes of the story
- Middle paragraphs: Context, background, analysis, multiple perspectives, historical parallels where relevant
- Penultimate paragraph: Reactions, implications, what different stakeholders are doing or saying
- Final paragraph: Forward-looking — what happens next and what readers should watch
- Use two or three descriptive H2 subheadings (## Heading) to break the article into sections
- Tone: Authoritative, measured, internationally minded
- Vocabulary: Precise journalistic English. No clichés. No sensationalism.
- Do NOT fabricate quotes, statistics, or facts
- Do NOT mention or reference any news outlet, wire service, or publication
- Do NOT include the main headline — body text and subheadings only
- Do NOT use bullet points or numbered lists — flowing prose only
- Write in English only

EDITORIAL FOCUS: {guidance}

NICHE: {niche.upper()}
HEADLINE: {article['title']}
SUMMARY: {article['summary']}

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

def build_image_query(article, seo):
    """Ask the AI for the best Wikimedia search term for this article."""
    title   = article["title"]
    niche   = article["niche"]
    keyword = seo.get("focus_keyword", "") if seo else ""

    prompt = f"""You are helping find a relevant, freely licensed image on Wikimedia Commons for a news article.

Given the article details below, return a SHORT search query (3-5 words maximum) that would find a relevant, factual image on Wikimedia Commons. Think: landmark, institution, person, flag, event, stadium, building, map — something specific that is likely to exist on Wikimedia.

RULES:
- Return ONLY the search query, nothing else
- No quotes, no punctuation, no explanation
- Prefer concrete nouns over abstract concepts
- Example good queries: "Kenya Supreme Court building", "Premier League trophy", "African Union headquarters"

NICHE: {niche}
HEADLINE: {title}
FOCUS KEYWORD: {keyword}

Search query:"""

    result = call_gemini(prompt, max_tokens=30)
    if not result:
        result = call_groq(prompt, max_tokens=30)
    if result:
        # Clean up — strip quotes and extra whitespace
        result = re.sub(r'["\'`]', '', result).strip()
        return result[:80]
    # Fallback: use focus keyword or title words
    return keyword or " ".join(title.split()[:4])

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
            candidates.append({
                "url":         info.get("thumburl") or info.get("url"),
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
            print(f"  ⚠️  Gemini key quota exceeded (429) — trying next key...")
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
                "model":       "llama-3.3-70b-versatile",
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
    prompt = build_article_prompt(article)

    text = call_gemini(prompt, max_tokens=2048)
    if text:
        words = len(text.split())
        print(f"  ✅ Gemini: {words} words")
        if words >= 300:
            return text
        print(f"  ⚠️  Too short ({words} words)")

    print(f"  🔄 Trying Groq fallback...")
    text = call_groq(prompt, max_tokens=2048)
    if text:
        words = len(text.split())
        print(f"  ✅ Groq: {words} words")
        if words >= 300:
            return text
        print(f"  ⚠️  Too short ({words} words)")

    print("  ❌ All AIs failed — skipping article")
    return None

# ─── SEO METADATA GENERATION ──────────────────────────────────────────────────

def generate_seo(article, body):
    prompt = build_seo_prompt(article, body)

    raw = call_gemini(prompt, max_tokens=400)
    if not raw:
        raw = call_groq(prompt, max_tokens=400)
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

    posted_ids  = set(load_posted_log())
    total_saved = 0

    all_niches    = ["sports", "africa", "politics", "global-affairs", "business", "climate", "law", "curious"]  # "culture" paused — add back when feature images ready
    active_niches = ["sports"] if sports_only else all_niches
    for niche in active_niches:
        print(f"\n📰 [{niche.upper()}]")
        articles = fetch_rss_articles(niche, posted_ids)

        limit       = NICHE_LIMITS.get(niche, 1)
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

            posted_ids.add(article["id"])
            saved_count += 1
            total_saved += 1

    save_posted_log(list(posted_ids)[-500:])
    print(f"\n{'=' * 65}")
    print(f"✨ Done — {total_saved} articles posted.")
    print(f"{'=' * 65}\n")

if __name__ == "__main__":
    main()