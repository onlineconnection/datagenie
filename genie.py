import os, requests
from dotenv import load_dotenv
import designer
import db
from validator import ALLOWED_TYPES, MAX_TABLES, MAX_COLUMNS, _clean_name, _check_name

load_dotenv(override=True)
KEY = os.getenv("GEMMA_API_KEY")
MODELS = [m for m in (os.getenv("GEMMA_MODEL"), os.getenv("GEMMA_MODEL_BACKUP")) if m]
TIMEOUT = 45
MAX_ACTIONS = 10

PROMPT = """You are GenieAI, an assistant inside a database app for small businesses. The user asks you to change the STRUCTURE of their database.
Their current tables:
{schema}
Output ONLY lines in these formats, no other text, no markdown:
SAY: <one short friendly sentence for the user>
ADD_TABLE <table_name>: <field>:<type>, <field>:<type>!, <field>:integer>other_table
DROP_TABLE <table_name>
ADD_COLUMN <table_name>: <field>:<type>
DROP_COLUMN <table_name>: <field>
Types: text, integer, numeric, boolean, date, timestamp. A ! after the type means required. '>other_table' links the field to another table. Never include an id field. Use snake_case names. Only use tables that exist or that you add. If the request is not about changing the structure, output only a SAY line explaining what you can do.
User request: """


def pretty(name):
    return str(name).replace("_", " ").title()


def schema_text(design):
    lines = []
    for t in design["tables"]:
        cols = []
        for c in t["columns"]:
            if c["primary_key"]:
                continue
            piece = f"{c['name']}:{c['type']}"
            if c["required"]:
                piece += "!"
            if c["references"]:
                piece += ">" + c["references"].split(".")[0]
            cols.append(piece)
        lines.append(f"{t['name']}: {', '.join(cols)}")
    return "\n".join(lines)


def ask(design, message):
    prompt = PROMPT.replace("{schema}", schema_text(design)) + message
    for model in MODELS:
        try:
            r = requests.post(
                f"https://generativelanguage.googleapis.com/v1beta/models/{model}:generateContent",
                headers={"x-goog-api-key": KEY},
                json={"contents": [{"parts": [{"text": prompt}]}],
                      "generationConfig": {"maxOutputTokens": 4000, "temperature": 0.2}},
                timeout=TIMEOUT)
            if r.status_code != 200:
                continue
            parts = r.json()["candidates"][0]["content"]["parts"]
            text = "".join(p.get("text", "") for p in parts if not p.get("thought"))
            if text.strip():
                return text
        except Exception as e:
            print("GenieAI attempt failed:", type(e).__name__)
    return None


def parse_actions(text):
    say, actions = "", []
    for line in text.splitlines():
        line = line.strip().lstrip("-*").strip().strip("`").strip()
        up = line.upper()
        if up.startswith("SAY:"):
            say = line[4:].strip()[:300]
        elif up.startswith("ADD_TABLE "):
            found = designer.parse(line[10:].strip())["tables"]
            if found:
                actions.append({"op": "add_table", "table": found[0]["name"], "columns": found[0]["columns"]})
        elif up.startswith("DROP_TABLE "):
            actions.append({"op": "drop_table", "table": line[11:].strip()})
        elif up.startswith("ADD_COLUMN "):
            head, _, spec = line[11:].partition(":")
            m = designer.FIELD.match(spec) if spec.strip() else None
            if m:
                name, ctype, req, ref = m.groups()
                actions.append({"op": "add_column", "table": head.strip(), "column": {
                    "name": name.strip(), "type": (ctype or "text").lower(), "required": False,
                    "references": (ref.strip().lower().replace(" ", "_") + ".id") if ref else None}})
        elif up.startswith("DROP_COLUMN "):
            head, _, spec = line[12:].partition(":")
            actions.append({"op": "drop_column", "table": head.strip(), "column": spec.strip()})
    return say, actions[:MAX_ACTIONS]


def _cols(cols, tables, errors, ctx, existing=()):
    out, seen = [], {"id", *existing}
    for c in cols or []:
        if not isinstance(c, dict):
            continue
        name = _clean_name(c.get("name"))
        if name == "id":
            continue
        if not _check_name(name, f"Field in {ctx}", errors):
            continue
        if name in seen:
            errors.append(f"The field '{name}' already exists in {ctx}.")
            continue
        seen.add(name)
        ctype = str(c.get("type", "text")).lower()
        if ctype not in ALLOWED_TYPES:
            errors.append(f"The field '{name}' has an unsupported type.")
            continue
        ref = c.get("references")
        if ref:
            rt = str(ref).split(".")[0].strip().lower().replace(" ", "_")
            if rt not in tables:
                errors.append(f"The field '{name}' links to '{rt}', which does not exist.")
                ref = None
            else:
                ref, ctype = rt + ".id", "integer"
        else:
            ref = None
        out.append({"name": name, "type": ctype, "required": bool(c.get("required")),
                    "primary_key": False, "references": ref})
    return out


