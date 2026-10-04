# ─── CONFIG & MODEL ───────────────────────────────────────────────────────────

GEMINI_MODEL = "gemini-3.6-flash"

# Fallback models if primary hits temporary 503 high demand or key quotas
FALLBACK_MODELS = ["gemini-3.6-flash", "gemini-3.8-flash", "gemini-2.0-flash", "gemini-1.5-flash"]

# ─── GEMINI API CALLS ─────────────────────────────────────────────────────────

def call_gemini_single_request(prompt, api_key, model_name, max_tokens=3000):
    try:
        url = f"https://generativelanguage.googleapis.com/v1beta/models/{model_name}:generateContent"
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
        
        # 429 (Rate Limit) & 503 (High Demand) are transient errors
        if resp.status_code in (429, 503):
            err_type = "Rate limited (429)" if resp.status_code == 429 else "High demand (503)"
            print(f"  ⚠️ {model_name} {err_type} — backing off for 3s...")
            time.sleep(3)
            return None, True  # True indicates transient/retryable error

        if not resp.ok:
            print(f"  ❌ Gemini error {resp.status_code}: {resp.text[:250]}")
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

def call_gemini(prompt, max_tokens=3000):
    if not GEMINI_API_KEYS:
        return None

    for model_name in FALLBACK_MODELS:
        for i, key in enumerate(GEMINI_API_KEYS):
            print(f"  🤖 Trying key {i + 1}/{len(GEMINI_API_KEYS)} ({model_name})...")
            result, is_transient = call_gemini_single_request(prompt, key, model_name, max_tokens)
            if result:
                return result
            if not is_transient:
                # Break key loop on structural non-retryable errors
                break

    print(f"  ❌ All Gemini keys/models failed or exhausted.")
    return None