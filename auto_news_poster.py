import os
import sys
import time
import json
import re
import requests
import feedparser
from datetime import datetime, timezone

# ─── CONFIG & MODELS ─────────────────────────────────────────────────────────

GEMINI_PRIMARY_MODEL = "gemini-3.6-flash"
GEMINI_FALLBACK_MODELS = [
    "gemini-3.6-flash",
    "gemini-3.8-flash",
    "gemini-2.0-flash",
    "gemini-1.5-flash"
]

GROQ_MODEL = "llama-3.3-70b-versatile"

# Gather all available Gemini API keys from environment variables (1-6)
GEMINI_API_KEYS = [
    os.environ.get(f"GEMINI_API_KEY_{i}")
    for i in range(1, 7)
    if os.environ.get(f"GEMINI_API_KEY_{i}")
]

GROQ_API_KEY = os.environ.get("GROQ_API_KEY")

POSTED_LOG_FILE = ".posted_articles.json"
DAILY_POST_LIMIT = 5

NICHE_LIMITS = {
    "politics": 1,
    "africa": 1,
    "business": 1,
    "sports": 1,
    "climate": 1,
    "law": 1,
    "curious": 1,
}

RSS_FEEDS = {
    "politics": ["https://news.google.com/rss/search?q=politics&hl=en-US&gl=US&ceid=US:en"],
    "africa": ["https://news.google.com/rss/search?q=africa+news&hl=en-US&gl=US&ceid=US:en"],
    "business": ["https://news.google.com/rss/search?q=business+finance&hl=en-US&gl=US&ceid=US:en"],
    "sports": ["https://news.google.com/rss/search?q=sports+match&hl=en-US&gl=US&ceid=US:en"],
    "climate": ["https://news.google.com/rss/search?q=climate+environment&hl=en-US&gl=US&ceid=US:en"],
    "law": ["https://news.google.com/rss/search?q=court+law+legal&hl=en-US&gl=US&ceid=US:en"],
    "curious": ["https://news.google.com/rss/search?q=curiosity+science+tech&hl=en-US&gl=US&ceid=US:en"],
}

# ─── LOGGING & HELPERS ────────────────────────────────────────────────────────

def load_posted_log():
    if os.path.exists(POSTED_LOG_FILE):
        try:
            with open(POSTED_LOG_FILE, "r", encoding="utf-8") as f:
                return json.load(f)
        except Exception as e:
            print(f"⚠️ Warning loading posted log: {e}")
    return {}

def save_posted_log(posted_log):
    try:
        with open(POSTED_LOG_FILE, "w", encoding="utf-8") as f:
            json.dump(posted_log, f, indent=2)
    except Exception as e:
        print(f"❌ Error saving posted log: {e}")

def count_today_posts(niche):
    posted_log = load_posted_log()
    today = datetime.now(timezone.utc).strftime("%Y-%m-%d")
    count = 0
    for entry in posted_log.values():
        if isinstance(entry, dict):
            entry_date = entry.get("date", "")
            entry_niche = entry.get("niche", "")
            if entry_date.startswith(today) and entry_niche == niche:
                count += 1
        elif isinstance(entry, str):
            if entry.startswith(today):
                count += 1
    return count

def slugify(text):
    text = text.lower()
    text = re.sub(r'[^a-z0-9\s-]', '', text)
    text = re.sub(r'[\s-]+', '-', text).strip('-')
    return text[:60]

def clean_json_response(raw_text):
    """Strips Markdown code blocks ```json ... ``` and returns a clean JSON string."""
    if not raw_text:
        return ""
    cleaned = raw_text.strip()
    if cleaned.startswith("```"):
        cleaned = re.sub(r"^```[a-zA-Z]*\n?", "", cleaned)
        cleaned = re.sub(r"\n?```$", "", cleaned)
    return cleaned.strip()

# ─── RSS FETCHING & VIRALITY SCORING ──────────────────────────────────────────

def fetch_rss_articles(niche, posted_ids):
    articles = []
    urls = RSS_FEEDS.get(niche, [])
    for url in urls:
        try:
            feed = feedparser.parse(url)
            for entry in feed.entries:
                article_id = entry.get("id", entry.get("link", entry.get("title")))
                if article_id in posted_ids:
                    continue
                articles.append({
                    "id": article_id,
                    "title": entry.get("title", "Untitled"),
                    "link": entry.get("link", ""),
                    "summary": entry.get("summary", entry.get("description", "")),
                    "published": entry.get("published", ""),
                    "niche": niche
                })
        except Exception as e:
            print(f"⚠️ RSS fetch error for {niche}: {e}")
    return articles

