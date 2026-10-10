import os, json, time, glob, requests
from dotenv import load_dotenv

load_dotenv(override=True)
KEY = os.getenv("GEMMA_API_KEY")
MODELS = [m for m in (os.getenv("GEMMA_MODEL"), os.getenv("GEMMA_MODEL_BACKUP")) if m]

def load_blueprints():
    return {os.path.basename(p)[:-5]: json.load(open(p)) for p in glob.glob("blueprints/*.json")}

def keyword_match(text, bps):
    text = text.lower()
    scores = {k: sum(w in text for w in b["keywords"]) for k, b in bps.items()}
    best = max(scores, key=scores.get)
    return best if scores[best] > 0 else "supermarket"

def ask_gemma(description, bps):
    shape = {k: [t["name"] for t in b["tables"]] for k, b in bps.items()}
    prompt = (
        "Pick the best blueprint for this business and list changes. Reply with ONLY JSON, no explanation:\n"
        '{"blueprint":"name","add_tables":["snake_case"],"remove_tables":["name"]}\n'
        f"Blueprints and their tables: {json.dumps(shape)}\n"
        f"Business: {description}"
    )
    for model in MODELS:
        try:
            r = requests.post(
                f"https://generativelanguage.googleapis.com/v1beta/models/{model}:generateContent",
                headers={"x-goog-api-key": KEY},
                json={"contents": [{"parts": [{"text": prompt}]}],
                      "generationConfig": {"maxOutputTokens": 4000, "temperature": 0.1}},
                timeout=25)
            if r.status_code != 200:
                continue
            parts = r.json()["candidates"][0]["content"]["parts"]
            text = "".join(p.get("text", "") for p in parts if not p.get("thought"))
            return json.loads(text[text.find("{"):text.rfind("}") + 1])
        except Exception:
            continue
    return None

def design(description):
    bps = load_blueprints()
    start = time.time()
    plan = ask_gemma(description, bps)
    source = "gemma"
    if not plan or plan.get("blueprint") not in bps:
        plan = {"blueprint": keyword_match(description, bps), "add_tables": [], "remove_tables": []}
        source = "fallback"
    schema = json.loads(json.dumps(bps[plan["blueprint"]]))
    remove = set(plan.get("remove_tables") or [])
    schema["tables"] = [t for t in schema["tables"] if t["name"] not in remove]
    for name in plan.get("add_tables") or []:
        schema["tables"].append({"name": name, "columns": [
            {"name": "id", "type": "integer", "required": True, "primary_key": True, "references": None},
            {"name": "name", "type": "text", "required": True, "primary_key": False, "references": None}]})
    return schema, source, round(time.time() - start, 1)

if __name__ == "__main__":
    import sys
    text = " ".join(sys.argv[1:]) or "I grow cassava and rice and want to track expenses, sales and profit"
    schema, source, secs = design(text)
    print(f"{source} in {secs}s -> {schema['name']}: {[t['name'] for t in schema['tables']]}")
