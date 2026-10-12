import os, time, requests
from dotenv import load_dotenv
from designer import INSTRUCTIONS

load_dotenv(override=True)
key, model = os.getenv("GEMMA_API_KEY"), os.getenv("GEMMA_MODEL")
prompt = INSTRUCTIONS + "I sell phone accessories and repair phones. I want to track stock, repairs, customers and money I owe suppliers."
tests = [
    ("baseline", {}, ""),
    ("thinkingLevel=minimal", {"thinkingConfig": {"thinkingLevel": "minimal"}}, ""),
    ("thinkingLevel=low", {"thinkingConfig": {"thinkingLevel": "low"}}, ""),
    ("prompt says answer now", {}, "Do not reason or plan. Answer immediately with only the lines requested.\n"),
]
url = f"https://generativelanguage.googleapis.com/v1beta/models/{model}:generateContent"
for name, extra, prefix in tests:
    cfg = {"maxOutputTokens": 6000, "temperature": 0.3, **extra}
    t = time.time()
    try:
        r = requests.post(url, headers={"x-goog-api-key": key},
                          json={"contents": [{"parts": [{"text": prefix + prompt}]}], "generationConfig": cfg}, timeout=120)
    except Exception as e:
        print(f"{name}: failed ({type(e).__name__})", flush=True)
        continue
    secs = round(time.time() - t, 1)
    if r.status_code != 200:
        try:
            msg = r.json().get("error", {}).get("message", "")[:100]
        except Exception:
            msg = ""
        print(f"{name}: HTTP {r.status_code} in {secs}s - {msg}", flush=True)
        continue
    u = r.json().get("usageMetadata", {})
    print(f"{name}: OK in {secs}s | thinking tokens {u.get('thoughtsTokenCount', 0)} | answer tokens {u.get('candidatesTokenCount', 0)}", flush=True)
