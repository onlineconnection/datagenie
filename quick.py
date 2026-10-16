import re
from genie import pretty, find_table
import db

BASE = {"select": [], "aggs": [], "where": [], "group": None, "order": None, "limit": 50}


def _bool_col(biz, pattern):
    for t in biz["design"]["tables"]:
        for c in t["columns"]:
            if c["type"] == "boolean" and re.search(pattern, c["name"]):
                return t["name"], c["name"]
    return None


def match(message, biz):
    low = message.strip().rstrip("?.! ").lower()
    low = re.sub(r"^(please|can you|could you|kindly)\s+", "", low)
    names = {t["name"] for t in biz["design"]["tables"]}

    # who has not paid / unpaid
    if re.search(r"\b(not paid|hasn'?t paid|haven'?t paid|unpaid|owing|owe|outstanding)\b", low):
        hit = _bool_col(biz, r"paid")
        if hit:
            return "Here are the records that are not paid yet.", dict(
                BASE, table=hit[0], where=[(hit[1], "=", "false")])

    # who paid
    if re.search(r"\b(paid|who pay|pay their|have paid)\b", low):
        hit = _bool_col(biz, r"paid")
        if hit:
            return "Here are the records that are paid.", dict(
                BASE, table=hit[0], where=[(hit[1], "=", "true")])

    # total / how much
    m = re.match(r"^(?:total|sum|how much)(?: of)?(?: the)?(?: money)?(?: from| in| for)? ?(.*)$", low)
    if m:
        words = m.group(1).replace("collected", "").replace("earned", "").strip()
        t = find_table(words, names) if words else None
        tables = [x for x in biz["design"]["tables"] if x["name"] == t] if t else biz["design"]["tables"]
        for tb in tables:
            for c in tb["columns"]:
                if c["type"] in ("numeric", "integer") and db.is_money(c["name"]):
                    return f"Here is the total of {pretty(c['name'])}.", dict(
                        BASE, table=tb["name"], aggs=[("sum", c["name"])])
    return None
