import os, re, requests
from decimal import InvalidOperation
from dotenv import load_dotenv
import designer
import db
from validator import ALLOWED_TYPES, MAX_TABLES, MAX_COLUMNS, _clean_name, _check_name

load_dotenv(override=True)
KEY = os.getenv("GEMMA_API_KEY")
MODEL = os.getenv("GEMMA_MODEL")
TIMEOUT = 40
MAX_ACTIONS = 25
MAX_ROWS = 20
MAX_MATCHES = 20

HEAD = """You are GenieAI, an assistant inside a database app for small businesses. The user asks you to change their database.
Their current tables (field:type, ! means required, >table means linked to that table):
{schema}
Output ONLY lines in these formats, no other text, no markdown. Use only the formats listed.
SAY: <one short friendly sentence>
"""

LINES = {
    "table": "ADD_TABLE <table>: <field>:<type>, <field>:<type>!, <field>:integer>other_table\nDROP_TABLE <table>\nRENAME_TABLE <table> > <new_name>\n",
    "column": "ADD_COLUMN <table>: <field>:<type>\nDROP_COLUMN <table>: <field>\nRENAME_COLUMN <table>.<field> > <new_name>\n",
    "row": "ADD_ROW <table>: <field>=<value> | <field>=<value>\n",
    "edit": "UPDATE_ROW <table> WHERE <field>=<value>: <field>=<value> | <field>=<value>\n",
    "relation": "ADD_RELATION <table>.<field> > <other_table>\n",
}

RULES = {
    "base": "Types: text, integer, numeric, boolean, date, timestamp. Never include an id field. Use snake_case names. Only use tables that exist or that you add.\n",
    "row": "For ADD_ROW write one line per record with exact field names, fields separated by |, dates as YYYY-MM-DD or today, booleans as yes or no, numbers without currency symbols. For a linked field write the NAME of the record in the other table, and add that record first if it does not exist. Fill every required field.\n",
    "edit": "UPDATE_ROW changes an existing record. WHERE picks the record by a field value such as its name. If the user does not say which record, leave out the WHERE part and write: UPDATE_ROW <table>: <field>=<value>\n",
    "relation": "ADD_RELATION links a field of one table to another table, and creates the field if it does not exist.\n",
}

SPLIT = re.compile(r"\s*(?:\||,(?=\s*[A-Za-z_][A-Za-z0-9_ ]*\s*=))\s*")
REL = re.compile(r"^([A-Za-z_][A-Za-z0-9_ ]*?)\s*\.\s*([A-Za-z_][A-Za-z0-9_ ]*?)\s*(?:->|>)\s*([A-Za-z_][A-Za-z0-9_ ]*)$")
RENAME_T = re.compile(r"^(.+?)\s*(?:->|>)\s*(.+)$")
UPD_HEAD = re.compile(r"^(.*?)(?:\s+WHERE\s+(.+?)\s*=\s*(.+))?$", re.I)


def pretty(name):
    return str(name).replace("_", " ").title()


def norm(s):
    return re.sub(r"[^a-z0-9]+", "_", str(s).lower()).strip("_")


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


def sections(message):
    m = message.lower()
    s = set()
    if re.search(r"\b(rename|change|update|edit|set|correct|fix|replace|modify)\b", m):
        s.add("edit")
    if re.search(r"\brename\b", m):
        s.update(["table", "column"])
    if re.search(r"\b(add|insert|record|records|sample|enter|new|log|register|row|rows)\b", m):
        s.add("row")
    if re.search(r"\b(link|relation|relationship|connect|join|reference|linked)\b", m):
        s.add("relation")
    if re.search(r"\b(table|tables|create|new|delete|drop|remove|make)\b", m):
        s.add("table")
    if re.search(r"\b(field|fields|column|columns|delete|remove)\b", m):
        s.add("column")
    return s or set(LINES)


def build_prompt(design, message):
    s = sections(message)
    out = HEAD.replace("{schema}", schema_text(design))
    out += "".join(LINES[k] for k in LINES if k in s)
    out += RULES["base"] + "".join(RULES[k] for k in ("row", "edit", "relation") if k in s)
    return out + "If the request is not about changing the database, output only a SAY line explaining what you can do.\nUser request: " + message


def ask(design, message):
    if not MODEL:
        return None
    try:
        r = requests.post(
            f"https://generativelanguage.googleapis.com/v1beta/models/{MODEL}:generateContent",
            headers={"x-goog-api-key": KEY},
            json={"contents": [{"parts": [{"text": build_prompt(design, message)}]}],
                  "generationConfig": designer.gen_config(5000, 0.2)},
            timeout=TIMEOUT)
        if r.status_code != 200:
            return None
        parts = r.json()["candidates"][0]["content"]["parts"]
        text = "".join(p.get("text", "") for p in parts if not p.get("thought"))
        return text if text.strip() else None
    except Exception as e:
        print("GenieAI attempt failed:", type(e).__name__)
        return None


