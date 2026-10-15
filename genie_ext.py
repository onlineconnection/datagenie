import re, threading
from datetime import date, datetime
from decimal import Decimal, InvalidOperation
import genie
import db
from validator import _clean_name, ALLOWED_TYPES

P = genie.pretty
EXT_OPS = {"delete_row", "change_type", "set_required", "drop_relation", "adjust"}
MAX_DELETE = 200
MAX_ADJUST = 5000
NAME = r"[A-Za-z_][A-Za-z0-9_ ]*?"
OK_CONV = {("integer", "numeric"), ("numeric", "integer"), ("date", "timestamp"), ("timestamp", "date")}
BOOLS = {"true", "false", "t", "f", "yes", "no", "y", "n", "1", "0", "on", "off"}
TYPE_ALIAS = {"number": "numeric", "amount": "numeric", "decimal": "numeric", "money": "numeric",
              "whole_number": "integer", "int": "integer", "string": "text", "yes/no": "boolean",
              "datetime": "timestamp", "date_and_time": "timestamp"}

LINES = {
    "delete_row": "DELETE_ROW <table> WHERE <field>=<value>\nDELETE_ROW <table> ALL\nDELETE_ROW removes records (ALL removes every record, use it only when the user clearly asks). DROP_TABLE removes a whole table.\n",
    "change_type": "CHANGE_TYPE <table>.<field> > <type>\n",
    "set_required": "SET_REQUIRED <table>.<field> > yes|no\n",
    "drop_relation": "DROP_RELATION <table>.<field>\nDROP_RELATION removes the link to another table but keeps the field.\n",
    "adjust": "ADJUST <table>.<field> <+|-|*|/> <number> [WHERE <field>=<value>]\nADJUST changes numbers in many records at once. A 10 percent increase is * 1.1 and a 20 percent discount is * 0.8.\n",
}
TRIGGERS = {
    "delete_row": r"\b(delete|remove|clear|erase|empty|wipe|get rid)\b",
    "change_type": r"\b(type|convert)\b",
    "set_required": r"\b(required|optional|mandatory|compulsory|must)\b",
    "drop_relation": r"\b(unlink|disconnect|detach|relationship|break the link|remove the link)\b",
    "adjust": r"(\b(increase|decrease|raise|reduce|discount|multiply|divide|markup|mark up|percent|double|triple|halve|subtract)\b|%)",
}

_local = threading.local()


def set_history(items):
    out = []
    if isinstance(items, list):
        for x in items[-3:]:
            if isinstance(x, str) and x.strip():
                out.append(x.strip()[:200])
    _local.history = out


def _build_prompt(design, message):
    out = _orig_prompt(design, message)
    low = message.lower()
    add = "".join(LINES[k] for k, rx in TRIGGERS.items() if re.search(rx, low))
    marker = "If the request is not about changing the database"
    i = out.rfind(marker)
    if add and i != -1:
        out = out[:i] + add + out[i:]
    hist = getattr(_local, "history", [])
    j = out.rfind("User request: ")
    if hist and j != -1:
        out = out[:j] + "Earlier requests from this user, for context only, do not repeat them: " + " | ".join(hist) + "\n" + out[j:]
    return out