def _count(biz, tname):
    try:
        return db.table_count(biz, tname)
    except Exception:
        return 0


def check(biz, raw):
    if not isinstance(raw, list):
        return [], ["Invalid request."], []
    errors, clean, descs = [], [], []
    tables = {t["name"]: [dict(c) for c in t["columns"]] for t in biz["design"]["tables"]}
    original = set(tables)
    for a in raw[:MAX_ACTIONS]:
        if not isinstance(a, dict):
            continue
        op = a.get("op")
        tname = _clean_name(a.get("table"))
        if op == "add_table":
            if not _check_name(tname, "Table", errors):
                continue
            if tname in tables:
                errors.append(f"The table {pretty(tname)} already exists.")
                continue
            if len(tables) >= MAX_TABLES:
                errors.append(f"You can have at most {MAX_TABLES} tables.")
                continue
            cols = _cols(a.get("columns"), tables, errors, pretty(tname))[:MAX_COLUMNS]
            if not cols:
                errors.append(f"The table {pretty(tname)} needs at least one field.")
                continue
            full = [{"name": "id", "type": "integer", "required": True, "primary_key": True, "references": None}] + cols
            tables[tname] = full
            clean.append({"op": "add_table", "table": tname, "columns": full})
            descs.append(f"Add the table {pretty(tname)} with: {', '.join(pretty(c['name']) for c in cols)}")
        elif op == "drop_table":
            if tname not in tables:
                errors.append(f"There is no table called {pretty(tname)}.")
                continue
            if len(tables) == 1:
                errors.append("You cannot delete your only table.")
                continue
            users = [t for t, cs in tables.items() if t != tname and any(
                c.get("references") and c["references"].split(".")[0] == tname for c in cs)]
            if users:
                errors.append(f"Cannot delete {pretty(tname)} because {', '.join(pretty(u) for u in users)} use it. Delete those first.")
                continue
            count = _count(biz, tname) if tname in original else 0
            del tables[tname]
            clean.append({"op": "drop_table", "table": tname})
            descs.append(f"Delete the table {pretty(tname)} and its {count} record{'' if count == 1 else 's'} permanently")
        elif op == "add_column":
            if tname not in tables:
                errors.append(f"There is no table called {pretty(tname)}.")
                continue
            if len(tables[tname]) >= MAX_COLUMNS:
                errors.append(f"{pretty(tname)} cannot have more fields.")
                continue
            found = _cols([a.get("column")], tables, errors, pretty(tname), [c["name"] for c in tables[tname]])
            if not found:
                continue
            col = found[0]
            col["required"] = False
            tables[tname].append(col)
            clean.append({"op": "add_column", "table": tname, "column": col})
            descs.append(f"Add the field {pretty(col['name'])} to {pretty(tname)}")
        elif op == "drop_column":
            cname = _clean_name(a.get("column"))
            names = [c["name"] for c in tables.get(tname, [])]
            if tname not in tables:
                errors.append(f"There is no table called {pretty(tname)}.")
                continue
            if cname == "id":
                errors.append("The id field cannot be deleted.")
                continue
            if cname not in names:
                errors.append(f"{pretty(tname)} has no field called {pretty(cname)}.")
                continue
            if len(names) <= 2:
                errors.append(f"{pretty(tname)} must keep at least one field.")
                continue
            tables[tname] = [c for c in tables[tname] if c["name"] != cname]
            clean.append({"op": "drop_column", "table": tname, "column": cname})
            descs.append(f"Delete the field {pretty(cname)} from {pretty(tname)}. Its data is lost")
    return clean, errors, descs


def plan(biz, message):
    text = ask(biz["design"], message)
    if text is None:
        return None
    say, raw = parse_actions(text)
    actions, errors, descs = check(biz, raw)
    if not say and not actions and not errors:
        say = "I could not turn that into a change. Try: Add a table to track payments."
    return {"reply": say, "actions": actions, "descriptions": descs, "errors": errors}
