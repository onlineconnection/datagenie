import os, json, time, requests
from dotenv import load_dotenv
load_dotenv(override=True)
m = os.getenv("GEMMA_MODEL")
t = time.time()
r = requests.post(
    f"https://generativelanguage.googleapis.com/v1beta/models/{m}:generateContent",
    headers={"x-goog-api-key": os.getenv("GEMMA_API_KEY")},
    json={"contents": [{"parts": [{"text": "Return JSON only: a database with 3 tables for a small farm. Format: {\"tables\":[{\"name\":\"x\",\"columns\":[{\"name\":\"y\",\"type\":\"text\"}]}]}"}]}],
          "generationConfig": {"maxOutputTokens": 8000, "temperature": 0.2}},
    timeout=150)
print("secs:", round(time.time() - t, 1), "status:", r.status_code)
d = r.json()
print("usage:", d.get("usageMetadata"))
c = d["candidates"][0]
print("finish:", c.get("finishReason"))
for i, p in enumerate(c["content"]["parts"]):
    print(f"--- part {i} thought={p.get('thought')} len={len(p.get('text',''))}")
    print(p.get("text", "")[:300])