def parse_extra(text):
    out = []
    for line in text.splitlines():
        line = line.strip().lstrip("-*").strip().strip("`").strip()
        up = line.upper()
        if up.startswith("DELETE_ROW "):
            rest = line[11:].strip()
            m = re.match(r"^(.+?)\s+ALL$", rest, re.I)
            if m:
                out.append({"op": "delete_row", "table": m.group(1).strip().strip("`"), "all": True})
                continue
            m = re.match(r"^(.+?)\s+WHERE\s+(.+?)\s*=\s*(.+)$", rest, re.I)
            if m:
                out.append({"op": "delete_row", "table": m.group(1).strip().strip("`"),
                            "where": {"column": m.group(2).strip().strip("`"), "value": m.group(3).strip().strip("\"'`")}})
        elif up.startswith("CHANGE_TYPE "):
            m = genie.REL.match(line[12:].strip())
            if m:
                out.append({"op": "change_type", "table": m.group(1), "column": m.group(2), "new_type": m.group(3).strip().lower()})
        elif up.startswith("SET_REQUIRED "):
            m = genie.REL.match(line[13:].strip())
            if m:
                out.append({"op": "set_required", "table": m.group(1), "column": m.group(2),
                            "required": m.group(3).strip().lower() in ("yes", "true", "required", "y", "1")})
        elif up.startswith("DROP_RELATION "):
            m = re.match(r"^(%s)\s*\.\s*([A-Za-z_][A-Za-z0-9_ ]*)$" % NAME, line[14:].strip())
            if m:
                out.append({"op": "drop_relation", "table": m.group(1), "column": m.group(2).strip()})
        elif up.startswith("ADJUST "):
            m = re.match(r"^(%s)\s*\.\s*(%s)\s*([+\-*/])\s*(-?\d+(?:\.\d+)?)\s*(?:WHERE\s+(.+?)\s*=\s*(.+))?$" % (NAME, NAME), line[7:].strip(), re.I)
            if m:
                act = {"op": "adjust", "table": m.group(1), "column": m.group(2), "operator": m.group(3), "value": m.group(4)}
                if m.group(5):
                    act["where"] = {"column": m.group(5).strip().strip("`"), "value": m.group(6).strip().strip("\"'`")}
                out.append(act)
    return out


def _parse_actions(text):
    say, actions = _orig_parse(text)
    return say, (actions + parse_extra(text))[:genie.MAX_ACTIONS]


def _col(t, name):
    for c in t["columns"]:
        if c["name"] == name:
            return c
    return None


def _find(biz, t, cname, value, limit):
    col = _col(t, cname)
    if not col:
        return None, f"{P(t['name'])} has no field called {P(cname)}."
    value = str(value).strip()
    if not value:
        return None, "Tell me which value to look for."
    ctype = col["type"]
    if col["references"]:
        ctype = "integer"
        if not value.isdigit():
            parent = db.get_table(biz, col["references"].split(".")[0])
            with db.connect() as c:
                rid = db.resolve_link(c, biz["pg_schema"], parent, value)
            if rid is None:
                return None, f"There is no {P(parent['name'])} record called '{value}'."
            value = str(rid)
    try:
        return db.ids_where(biz, t["name"], cname, ctype, value, limit), None
    except Exception:
        return None, f"'{value}' is not a valid value for {P(cname)}."


def _fits(ntype, v):
    s = str(v).strip()
    try:
        if ntype == "integer":
            return bool(re.fullmatch(r"-?\d+", s))
        if ntype == "numeric":
            return bool(re.fullmatch(r"-?\d+(\.\d+)?", s))
        if ntype == "boolean":
            return s.lower() in BOOLS
        if ntype == "date":
            date.fromisoformat(s)
        if ntype == "timestamp":
            datetime.fromisoformat(s)
    except ValueError:
        return False
    return True