# ---------- instant path: no Gemma needed ----------
def find_table(text, names):
    n = norm(text)
    n = re.sub(r"^(the|a|an|my)_", "", n)
    n = re.sub(r"_(table|section|list)$", "", n)
    for cand in (n, n + "s", n.rstrip("s"), n + "es"):
        if cand in names:
            return cand
    return None


def guess_type(name):
    n = name.lower()
    if re.search(r"(price|amount|cost|salary|total|balance|fee|wage|rate|weight|percent)", n):
        return "numeric"
    if re.search(r"(quantity|count|age|year|days|minutes|stock)", n):
        return "integer"
    if n == "date" or n.endswith("_date") or "date" in n.split("_"):
        return "date"
    return "text"


def singular(name):
    if name.endswith("ies"):
        return name[:-3] + "y"
    if name.endswith("s") and not name.endswith("ss"):
        return name[:-1]
    return name


def local_actions(message, names):
    low = message.strip().rstrip(".!?").strip().lower()
    low = re.sub(r"^(please|can you|could you|kindly)\s+", "", low)

    r = re.match(r"^(?:delete|drop|remove)\s+(?:the\s+)?(.+?)\s+table$", low)
    if r and find_table(r.group(1), names):
        return [{"op": "drop_table", "table": find_table(r.group(1), names)}]

    r = re.match(r"^(?:delete|drop|remove)\s+(?:the\s+)?(.+?)\s+(?:field|column)\s+(?:from|in|of)\s+(?:the\s+)?(.+?)(?:\s+table)?$", low)
    if r and find_table(r.group(2), names):
        return [{"op": "drop_column", "table": find_table(r.group(2), names), "column": norm(r.group(1))}]

    r = re.match(r"^(?:add|create)\s+(?:an?\s+|the\s+)?(.+?)\s+(?:field|column)\s+(?:to|in|on)\s+(?:the\s+)?(.+?)(?:\s+table)?$", low)
    if r and find_table(r.group(2), names):
        n = norm(r.group(1))
        return [{"op": "add_column", "table": find_table(r.group(2), names),
                 "column": {"name": n, "type": guess_type(n), "required": False, "references": None}}]

    r = re.match(r"^rename\s+(?:the\s+)?(.+?)\s+table\s+(?:to|as)\s+(.+)$", low)
    if r and find_table(r.group(1), names):
        return [{"op": "rename_table", "table": find_table(r.group(1), names), "new_name": norm(r.group(2))}]

    r = re.match(r"^rename\s+(?:the\s+)?(.+?)\s+(?:field|column)\s+(?:in|of|on)\s+(?:the\s+)?(.+?)(?:\s+table)?\s+(?:to|as)\s+(.+)$", low)
    if r and find_table(r.group(2), names):
        return [{"op": "rename_column", "table": find_table(r.group(2), names),
                 "column": norm(r.group(1)), "new_name": norm(r.group(3))}]

    r = re.match(r"^(?:link|connect)\s+(?:the\s+)?(.+?)(?:\s+table)?\s+(?:to|with)\s+(?:the\s+)?(.+?)(?:\s+table)?$", low)
    if r and find_table(r.group(1), names) and find_table(r.group(2), names):
        parent = find_table(r.group(2), names)
        return [{"op": "add_relation", "table": find_table(r.group(1), names),
                 "column": singular(parent) + "_id", "references": parent}]
    return None


# ---------- parse Gemma output ----------
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
        elif up.startswith("RENAME_TABLE "):
            m = RENAME_T.match(line[13:].strip())
            if m:
                actions.append({"op": "rename_table", "table": m.group(1).strip(), "new_name": m.group(2).strip()})
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
        elif up.startswith("RENAME_COLUMN "):
            m = REL.match(line[14:].strip())
            if m:
                actions.append({"op": "rename_column", "table": m.group(1), "column": m.group(2), "new_name": m.group(3)})
        elif up.startswith("ADD_ROW "):
            head, _, spec = line[8:].partition(":")
            values = {}
            for piece in SPLIT.split(spec.strip()):
                k, eq, v = piece.partition("=")
                if eq and k.strip():
                    values[k.strip().strip("`")] = v.strip().strip("\"'`")
            if values:
                actions.append({"op": "add_row", "table": head.strip().strip("`"), "values": values})
        elif up.startswith("UPDATE_ROW "):
            head, _, spec = line[11:].partition(":")
            hm = UPD_HEAD.match(head.strip())
            values = {}
            for piece in SPLIT.split(spec.strip()):
                k, eq, v = piece.partition("=")
                if eq and k.strip():
                    values[k.strip().strip("`")] = v.strip().strip("\"'`")
            if hm and values:
                act = {"op": "update_row", "table": hm.group(1).strip().strip("`"), "values": values}
                if hm.group(2):
                    act["where"] = {"column": hm.group(2).strip().strip("`"), "value": hm.group(3).strip().strip("\"'`")}
                actions.append(act)
        elif up.startswith("ADD_RELATION "):
            m = REL.match(line[13:].strip())
            if m:
                actions.append({"op": "add_relation", "table": m.group(1), "column": m.group(2), "references": m.group(3)})
    return say, actions[:MAX_ACTIONS]


