import os, re, requests
from datetime import date, datetime
from decimal import Decimal
from dotenv import load_dotenv
from psycopg import sql
import db
import designer
from genie import pretty, find_table, schema_text
from validator import _clean_name

load_dotenv(override=True)
KEY = os.getenv("GEMMA_API_KEY")
MODEL = os.getenv("GEMMA_MODEL")
TIMEOUT = 40

CHANGE = re.compile(r"^\s*(please\s+)?(add|create|delete|drop|remove|rename|change|update|edit|link|connect|set|insert|make|put|fix|modify|replace)\b", re.I)
ASK = re.compile(r"^\s*(please\s+)?(how|what|which|who|when|where|show|list|view|display|give|tell|is|are|do|does|total|count|average|top|compare|any)\b", re.I)
WHERE_RE = re.compile(r"^(\S+?)\s*(>=|<=|!=|=|>|<|contains)\s*(.+)$", re.I)
AGG_RE = re.compile(r"^(sum|count|avg|min|max)\s*\(?\s*([A-Za-z_][A-Za-z0-9_]*|\*)\s*\)?", re.I)

AGGS = {"sum", "count", "avg", "min", "max"}
OPS = {"=", "!=", ">", "<", ">=", "<=", "contains"}
NUM = ("integer", "numeric")
ORDERED = ("integer", "numeric", "date", "timestamp")
HEAD = {"sum": "Total {}", "avg": "Average {}", "min": "Lowest {}", "max": "Highest {}", "count": "Count of {}"}

PROMPT = """You answer questions about a small business database by writing a query plan. Never write SQL.
Tables (field:type, ! means required, >table means linked to that table, and linked fields show that table's names):
{schema}
Today is {today}.
Output ONLY lines in these formats, no other text, no markdown:
SAY: <one short friendly sentence>
QUERY <table>
SELECT <field>, <field>
AGG <sum|count|avg|min|max> <field or *>
WHERE <field> <op> <value>
GROUP <field>
ORDER <field or agg> <asc|desc>
LIMIT <number>
Rules: Use only ONE table and its exact field names. SELECT is for listing records, leave it out for totals and groups. Op is one of = != > < >= <= contains. Write a date as YYYY-MM-DD or the word today. To compare one field with another write @field as the value. Repeat WHERE for more conditions.
For "how much", "total", "revenue", "earned" or "spent" use AGG sum. For "how many" use AGG count *. For "most", "best" or "top" use GROUP on the linked or category field, AGG sum or count, ORDER agg desc and LIMIT 5. For "low stock" compare the stock field with the reorder field using @.
Question: """


def is_question(message):
    if CHANGE.match(message):
        return False
    return message.strip().endswith("?") or bool(ASK.match(message))


def ask(design, message):
    if not MODEL:
        return None
    prompt = PROMPT.replace("{schema}", schema_text(design)).replace("{today}", date.today().isoformat()) + message
    try:
        r = requests.post(
            f"https://generativelanguage.googleapis.com/v1beta/models/{MODEL}:generateContent",
            headers={"x-goog-api-key": KEY},
            json={"contents": [{"parts": [{"text": prompt}]}],
                  "generationConfig": designer.gen_config(3000, 0.1)},
            timeout=TIMEOUT)
        if r.status_code != 200:
            return None
        parts = r.json()["candidates"][0]["content"]["parts"]
        text = "".join(p.get("text", "") for p in parts if not p.get("thought"))
        return text if text.strip() else None
    except Exception as e:
        print("Question attempt failed:", type(e).__name__)
        return None


def parse(text):
    say = ""
    q = {"table": None, "select": [], "aggs": [], "where": [], "group": None, "order": None, "limit": None}
    for line in text.splitlines():
        line = line.strip().lstrip("-*").strip().strip("`").strip()
        up = line.upper()
        if up.startswith("SAY:"):
            say = line[4:].strip()[:300]
        elif up.startswith("QUERY "):
            q["table"] = line[6:].strip()
        elif up.startswith("SELECT "):
            q["select"] = [x.strip() for x in line[7:].split(",") if x.strip()]
        elif up.startswith("AGG "):
            m = AGG_RE.match(line[4:].strip())
            if m:
                q["aggs"].append((m.group(1).lower(), m.group(2)))
        elif up.startswith("WHERE "):
            m = WHERE_RE.match(line[6:].strip())
            if m:
                q["where"].append((m.group(1), m.group(2).lower(), m.group(3).strip().strip("\"'")))
        elif up.startswith("GROUP "):
            q["group"] = line[6:].strip()
        elif up.startswith("ORDER "):
            parts = line[6:].split()
            if parts:
                q["order"] = (parts[0], parts[1].lower() if len(parts) > 1 else "desc")
        elif up.startswith("LIMIT "):
            try:
                q["limit"] = int(line[6:].strip())
            except ValueError:
                pass
    return say, q