def score_by_virality(articles):
    """Scores articles based on keyword triggers and summary depth."""
    high_impact_keywords = [
        "breaking", "urgent", "crisis", "exclusive", "major", "official",
        "record", "shock", "disaster", "deal", "victory", "historic", "warns"
    ]
    
    scored_articles = []
    for art in articles:
        score = 0
        title_lower = art["title"].lower()
        summary_lower = art["summary"].lower()

        for word in high_impact_keywords:
            if word in title_lower:
                score += 3
            if word in summary_lower:
                score += 1

        if len(art["summary"]) > 100:
            score += 1

        scored_articles.append((score, art))

    scored_articles.sort(key=lambda x: x[0], reverse=True)
    return [art for score, art in scored_articles]

# ─── LLM INTEGRATION (GEMINI + GROQ FALLBACK) ────────────────────────────────

def call_gemini_single_request(prompt, api_key, model_name, max_tokens=3000):
    try:
        url = f"[https://generativelanguage.googleapis.com/v1beta/models/](https://generativelanguage.googleapis.com/v1beta/models/){model_name}:generateContent"
        resp = requests.post(
            url,
            params={"key": api_key},
            json={
                "contents": [{"parts": [{"text": prompt}]}],
                "generationConfig": {"temperature": 0.7, "maxOutputTokens": max_tokens},
            },
            headers={"Content-Type": "application/json"},
            timeout=35,
        )

        if resp.status_code in (429, 503):
            err_type = "Rate limited (429)" if resp.status_code == 429 else "High demand (503)"
            print(f"  ⚠️ {model_name} {err_type} — backing off 3s...")
            time.sleep(3)
            return None, True

        if not resp.ok:
            print(f"  ❌ Gemini error {resp.status_code}: {resp.text[:200]}")
            return None, False

        data = resp.json()
        candidates = data.get("candidates", [])
        if candidates and "content" in candidates[0]:
            parts = candidates[0]["content"].get("parts", [])
            if parts and "text" in parts[0]:
                return parts[0]["text"].strip(), False
        return None, False
    except Exception as e:
        print(f"  ❌ Gemini exception: {e}")
        return None, False

def call_groq(prompt, max_tokens=3000):
    if not GROQ_API_KEY:
        return None
    try:
        print(f"  🤖 Falling back to Groq ({GROQ_MODEL})...")
        url = "[https://api.groq.com/openai/v1/chat/completions](https://api.groq.com/openai/v1/chat/completions)"
        headers = {
            "Authorization": f"Bearer {GROQ_API_KEY}",
            "Content-Type": "application/json"
        }
        payload = {
            "model": GROQ_MODEL,
            "messages": [{"role": "user", "content": prompt}],
            "max_tokens": max_tokens,
            "temperature": 0.7
        }
        resp = requests.post(url, json=payload, headers=headers, timeout=35)
        if resp.ok:
            data = resp.json()
            return data["choices"][0]["message"]["content"].strip()
        else:
            print(f"  ❌ Groq error {resp.status_code}: {resp.text[:200]}")
            return None
    except Exception as e:
        print(f"  ❌ Groq exception: {e}")
        return None

def call_llm(prompt, max_tokens=3000):
    # Try Gemini across available keys and models
    if GEMINI_API_KEYS:
        for model_name in GEMINI_FALLBACK_MODELS:
            for i, key in enumerate(GEMINI_API_KEYS):
                print(f"  🤖 Trying Gemini key {i + 1}/{len(GEMINI_API_KEYS)} ({model_name})...")
                result, is_transient = call_gemini_single_request(prompt, key, model_name, max_tokens)
                if result:
                    return result
                if not is_transient:
                    break

    # Secondary Provider Fallback: Groq
    if GROQ_API_KEY:
        groq_result = call_groq(prompt, max_tokens)
        if groq_result:
            return groq_result

    print(f"  ❌ All LLM options (Gemini & Groq) failed or exhausted.")
    return None

# ─── GENERATION & HUGO POSTING ────────────────────────────────────────────────

def rewrite_article(article):
    prompt = (
        f"You are a professional journalist for an international news outlet. "
        f"Rewrite the following story into a fully original, highly engaging news article. "
        f"Do NOT output markdown title headers `# Title`. Start directly with the body content.\n\n"
        f"Headline: {article['title']}\n"
        f"Summary/Context: {article['summary']}\n"
        f"Source Link: {article['link']}\n\n"
        f"Write a detailed 300 to 500-word news story with clear paragraphs."
    )
    return call_llm(prompt, max_tokens=3000)

