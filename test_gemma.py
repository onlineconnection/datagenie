import os, json, time, requests
from dotenv import load_dotenv

load_dotenv(override=True)
key = os.getenv("GEMMA_API_KEY")
models = [os.getenv("GEMMA_MODEL"), os.getenv("GEMMA_MODEL_BACKUP")]

prompt = """You are a database designer. Return ONLY valid JSON, no markdown.
Design a compact database for this business:
"I run a supermarket. I want to manage products, suppliers, inventory, sales, customers, expenses, and daily profit."

Rules: max 8 tables, max 7 columns per table, no descriptions, every table has an integer id primary key.

Format:
{"tables":[{"name":"snake_case","columns":[{"name":"snake_case","type":"text|integer|numeric|boolean|date|timestamp","required":true,"primary_key":false,"references":"table.column or null"}]}]}
"""

def extract_json(parts):
    text = "".join(p.get("text", "") for p in parts if not p.get("thought"))
    a, b = text.find("{"), text.rfind("}")
    return json.loads(text[a:b + 1])

def call(model, thinking_off):
    config = {"maxOutputTokens": 16000, "temperature": 0.2}
    if thinking_off:
        config["thinkingConfig"] = {"thinkingBudget": 0}
    url = f"https://generativelanguage.googleapis.com/v1beta/models/{model}:generateContent"
    return requests.post(
        url,
        headers={"x-goog-api-key": key},
        json={"contents": [{"parts": [{"text": prompt}]}], "generationConfig": config},
        timeout=120,
    )

for model in models:
    if not model:
        continue
    for thinking_off in (True, False):
        start = time.time()
        print(f"Trying {model} (thinking_off={thinking_off})...")
        try:
            r = call(model, thinking_off)
        except requests.RequestException as e:
            print("  Request failed:", type(e).__name__)
            break
        print(f"  Status {r.status_code} in {time.time() - start:.1f}s")
        if r.status_code != 200:
            print("  ", r.text[:300])
            continue
        d = r.json()
        c = d["candidates"][0]
        print("  finish:", c.get("finishReason"),
              "| thought tokens:", d.get("usageMetadata", {}).get("thoughtsTokenCount", 0))
        try:
            schema = extract_json(c["content"]["parts"])
            names = [t["name"] for t in schema["tables"]]
        except Exception as e:
            print("  Bad response:", type(e).__name__)
            continue
        with open("sample_schema.json", "w") as f:
            json.dump(schema, f, indent=2)
        print(f"SUCCESS with {model}: {len(names)} tables: {names}")
        raise SystemExit

print("All models failed.")