def check_ext(biz, raw):
    errors, clean, descs = [], [], []
    tables = {t["name"]: t for t in biz["design"]["tables"]}
    for a in raw:
        op = a.get("op")
        tname = _clean_name(a.get("table"))
        t = tables.get(tname)
        if not t:
            errors.append(f"There is no table called {P(a.get('table'))}.")
            continue
        cname = _clean_name(a.get("column")) if a.get("column") else None
        col = _col(t, cname) if cname else None

        if op == "delete_row":
            if a.get("all"):
                try:
                    ids = db.ids_where(biz, tname, None, None, None, MAX_DELETE + 1)
                except Exception:
                    errors.append("I could not look up those records.")
                    continue
                keep, where_txt = {"all": True}, ""
            else:
                w = a.get("where") if isinstance(a.get("where"), dict) else {}
                wc = _clean_name(w.get("column"))
                ids, err = _find(biz, t, wc, w.get("value", ""), MAX_DELETE + 1)
                if err:
                    errors.append(err)
                    continue
                keep = {"where": {"column": wc, "value": str(w.get("value")).strip()}}
                where_txt = f" where {P(wc)} = {str(w.get('value')).strip()}"
            if not ids:
                errors.append(f"I could not find any matching records in {P(tname)}.")
                continue
            if len(ids) > MAX_DELETE:
                errors.append(f"That would delete more than {MAX_DELETE} records at once. Be more specific.")
                continue
            n = len(ids)
            clean.append({"op": "delete_row", "table": tname, "ids": ids, **keep})
            descs.append(f"Delete {n} record{'' if n == 1 else 's'} from {P(tname)}{where_txt}. This cannot be undone")

        elif op == "change_type":
            new = str(a.get("new_type", "")).strip().lower().replace(" ", "_")
            new = TYPE_ALIAS.get(new, new)
            if not col or col["primary_key"]:
                errors.append(f"{P(tname)} has no field called {P(a.get('column'))} that can be changed.")
                continue
            if col["references"]:
                errors.append(f"{P(cname)} is a link to another table, so its type cannot change.")
                continue
            if new not in ALLOWED_TYPES:
                errors.append(f"I do not support the type '{new}'.")
                continue
            if new == col["type"]:
                errors.append(f"{P(cname)} is already of that type.")
                continue
            if not (col["type"] == "text" or new == "text" or (col["type"], new) in OK_CONV):
                errors.append(f"I cannot convert {P(cname)} from {col['type']} to {new}.")
                continue
            if col["type"] == "text" and new != "text":
                bad = sum(1 for v in db.column_values(biz, tname, cname, 5000) if not _fits(new, v))
                if bad:
                    errors.append(f"{bad} value{'' if bad == 1 else 's'} in {P(cname)} cannot be converted to {new}. Fix or delete them first.")
                    continue
            clean.append({"op": "change_type", "table": tname, "column": cname, "new_type": new})
            descs.append(f"Change {P(cname)} in {P(tname)} from {col['type']} to {new}")

        elif op == "set_required":
            want = bool(a.get("required"))
            if not col or col["primary_key"]:
                errors.append(f"{P(tname)} has no field called {P(a.get('column'))} that can be changed.")
                continue
            if want == col["required"]:
                errors.append(f"{P(cname)} in {P(tname)} is already {'required' if want else 'optional'}.")
                continue
            if want:
                empty = db.null_count(biz, tname, cname)
                if empty:
                    errors.append(f"{empty} record{'' if empty == 1 else 's'} in {P(tname)} have no {P(cname)}. Fill them in first.")
                    continue
            clean.append({"op": "set_required", "table": tname, "column": cname, "required": want})
            descs.append(f"Make {P(cname)} in {P(tname)} {'required' if want else 'optional'}")

        elif op == "drop_relation":
            if not col or not col["references"]:
                errors.append(f"{P(a.get('column'))} in {P(tname)} is not linked to another table.")
                continue
            parent = col["references"].split(".")[0]
            clean.append({"op": "drop_relation", "table": tname, "column": cname})
            descs.append(f"Remove the link from {P(cname)} in {P(tname)} to {P(parent)}. The field and its values stay")

        elif op == "adjust":
            sym = str(a.get("operator", ""))
            if not col or col["type"] not in ("integer", "numeric") or col["primary_key"] or col["references"]:
                errors.append(f"{P(a.get('column'))} in {P(tname)} is not a number field I can change.")
                continue
            if sym not in ("+", "-", "*", "/"):
                errors.append("I did not understand that calculation.")
                continue
            try:
                val = Decimal(str(a.get("value")))
            except (InvalidOperation, ValueError):
                errors.append("That is not a valid number.")
                continue
            if sym == "/" and val == 0:
                errors.append("I cannot divide by zero.")
                continue
            w = a.get("where") if isinstance(a.get("where"), dict) else None
            if w and w.get("column"):
                wc = _clean_name(w.get("column"))
                ids, err = _find(biz, t, wc, w.get("value", ""), MAX_ADJUST + 1)
                if err:
                    errors.append(err)
                    continue
                if not ids:
                    errors.append(f"I could not find any matching records in {P(tname)}.")
                    continue
                if len(ids) > MAX_ADJUST:
                    errors.append("That matches too many records. Be more specific.")
                    continue
                keep = {"where": {"column": wc, "value": str(w.get("value")).strip()}}
                scope = f"{len(ids)} record{'' if len(ids) == 1 else 's'} where {P(wc)} = {str(w.get('value')).strip()}"
            else:
                n = db.table_count(biz, tname)
                if n == 0:
                    errors.append(f"{P(tname)} has no records yet.")
                    continue
                if n > MAX_ADJUST:
                    errors.append("That table is too large to change all at once.")
                    continue
                ids, keep, scope = None, {}, f"all {n} records"
            clean.append({"op": "adjust", "table": tname, "column": cname, "operator": sym,
                          "value": str(val), "ids": ids, **keep})
            descs.append(f"Set {P(cname)} = {P(cname)} {sym} {val} for {scope} in {P(tname)}")
    return clean, errors, descs