def _first(cols, pattern):
    for c in cols:
        if re.search(pattern, c["name"]):
            return c["name"]
    return None


def local(message, biz):
    low = message.strip().rstrip("?.! ").lower()
    low = re.sub(r"^(please|can you|could you|kindly)\s+", "", low)
    names = {t["name"] for t in biz["design"]["tables"]}
    base = {"select": [], "aggs": [], "where": [], "group": None, "order": None, "limit": 20}

    if re.search(r"\b(low stock|running out|out of stock|need restock|needs restock|reorder)\b", low):
        for t in biz["design"]["tables"]:
            qty = _first(t["columns"], r"(stock|quantity)")
            rl = _first(t["columns"], r"reorder")
            if qty and rl:
                return "Here are the items that are low on stock.", dict(
                    base, table=t["name"], where=[(qty, "<=", "@" + rl)], order=(qty, "asc"))

    m = re.match(r"^how many (.+?)(?: do i have| are there| have i got| in total)?$", low)
    if m and find_table(m.group(1), names):
        t = find_table(m.group(1), names)
        return f"Here is the number of {pretty(t)}.", dict(base, table=t, aggs=[("count", "*")])

    m = re.match(r"^(?:show|list|view|display|give)(?: me)?(?: all)?(?: of)?(?: my| the)? (.+)$", low)
    if m and find_table(m.group(1), names):
        t = find_table(m.group(1), names)
        return f"Here are your {pretty(t)}.", dict(base, table=t)
    return None


def fmt(v):
    if v is None:
        return ""
    if isinstance(v, bool):
        return "Yes" if v else "No"
    if isinstance(v, Decimal):
        return f"{int(v):,}" if v == v.to_integral_value() else f"{v:,.2f}"
    if isinstance(v, (date, datetime)):
        return v.strftime("%Y-%m-%d")
    return str(v)