# ---------- validation (always re-run before anything is applied) ----------
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


def _link_ok(biz, parent, raw, pending):
    if raw.lower() in pending.get(parent, set()):
        return True
    existing = db.get_table(biz, parent)
    if not existing:
        return False
    try:
        with db.connect() as c:
            return db.resolve_link(c, biz["pg_schema"], existing, raw) is not None
    except Exception:
        return False


def _values(biz, tname, tables, given, pending, errors, partial):
    cols = {c["name"]: c for c in tables[tname]}
    values, bad = {}, False
    for k, v in (given or {}).items():
        cname = _clean_name(k)
        col = cols.get(cname)
        if not col or col["primary_key"]:
            errors.append(f"{pretty(tname)} has no field called {pretty(k)}.")
            bad = True
            continue
        v = "" if v is None else str(v).strip()
        if v == "":
            continue
        if col["references"]:
            parent = col["references"].split(".")[0]
            if not _link_ok(biz, parent, v, pending):
                errors.append(f"There is no {pretty(parent)} record called '{v}'. Add it first.")
                bad = True
                continue
        else:
            try:
                db.convert_value(col["type"], v)
            except (ValueError, InvalidOperation, ArithmeticError):
                errors.append(f"'{v}' is not a valid value for {pretty(cname)} in {pretty(tname)}.")
                bad = True
                continue
        values[cname] = v
    if not partial:
        missing = [c["name"] for c in tables[tname]
                   if c["required"] and not c["primary_key"] and c["name"] not in values]
        if missing:
            errors.append(f"{pretty(tname)} needs a value for: {', '.join(pretty(m) for m in missing)}.")
            bad = True
    return None if bad else values