def _check(biz, raw):
    clean, errors, descs = _orig_check(biz, raw)
    ext = [a for a in raw if isinstance(a, dict) and a.get("op") in EXT_OPS] if isinstance(raw, list) else []
    if ext:
        c2, e2, d2 = check_ext(biz, ext[:genie.MAX_ACTIONS])
        clean, errors, descs = clean + c2, errors + e2, descs + d2
    return clean, errors, descs


def _local_actions(message, names):
    low = message.strip().rstrip(".!?").strip().lower()
    low = re.sub(r"^(please|can you|could you|kindly)\s+", "", low)
    m = re.match(r"^(?:delete|remove|clear|erase|wipe)\s+(?:all|every)\s+(?:the\s+)?(?:records|rows|data|entries)\s+(?:in|from|of)\s+(?:the\s+)?(.+?)(?:\s+table)?$", low)
    if m and genie.find_table(m.group(1), names):
        return [{"op": "delete_row", "table": genie.find_table(m.group(1), names), "all": True}]
    m = re.match(r"^(?:make|set)\s+(?:the\s+)?(.+?)\s+(?:field|column)\s+(?:in|of|on)\s+(?:the\s+)?(.+?)(?:\s+table)?\s+(required|optional|mandatory)$", low)
    if m and genie.find_table(m.group(2), names):
        return [{"op": "set_required", "table": genie.find_table(m.group(2), names),
                 "column": genie.norm(m.group(1)), "required": m.group(3) != "optional"}]
    return _orig_local(message, names)


if not getattr(genie, "_ext_installed", False):
    _orig_prompt, _orig_parse = genie.build_prompt, genie.parse_actions
    _orig_check, _orig_local = genie.check, genie.local_actions
    genie.build_prompt, genie.parse_actions = _build_prompt, _parse_actions
    genie.check, genie.local_actions = _check, _local_actions
    genie._ext_installed = True



_RENAME = [
    re.compile(r"^(?:in|on)\s+(?:the\s+)?(.+?)\s+table,?\s+(?:change|rename|call)\s+(?:the\s+)?(.+?)(?:\s+(?:field|column))?\s+(?:to|as|into)\s+(.+)$"),
    re.compile(r"^(?:change|rename)\s+(?:the\s+)?(.+?)\s+(?:field|column)\s+(?:in|of|on)\s+(?:the\s+)?(.+?)(?:\s+table)?\s+(?:to|as|into)\s+(.+)$"),
]


def _local2(message, names):
    low = message.strip().rstrip(".!?").strip().lower()
    low = re.sub(r"^(please|can you|could you|kindly)\s+", "", low)
    biz = getattr(_local, "biz", None)
    if biz:
        for i, rx in enumerate(_RENAME):
            m = rx.match(low)
            if not m:
                continue
            if i == 0:
                tbl, col, new = m.group(1), m.group(2), m.group(3)
            else:
                col, tbl, new = m.group(1), m.group(2), m.group(3)
            t = genie.find_table(tbl, names)
            if not t:
                continue
            cn = genie.norm(col)
            tab = next(x for x in biz["design"]["tables"] if x["name"] == t)
            if any(c["name"] == cn and not c["primary_key"] for c in tab["columns"]):
                return [{"op": "rename_column", "table": t, "column": cn, "new_name": genie.norm(new)}]
    return _prev_local(message, names)


def _sections2(message):
    s = _prev_sections(message)
    if re.search(r"\b(change|call|name|rename)\b", message.lower()):
        s.update(["table", "column"])
    return s


def _plan2(biz, message):
    _local.biz = biz
    return _prev_plan(biz, message)


if not getattr(genie, "_ext2_installed", False):
    _prev_local, _prev_sections, _prev_plan = genie.local_actions, genie.sections, genie.plan
    genie.local_actions, genie.sections, genie.plan = _local2, _sections2, _plan2
    genie._ext2_installed = True