def run(biz, q):
    pg = biz["pg_schema"]
    t = db.get_table(biz, _clean_name(q.get("table") or ""))
    if not t:
        return None, None, ["I could not tell which table you mean."]
    cols = {c["name"]: c for c in t["columns"]}
    errors, joins, alias, counter = [], [], {}, [0]

    def plain(cname):
        return sql.SQL("t.{}").format(sql.Identifier(cname))

    def shown(cname):
        c = cols[cname]
        if not c["references"]:
            return plain(cname), pretty(cname)
        if cname not in alias:
            counter[0] += 1
            parent = db.get_table(biz, c["references"].split(".")[0])
            a = f"j{counter[0]}"
            alias[cname] = (a, db.label_col(parent))
            joins.append(sql.SQL(" left join {}.{} as {} on {}.id = t.{}").format(
                sql.Identifier(pg), sql.Identifier(parent["name"]), sql.Identifier(a),
                sql.Identifier(a), sql.Identifier(cname)))
        a, lab = alias[cname]
        return sql.SQL("{}.{}").format(sql.Identifier(a), sql.Identifier(lab)), pretty(re.sub(r"_id$", "", cname))

    aggs, agg_heads = [], []
    for fn, field in (q.get("aggs") or [])[:3]:
        fn = str(fn).lower()
        if fn not in AGGS:
            errors.append(f"I cannot calculate '{fn}'.")
            continue
        if field == "*":
            if fn != "count":
                errors.append("Only counting works without a field.")
                continue
            aggs.append(sql.SQL("count(*)"))
            agg_heads.append("Count")
            continue
        fname = _clean_name(field)
        c = cols.get(fname)
        if not c:
            errors.append(f"{pretty(t['name'])} has no field called {pretty(field)}.")
            continue
        if fn in ("sum", "avg") and c["type"] not in NUM:
            errors.append(f"{pretty(fname)} is not a number, so I cannot add it up.")
            continue
        if fn in ("min", "max") and c["type"] not in ORDERED:
            errors.append(f"I cannot compare {pretty(fname)} values.")
            continue
        aggs.append(sql.SQL("{}({})").format(sql.SQL(fn), plain(fname)))
        agg_heads.append(HEAD[fn].format(pretty(fname)))

    group = None
    if q.get("group"):
        g = _clean_name(q["group"])
        if g not in cols or cols[g]["primary_key"]:
            errors.append(f"{pretty(t['name'])} has no field called {pretty(q['group'])} to group by.")
        else:
            group = g

    wh, params = [], []
    for f, op, v in (q.get("where") or [])[:5]:
        fname, op, v = _clean_name(f), str(op).lower(), str(v).strip()
        c = cols.get(fname)
        if not c:
            errors.append(f"{pretty(t['name'])} has no field called {pretty(f)}.")
            continue
        if op not in OPS:
            errors.append("I did not understand one of the conditions.")
            continue
        if v.startswith("@"):
            o = cols.get(_clean_name(v[1:]))
            if not o or op in ("contains",) or o["type"] not in ORDERED or c["type"] not in ORDERED:
                errors.append("I could not compare those two fields.")
                continue
            wh.append(sql.SQL("{} {} {}").format(plain(fname), sql.SQL(op), plain(o["name"])))
            continue
        if c["references"]:
            parent = db.get_table(biz, c["references"].split(".")[0])
            if v.isdigit() and op in ("=", "!="):
                wh.append(sql.SQL("{} {} %s").format(plain(fname), sql.SQL(op)))
                params.append(int(v))
                continue
            if op not in ("=", "!=", "contains"):
                errors.append(f"I cannot use that comparison on {pretty(fname)}.")
                continue
            wh.append(sql.SQL("t.{col} {neg}in (select id from {pg}.{par} where lower({lab}::text) {cmp} lower(%s))").format(
                col=sql.Identifier(fname), neg=sql.SQL("not " if op == "!=" else ""),
                pg=sql.Identifier(pg), par=sql.Identifier(parent["name"]),
                lab=sql.Identifier(db.label_col(parent)), cmp=sql.SQL("like" if op == "contains" else "=")))
            params.append(f"%{v}%" if op == "contains" else v)
            continue
        ty = c["type"]
        if ty == "text":
            if op == "contains":
                wh.append(sql.SQL("{} ilike %s").format(plain(fname)))
                params.append(f"%{v}%")
            elif op in ("=", "!="):
                wh.append(sql.SQL("lower({}) {} lower(%s)").format(plain(fname), sql.SQL(op)))
                params.append(v)
            else:
                errors.append(f"I cannot compare {pretty(fname)} that way.")
            continue
        if op == "contains" or (ty == "boolean" and op not in ("=", "!=")):
            errors.append(f"I cannot compare {pretty(fname)} that way.")
            continue
        try:
            if ty == "timestamp":
                val, ce = db.convert_value("date", v), sql.SQL("{}::date").format(plain(fname))
            else:
                val, ce = db.convert_value(ty, v), plain(fname)
        except Exception:
            errors.append(f"'{v}' is not a valid value for {pretty(fname)}.")
            continue
        wh.append(sql.SQL("{} {} %s").format(ce, sql.SQL(op)))
        params.append(val)

    sel, heads, group_expr = [], [], None
    if group:
        group_expr, h = shown(group)
        sel.append(group_expr)
        heads.append(h)
        if not aggs:
            aggs, agg_heads = [sql.SQL("count(*)")], ["Count"]
        sel += aggs
        heads += agg_heads
    elif aggs:
        sel += aggs
        heads += agg_heads
    else:
        names = [n for n in (_clean_name(s) for s in (q.get("select") or [])) if n in cols and not cols[n]["primary_key"]]
        if not names:
            names = [c["name"] for c in t["columns"] if not c["primary_key"]][:8]
        for n in names[:10]:
            e, h = shown(n)
            sel.append(e)
            heads.append(h)

    if errors:
        return None, None, errors

    query = sql.SQL("select {} from {}.{} as t").format(
        sql.SQL(", ").join(sel), sql.Identifier(pg), sql.Identifier(t["name"]))
    query = query + sql.SQL("").join(joins)
    if wh:
        query = query + sql.SQL(" where ") + sql.SQL(" and ").join(wh)
    if group:
        query = query + sql.SQL(" group by ") + group_expr

    o = q.get("order")
    direction = "asc" if (o and str(o[1]).lower() == "asc") else "desc"
    okey = _clean_name(o[0]) if o else None
    if group:
        ob = group_expr if okey == group else aggs[0]
        query = query + sql.SQL(" order by ") + ob + sql.SQL(" " + direction)
    elif not aggs:
        ob = plain(okey) if okey in cols else plain("id")
        query = query + sql.SQL(" order by ") + ob + sql.SQL(" " + direction)
    if group or not aggs:
        try:
            limit = int(q.get("limit") or (10 if group else 20))
        except (TypeError, ValueError):
            limit = 20
        query = query + sql.SQL(" limit {}").format(sql.Literal(max(1, min(limit, 50))))

    try:
        with db.connect() as c:
            raw = c.execute(query, params).fetchall()
    except Exception as e:
        print("Question failed:", type(e).__name__, e)
        return None, None, ["I could not run that question. Try asking it a different way."]
    return heads, [[fmt(v) for v in r] for r in raw], []


def answer(biz, message):
    loc = local(message, biz)
    if loc:
        say, q = loc
    else:
        text = ask(biz["design"], message)
        if text is None:
            return None
        say, q = parse(text)
        if not q["table"]:
            return {"reply": say or "I could not find an answer to that. Try asking about one table, for example: How many products do I have?",
                    "errors": [], "headers": None, "rows": None}
    headers, rows, errors = run(biz, q)
    return {"reply": "" if errors else say, "errors": errors, "headers": headers, "rows": rows}