def check(biz, raw):
    if not isinstance(raw, list):
        return [], ["Invalid request."], []
    errors, clean, descs = [], [], []
    tables = {t["name"]: [dict(c) for c in t["columns"]] for t in biz["design"]["tables"]}
    original = set(tables)
    pending, rows_added = {}, 0
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
            pending.pop(tname, None)
            clean.append({"op": "drop_table", "table": tname})
            descs.append(f"Delete the table {pretty(tname)} and its {count} record{'' if count == 1 else 's'} permanently")
        elif op == "rename_table":
            new = _clean_name(a.get("new_name"))
            if tname not in tables:
                errors.append(f"There is no table called {pretty(tname)}.")
                continue
            if not _check_name(new, "Table", errors):
                continue
            if new in tables:
                errors.append(f"The table {pretty(new)} already exists.")
                continue
            tables[new] = tables.pop(tname)
            for cs in tables.values():
                for c in cs:
                    if c.get("references") and c["references"].split(".")[0] == tname:
                        c["references"] = new + ".id"
            original.discard(tname)
            clean.append({"op": "rename_table", "table": tname, "new_name": new})
            descs.append(f"Rename the table {pretty(tname)} to {pretty(new)}")
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
        elif op == "rename_column":
            cname = _clean_name(a.get("column"))
            new = _clean_name(a.get("new_name"))
            if tname not in tables:
                errors.append(f"There is no table called {pretty(tname)}.")
                continue
            names = [c["name"] for c in tables[tname]]
            if cname == "id" or cname not in names:
                errors.append(f"{pretty(tname)} has no field called {pretty(cname)} that can be renamed.")
                continue
            if not _check_name(new, f"Field in {pretty(tname)}", errors):
                continue
            if new in names:
                errors.append(f"{pretty(tname)} already has a field called {pretty(new)}.")
                continue
            for c in tables[tname]:
                if c["name"] == cname:
                    c["name"] = new
            clean.append({"op": "rename_column", "table": tname, "column": cname, "new_name": new})
            descs.append(f"Rename the field {pretty(cname)} in {pretty(tname)} to {pretty(new)}")
        elif op == "add_row":
            if tname not in tables:
                errors.append(f"There is no table called {pretty(tname)}.")
                continue
            if rows_added >= MAX_ROWS:
                if "Too many records" not in " ".join(errors):
                    errors.append(f"Too many records at once. I can add up to {MAX_ROWS} at a time.")
                continue
            values = _values(biz, tname, tables, a.get("values") if isinstance(a.get("values"), dict) else {},
                             pending, errors, False)
            if not values:
                continue
            label = db.label_col({"columns": tables[tname]})
            if label in values:
                pending.setdefault(tname, set()).add(values[label].lower())
            rows_added += 1
            clean.append({"op": "add_row", "table": tname, "values": values})
            descs.append(f"Add a record to {pretty(tname)}: " + ", ".join(f"{pretty(k)} = {v}" for k, v in values.items()))
        elif op == "update_row":
            if tname not in tables or tname not in original:
                errors.append(f"There is no existing table called {pretty(tname)}.")
                continue
            values = _values(biz, tname, tables, a.get("values") if isinstance(a.get("values"), dict) else {},
                             pending, errors, True)
            if values is None:
                continue
            if not values:
                errors.append("There is nothing to change.")
                continue
            w = a.get("where") if isinstance(a.get("where"), dict) else None
            try:
                if w and w.get("column") and str(w.get("value") or "").strip():
                    wcol = _clean_name(w["column"])
                    if wcol not in {c["name"] for c in tables[tname]}:
                        errors.append(f"{pretty(tname)} has no field called {pretty(w['column'])}.")
                        continue
                    ids = db.find_rows(biz, tname, wcol, str(w["value"]).strip())
                    where_out = {"column": wcol, "value": str(w["value"]).strip()}
                else:
                    ids = db.find_rows(biz, tname, None, None)
                    if len(ids) != 1:
                        errors.append(f"Which record in {pretty(tname)} do you mean? Tell me its name, for example: change {pretty(tname)} Apple to Sarah.")
                        continue
                    where_out = {"column": "id", "value": str(ids[0])}
            except Exception:
                errors.append("I could not look up that record.")
                continue
            if not ids:
                errors.append(f"I could not find that record in {pretty(tname)}.")
                continue
            if len(ids) > MAX_MATCHES:
                errors.append(f"That matches too many records in {pretty(tname)}. Be more specific.")
                continue
            clean.append({"op": "update_row", "table": tname, "where": where_out, "values": values, "ids": ids})
            who = "the record" if where_out["column"] == "id" else f"{len(ids)} record{'' if len(ids) == 1 else 's'} where {pretty(where_out['column'])} = {where_out['value']}"
            descs.append(f"Change {who} in {pretty(tname)}: " + ", ".join(f"{pretty(k)} = {v}" for k, v in values.items()))
        elif op == "add_relation":
            cname = _clean_name(a.get("column"))
            parent = _clean_name(a.get("references"))
            if tname not in tables:
                errors.append(f"There is no table called {pretty(tname)}.")
                continue
            if parent not in tables:
                errors.append(f"There is no table called {pretty(parent)}.")
                continue
            if tname == parent:
                errors.append("A table cannot be linked to itself.")
                continue
            if not _check_name(cname, f"Field in {pretty(tname)}", errors):
                continue
            existing = next((c for c in tables[tname] if c["name"] == cname), None)
            if existing:
                if existing["primary_key"] or existing["type"] != "integer":
                    errors.append(f"{pretty(cname)} in {pretty(tname)} must be a whole-number field to be linked.")
                    continue
                if existing["references"]:
                    errors.append(f"{pretty(cname)} in {pretty(tname)} is already linked to another table.")
                    continue
                existing["references"] = parent + ".id"
                descs.append(f"Link {pretty(tname)} to {pretty(parent)} using the existing field {pretty(cname)}")
            else:
                if len(tables[tname]) >= MAX_COLUMNS:
                    errors.append(f"{pretty(tname)} cannot have more fields.")
                    continue
                tables[tname].append({"name": cname, "type": "integer", "required": False,
                                      "primary_key": False, "references": parent + ".id"})
                descs.append(f"Create the field {pretty(cname)} in {pretty(tname)}, linked to {pretty(parent)}")
            clean.append({"op": "add_relation", "table": tname, "column": cname, "references": parent})
    return clean, errors, descs


def plan(biz, message):
    names = {t["name"] for t in biz["design"]["tables"]}
    fast = local_actions(message, names)
    if fast:
        actions, errors, descs = check(biz, fast)
        return {"reply": "Got it. Please review the change below.", "actions": actions,
                "descriptions": descs, "errors": errors, "fast": True}
    text = ask(biz["design"], message)
    if text is None:
        return None
    say, raw = parse_actions(text)
    actions, errors, descs = check(biz, raw)
    if not say and not actions and not errors:
        say = "I could not turn that into a change. Try: Add 3 sample records to a table."
    return {"reply": say, "actions": actions, "descriptions": descs, "errors": errors, "fast": False}