def generate_seo(article, body):
    prompt = (
        f"Generate JSON metadata for a news post. Return strictly valid JSON without explanations.\n"
        f"JSON keys required: 'title', 'description', 'keywords'.\n"
        f"'keywords' must be a JSON array of strings.\n\n"
        f"Article Title: {article['title']}\n"
        f"Content Excerpt: {body[:300]}"
    )
    res = call_llm(prompt, max_tokens=500)
    if res:
        cleaned_json = clean_json_response(res)
        try:
            return json.loads(cleaned_json)
        except Exception as e:
            print(f"  ⚠️ JSON parse warning: {e}. Using fallback metadata.")

    return {
        "title": article["title"],
        "description": article["title"],
        "keywords": [article["niche"], "news", "updates"]
    }

def save_hugo_post(article, body, seo):
    niche_dir = f"content/posts/{article['niche']}"
    os.makedirs(niche_dir, exist_ok=True)
    
    slug = slugify(seo.get("title", article["title"]))
    filename = f"{niche_dir}/{slug}.md"
    now = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")

    keywords_list = seo.get("keywords", [article["niche"]])
    if isinstance(keywords_list, str):
        keywords_list = [k.strip() for k in keywords_list.split(",")]

    content = f"""---
title: "{seo.get('title', article['title']).replace('"', '\\"')}"
date: {now}
draft: false
description: "{seo.get('description', article['title']).replace('"', '\\"')}"
categories: ["{article['niche'].capitalize()}"]
tags: {json.dumps(keywords_list)}
author: "Veridus AI"
---

{body}
"""
    with open(filename, "w", encoding="utf-8") as f:
        f.write(content)
    print(f"  💾 Saved Hugo post: {filename}")

# ─── MAIN ENTRY POINT ─────────────────────────────────────────────────────────

def main():
    sports_only = "--sports-only" in sys.argv

    print(f"\n{'=' * 65}")
    print(f"🚀 Veridus Auto News Poster ({GEMINI_PRIMARY_MODEL}){' [SPORTS ONLY]' if sports_only else ''}")
    print(f"   {datetime.now(timezone.utc).strftime('%Y-%m-%d %H:%M UTC')}")
    print(f"{'=' * 65}")

    if not GEMINI_API_KEYS and not GROQ_API_KEY:
        print("❌ No API keys found. Please set GEMINI_API_KEY_1 or GROQ_API_KEY in Secrets.")
        sys.exit(1)

    posted_log = load_posted_log()
    posted_ids = set(posted_log.keys())
    total_saved = 0
    consecutive_api_failures = 0

    all_niches    = ["politics", "africa", "business", "sports", "climate", "law", "curious"]
    active_niches = ["sports"] if sports_only else all_niches

    for niche in active_niches:
        print(f"\n📰 [{niche.upper()}]")
        already_today = count_today_posts(niche)
        remaining_today = max(0, DAILY_POST_LIMIT - already_today)
        if remaining_today == 0:
            print(f"   📊 Daily limit reached ({DAILY_POST_LIMIT}/day) — skipping {niche}")
            continue

        articles = fetch_rss_articles(niche, posted_ids)
        articles = score_by_virality(articles)

        limit = min(NICHE_LIMITS.get(niche, 1), remaining_today)
        saved_count = 0
        attempts = 0
        MAX_ATTEMPTS_PER_NICHE = max(1, limit * 3)

        for article in articles:
            if saved_count >= limit:
                break

            if attempts >= MAX_ATTEMPTS_PER_NICHE:
                print(f"   ⚠️ Reached maximum attempts ({MAX_ATTEMPTS_PER_NICHE}) for {niche}. Skipping remaining.")
                break

            attempts += 1
            print(f"\n  📝 [{attempts}/{MAX_ATTEMPTS_PER_NICHE}] {article['title'][:70]}")
            body = rewrite_article(article)

            if not body:
                consecutive_api_failures += 1
                if consecutive_api_failures >= 2:
                    print("\n❌ Circuit breaker triggered: 2 consecutive global LLM failures. Aborting workflow.")
                    save_posted_log(posted_log)
                    sys.exit(1)
                continue

            consecutive_api_failures = 0
            seo = generate_seo(article, body)
            save_hugo_post(article, body, seo)

            posted_log[article["id"]] = {
                "date": datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
                "niche": niche
            }
            posted_ids.add(article["id"])
            saved_count += 1
            total_saved += 1
            time.sleep(3)

    save_posted_log(posted_log)
    print(f"\n{'=' * 65}")
    print(f"✨ Complete — {total_saved} articles posted.")
    print(f"{'=' * 65}\n")

if __name__ == "__main__":
    main()