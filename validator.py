import re

ALLOWED_TYPES = {"text", "integer", "numeric", "boolean", "date", "timestamp"}
NAME_RE = re.compile(r"^[a-z][a-z0-9_]{0,40}$")
MAX_TABLES = 15
MAX_COLUMNS = 25

# Names that would clash with PostgreSQL keywords or system objects
RESERVED = {
    "user", "users", "select", "insert", "update", "delete", "drop", "table",
    "where", "from", "order", "group", "by", "and", "or", "not", "null",
    "primary", "foreign", "key", "check", "default", "create", "alter",
    "grant", "index", "column", "constraint", "references", "union", "all",
    "analyse", "analyze", "authorization", "desc", "asc", "limit", "offset",
    "pg_catalog", "information_schema", "auth", "storage",
}


def _clean_name(value):
    if not isinstance(value, str):
        return None
    name = value.strip().lower().replace(" ", "_").replace("-", "_")
    return name


def _check_name(name, what, errors):
    if not name or not NAME_RE.match(name):
        errors.append(f"{what} '{name}' is not a valid name (use letters, numbers and underscores only).")
        return False
    if name in RESERVED:
        errors.append(f"{what} '{name}' is a reserved word. Please use a different name.")
        return False
    return True


def validate_schema(schema):
    """
    Takes a schema dict (from Gemma or a blueprint).
    Returns (ok, cleaned_schema_or_None, errors_list).
    The cleaned schema is rebuilt from scratch so no unexpected keys survive.
    """
    errors = []

    if not isinstance(schema, dict) or not isinstance(schema.get("tables"), list):
        return False, None, ["The design is missing its list of tables."]

    raw_tables = schema["tables"]
    if not raw_tables:
        return False, None, ["The design has no tables."]
    if len(raw_tables) > MAX_TABLES:
        errors.append(f"Too many tables ({len(raw_tables)}). Maximum is {MAX_TABLES}.")
        raw_tables = raw_tables[:MAX_TABLES]

    # Pass 1: clean names, types, duplicates
    tables = {}
    for t in raw_tables:
        if not isinstance(t, dict):
            errors.append("A table entry is not valid.")
            continue
        tname = _clean_name(t.get("name"))
        if not _check_name(tname, "Table", errors):
            continue
        if tname in tables:
            errors.append(f"Table '{tname}' appears more than once.")
            continue

        raw_cols = t.get("columns")
        if not isinstance(raw_cols, list) or not raw_cols:
            errors.append(f"Table '{tname}' has no columns.")
            continue
        if len(raw_cols) > MAX_COLUMNS:
            errors.append(f"Table '{tname}' has too many columns. Maximum is {MAX_COLUMNS}.")
            raw_cols = raw_cols[:MAX_COLUMNS]

        cols, seen = [], set()
        for c in raw_cols:
            if not isinstance(c, dict):
                errors.append(f"A column in '{tname}' is not valid.")
                continue
            cname = _clean_name(c.get("name"))
            if not _check_name(cname, f"Column in '{tname}'", errors):
                continue
            if cname in seen:
                errors.append(f"Column '{cname}' appears twice in '{tname}'.")
                continue
            seen.add(cname)

            ctype = str(c.get("type", "text")).strip().lower()
            if ctype not in ALLOWED_TYPES:
                errors.append(f"Column '{tname}.{cname}' has unsupported type '{ctype}'.")
                continue

            ref = c.get("references")
            ref = ref.strip().lower() if isinstance(ref, str) and ref.strip() and ref.strip().lower() != "null" else None

            cols.append({
                "name": cname,
                "type": ctype,
                "required": bool(c.get("required", False)),
                "primary_key": bool(c.get("primary_key", False)),
                "references": ref,
            })

        # Make sure there is exactly one primary key called id
        pks = [c for c in cols if c["primary_key"]]
        if not any(c["name"] == "id" for c in cols):
            cols.insert(0, {"name": "id", "type": "integer", "required": True,
                            "primary_key": True, "references": None})
        elif not pks:
            for c in cols:
                if c["name"] == "id":
                    c["primary_key"] = True
                    c["required"] = True
        pk_count = sum(c["primary_key"] for c in cols)
        if pk_count != 1:
            for c in cols:
                c["primary_key"] = (c["name"] == "id")
        for c in cols:
            if c["name"] == "id":
                c["type"] = "integer"
                c["references"] = None

        tables[tname] = cols

    # Pass 2: check references point at real tables and columns
    for tname, cols in tables.items():
        for c in cols:
            ref = c["references"]
            if not ref:
                continue
            if ref.count(".") != 1:
                errors.append(f"Link '{tname}.{c['name']}' has an invalid target '{ref}'.")
                c["references"] = None
                continue
            rt, rc = ref.split(".")
            if rt not in tables:
                errors.append(f"'{tname}.{c['name']}' links to table '{rt}', which does not exist.")
                c["references"] = None
            elif rc != "id":
                errors.append(f"'{tname}.{c['name']}' must link to the id of '{rt}'.")
                c["references"] = None
            elif rt == tname:
                c["references"] = None  # skip self links to keep creation simple
            elif c["type"] != "integer":
                c["type"] = "integer"

    if not tables:
        return False, None, errors or ["No valid tables were found."]

    # Pass 3: order tables so referenced tables are created first
    ordered, remaining = [], dict(tables)
    while remaining:
        progress = False
        for tname in list(remaining):
            deps = {c["references"].split(".")[0] for c in remaining[tname] if c["references"]}
            if all(d in [o["name"] for o in ordered] for d in deps):
                ordered.append({"name": tname, "columns": remaining.pop(tname)})
                progress = True
        if not progress:
            # circular links: drop the offending references so creation still works
            tname = next(iter(remaining))
            for c in remaining[tname]:
                if c["references"]:
                    errors.append(f"Circular link removed at '{tname}.{c['name']}'.")
                    c["references"] = None
            ordered.append({"name": tname, "columns": remaining.pop(tname)})

    cleaned = {"name": str(schema.get("name", "custom"))[:40], "tables": ordered}

    # Only hard failures block approval; fixed-up issues are reported as warnings
    return True, cleaned, errors


if __name__ == "__main__":
    import json, glob
    for path in sorted(glob.glob("blueprints/*.json")):
        ok, cleaned, errs = validate_schema(json.load(open(path)))
        print(f"{path}: ok={ok} tables={len(cleaned['tables']) if cleaned else 0} issues={errs}")

    bad = {"tables": [
        {"name": "products; DROP TABLE sales", "columns": [{"name": "id", "type": "integer"}]},
        {"name": "orders", "columns": [
            {"name": "id", "type": "money"},
            {"name": "item_id", "type": "integer", "references": "items.id"},
            {"name": "item_id", "type": "text"}]},
        {"name": "user", "columns": [{"name": "id", "type": "integer"}]},
    ]}
    ok, cleaned, errs = validate_schema(bad)
    print("\nBad schema test: ok =", ok)
    for e in errs:
        print(" -", e)
