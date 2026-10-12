import os, re, time, requests
from dotenv import load_dotenv
import adapt  # only used for the emergency fallback

load_dotenv(override=True)
KEY = os.getenv("GEMMA_API_KEY")
MODELS = [m for m in (os.getenv("GEMMA_MODEL"), os.getenv("GEMMA_MODEL_BACKUP")) if m]
TIMEOUT = 45

INSTRUCTIONS = """You design simple databases for small businesses. Design tables that fit EXACTLY what this person says they want to manage.
Rules: 4 to 8 tables, 3 to 8 fields per table. Do NOT include an id field (it is automatic). Use snake_case names.
Output ONLY these lines, no other text, no markdown:
title: <2 or 3 word name for this business>
<table_name>: <field>:<type>, <field>:<type>!, <field>:integer>other_table
Types: text, integer, numeric, boolean, date, timestamp. A ! after the type means required. '>other_table' links the field to that table.
Example lines:
suppliers: name:text!, phone:text
products: name:text!, price:numeric!, supplier_id:integer>suppliers
The business: """

LINE = re.compile(r"^\s*(?:[-*\d.)]+\s+)?`?([A-Za-z_][A-Za-z0-9_ ]*?)`?\s*:\s*(.+)$")
FIELD = re.compile(r"^\s*`?([A-Za-z_][A-Za-z0-9_ ]*?)`?\s*(?::\s*([A-Za-z]+))?\s*(!)?\s*(?:>\s*([A-Za-z_][A-Za-z0-9_ ]*))?\s*$")


def parse(text):
    title, tables = "Business", []
    for line in text.splitlines():
        m = LINE.match(line)
        if not m:
            continue
        name, rest = m.group(1).strip(), m.group(2).strip()
        if name.lower() == "title":
            title = rest.strip("` *")[:40] or title
            continue
        cols = []
        for f in rest.split(","):
            fm = FIELD.match(f)
            if not fm:
                continue
            cname, ctype, req, ref = fm.groups()
            cols.append({"name": cname.strip(), "type": (ctype or "text").lower(),
                         "required": bool(req), "primary_key": False,
                         "references": (ref.strip().lower().replace(" ", "_") + ".id") if ref else None})
        if cols:
            tables.append({"name": name, "columns": [
                {"name": "id", "type": "integer", "required": True, "primary_key": True, "references": None}] + cols})
    return {"name": title, "tables": tables}


def ask(model, description):
    r = requests.post(
        f"https://generativelanguage.googleapis.com/v1beta/models/{model}:generateContent",
        headers={"x-goog-api-key": KEY},
        json={"contents": [{"parts": [{"text": INSTRUCTIONS + description}]}],
              "generationConfig": gen_config(6000, 0.3)},
        timeout=TIMEOUT)
    if r.status_code != 200:
        return None
    parts = r.json()["candidates"][0]["content"]["parts"]
    return "".join(p.get("text", "") for p in parts if not p.get("thought"))


def design(description):
    start = time.time()
    for model in MODELS:
        try:
            text = ask(model, description)
            schema = parse(text) if text else None
            if schema and len(schema["tables"]) >= 2:
                return schema, "gemma", round(time.time() - start, 1)
        except Exception as e:
            print("Gemma attempt failed:", type(e).__name__)
    if os.getenv("DESIGN_FALLBACK") == "1":
        bps = adapt.load_blueprints()
        return bps[adapt.keyword_match(description, bps)], "fallback", round(time.time() - start, 1)
    return None, "failed", round(time.time() - start, 1)


def gen_config(tokens, temp):
    cfg = {"maxOutputTokens": tokens, "temperature": temp}
    level = os.getenv("GEMMA_THINKING")
    if level:
        cfg["thinkingConfig"] = {"thinkingLevel": level}
    return cfg
