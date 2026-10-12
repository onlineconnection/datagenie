import os, json, uuid
import psycopg
from psycopg import sql
from dotenv import load_dotenv

load_dotenv(override=True)

TYPE_SQL = {
    "text": "text",
    "integer": "integer",
    "numeric": "numeric(14,2)",
    "boolean": "boolean",
    "date": "date",
    "timestamp": "timestamp",
}


from psycopg_pool import ConnectionPool

_pool = None

def _get_pool():
    global _pool
    if _pool is None:
        _pool = ConnectionPool(os.getenv("DATABASE_URL"), min_size=1, max_size=4, open=True)
    return _pool

def connect():
    return _get_pool().connection()


def init_registry():
    with connect() as c:
        c.execute("""
            create table if not exists datagenie_businesses (
                id text primary key,
                title text not null,
                description text not null,
                design jsonb not null,
                pg_schema text,
                status text not null default 'pending',
                created_at timestamptz not null default now()
            )""")
        c.execute("alter table datagenie_businesses add column if not exists currency text not null default 'USD'")
        c.execute("alter table datagenie_businesses add column if not exists share_token text")


def get_currency(biz_id):
    with connect() as c:
        r = c.execute("select currency from datagenie_businesses where id=%s", (biz_id,)).fetchone()
    return r[0] if r else "USD"


def set_currency(biz_id, code):
    with connect() as c:
        c.execute("update datagenie_businesses set currency=%s where id=%s", (code, biz_id))


def save_pending(description, design):
    biz_id = uuid.uuid4().hex[:16]
    title = design.get("name", "business").replace("_", " ").title()
    with connect() as c:
        c.execute(
            "insert into datagenie_businesses (id, title, description, design) values (%s,%s,%s,%s)",
            (biz_id, title, description[:2000], json.dumps(design)),
        )
    return biz_id


def get_business(biz_id):
    with connect() as c:
        row = c.execute(
            "select id, title, description, design, pg_schema, status from datagenie_businesses where id=%s",
            (biz_id,),
        ).fetchone()
    if not row:
        return None
    return dict(zip(["id", "title", "description", "design", "pg_schema", "status"], row))


def create_tables(biz_id):
    biz = get_business(biz_id)
    if not biz:
        raise ValueError("Business not found.")
    if biz["status"] == "active":
        return biz["pg_schema"]

    pg_schema = "biz_" + biz_id
    with connect() as c:
        c.execute(sql.SQL("create schema {}").format(sql.Identifier(pg_schema)))
        for table in biz["design"]["tables"]:
            parts = []
            for col in table["columns"]:
                if col["primary_key"]:
                    parts.append(sql.SQL("{} integer generated always as identity primary key")
                                 .format(sql.Identifier(col["name"])))
                    continue
                piece = sql.SQL("{} {}").format(sql.Identifier(col["name"]),
                                                sql.SQL(TYPE_SQL[col["type"]]))
                if col["required"]:
                    piece += sql.SQL(" not null")
                if col["references"]:
                    rt, rc = col["references"].split(".")
                    piece += sql.SQL(" references {}.{} ({})").format(
                        sql.Identifier(pg_schema), sql.Identifier(rt), sql.Identifier(rc))
                parts.append(piece)
            c.execute(sql.SQL("create table {}.{} ({})").format(
                sql.Identifier(pg_schema), sql.Identifier(table["name"]),
                sql.SQL(", ").join(parts)))
        c.execute("update datagenie_businesses set status='active', pg_schema=%s where id=%s",
                  (pg_schema, biz_id))
    return pg_schema


def row_counts(biz):
    counts = {}
    with connect() as c:
        for t in biz["design"]["tables"]:
            q = sql.SQL("select count(*) from {}.{}").format(
                sql.Identifier(biz["pg_schema"]), sql.Identifier(t["name"]))
            counts[t["name"]] = c.execute(q).fetchone()[0]
    return counts


# ---------- data management ----------
from decimal import Decimal, InvalidOperation
from datetime import date, datetime


def get_table(biz, tname):
    for t in biz["design"]["tables"]:
        if t["name"] == tname:
            return t
    return None


def label_col(table):
    for c in table["columns"]:
        if c["type"] == "text" and not c["primary_key"] and not c["references"]:
            return c["name"]
    return "id"


def fk_options(biz, table):
    opts = {}
    with connect() as c:
        for col in table["columns"]:
            if col["references"]:
                rt = col["references"].split(".")[0]
                rtable = get_table(biz, rt)
                q = sql.SQL("select id, {} from {}.{} order by id desc limit 500").format(
                    sql.Identifier(label_col(rtable)), sql.Identifier(biz["pg_schema"]), sql.Identifier(rt))
                opts[col["name"]] = {r[0]: str(r[1]) for r in c.execute(q).fetchall()}
    return opts


def list_rows(biz, table, limit=200):
    cols = sql.SQL(", ").join(sql.Identifier(c["name"]) for c in table["columns"])
    q = sql.SQL("select {} from {}.{} order by id desc limit %s").format(
        cols, sql.Identifier(biz["pg_schema"]), sql.Identifier(table["name"]))
    with connect() as c:
        return c.execute(q, (limit,)).fetchall()


def insert_row(biz, table, form):
    values, errors = {}, []
    for col in table["columns"]:
        if col["primary_key"]:
            continue
        name, label = col["name"], col["name"].replace("_", " ").title()
        if col["type"] == "boolean":
            values[name] = form.get(name) is not None
            continue
        raw = (form.get(name) or "").strip()
        if raw == "":
            if col["required"]:
                errors.append(f"{label} is required.")
            continue
        try:
            if col["type"] == "integer":
                v = int(raw)
            elif col["type"] == "numeric":
                v = Decimal(raw)
            elif col["type"] == "date":
                v = date.fromisoformat(raw)
            elif col["type"] == "timestamp":
                v = datetime.fromisoformat(raw)
            else:
                v = raw[:2000]
        except (ValueError, InvalidOperation):
            errors.append(f"{label} is not valid.")
            continue
        values[name] = v
    if errors:
        return errors
    if not values:
        return ["Nothing to save."]
    q = sql.SQL("insert into {}.{} ({}) values ({})").format(
        sql.Identifier(biz["pg_schema"]), sql.Identifier(table["name"]),
        sql.SQL(", ").join(sql.Identifier(k) for k in values),
        sql.SQL(", ").join(sql.Placeholder() for _ in values))
    try:
        with connect() as c:
            c.execute(q, list(values.values()))
    except psycopg.Error:
        return ["Could not save. Check that the linked items exist."]
    return []


def delete_row(biz, table, row_id):
    q = sql.SQL("delete from {}.{} where id=%s").format(
        sql.Identifier(biz["pg_schema"]), sql.Identifier(table["name"]))
    try:
        with connect() as c:
            c.execute(q, (row_id,))
    except psycopg.errors.ForeignKeyViolation:
        return "This record is used by another record, so it cannot be deleted."
    except psycopg.Error:
        return "Could not delete this record."
    return None


# ---------- final product view ----------
def totals(biz, max_items=6):
    items = []
    with connect() as c:
        for t in biz["design"]["tables"]:
            for col in t["columns"]:
                if col["type"] == "numeric" and len(items) < max_items:
                    q = sql.SQL("select coalesce(sum({}), 0) from {}.{}").format(
                        sql.Identifier(col["name"]), sql.Identifier(biz["pg_schema"]),
                        sql.Identifier(t["name"]))
                    label = f"{t['name'].replace('_', ' ').title()}: {col['name'].replace('_', ' ').title()}"
                    items.append({"label": label, "value": c.execute(q).fetchone()[0]})
    return items


def search_rows(biz, table, term, limit=200):
    cols = sql.SQL(", ").join(sql.Identifier(c["name"]) for c in table["columns"])
    q = sql.SQL("select {} from {}.{}").format(
        cols, sql.Identifier(biz["pg_schema"]), sql.Identifier(table["name"]))
    params = []
    if term:
        conds = [sql.SQL("{}::text ilike %s").format(sql.Identifier(c["name"]))
                 for c in table["columns"] if not c["references"]]
        q = q + sql.SQL(" where ") + sql.SQL(" or ").join(conds)
        params = [f"%{term}%"] * len(conds)
    q = q + sql.SQL(" order by id desc limit %s")
    with connect() as c:
        return c.execute(q, params + [limit]).fetchall()


# ---------- accounts ----------
def init_users():
    with connect() as c:
        c.execute("""
            create table if not exists datagenie_users (
                id serial primary key,
                email text unique not null,
                password_hash text not null,
                created_at timestamptz not null default now()
            )""")
        c.execute("alter table datagenie_businesses add column if not exists owner_id integer")


def create_user(email, pw_hash):
    try:
        with connect() as c:
            return c.execute(
                "insert into datagenie_users (email, password_hash) values (%s,%s) returning id",
                (email, pw_hash)).fetchone()[0]
    except psycopg.errors.UniqueViolation:
        return None


def user_by_email(email):
    with connect() as c:
        return c.execute("select id, email, password_hash from datagenie_users where email=%s",
                         (email,)).fetchone()


def set_owner(biz_id, owner_id):
    with connect() as c:
        c.execute("update datagenie_businesses set owner_id=%s where id=%s", (owner_id, biz_id))


def owner_of(biz_id):
    with connect() as c:
        row = c.execute("select owner_id from datagenie_businesses where id=%s", (biz_id,)).fetchone()
    return row[0] if row else None


def list_businesses(owner_id):
    with connect() as c:
        rows = c.execute(
            "select id, title, status from datagenie_businesses where owner_id=%s order by created_at desc",
            (owner_id,)).fetchall()
    return [dict(zip(["id", "title", "status"], r)) for r in rows]



def table_count(biz, tname):
    q = sql.SQL("select count(*) from {}.{}").format(sql.Identifier(biz["pg_schema"]), sql.Identifier(tname))
    with connect() as c:
        return c.execute(q).fetchone()[0]


def _col_sql(pg_schema, col):
    piece = sql.SQL("{} {}").format(sql.Identifier(col["name"]), sql.SQL(TYPE_SQL[col["type"]]))
    if col["required"]:
        piece += sql.SQL(" not null")
    if col["references"]:
        rt, rc = col["references"].split(".")
        piece += sql.SQL(" references {}.{} ({})").format(
            sql.Identifier(pg_schema), sql.Identifier(rt), sql.Identifier(rc))
    return piece


def convert_value(ctype, raw):
    raw = raw.strip() if isinstance(raw, str) else raw
    if ctype == "boolean":
        if isinstance(raw, bool):
            return raw
        return str(raw).lower() in ("yes", "true", "1", "y", "paid", "done")
    if raw is None or raw == "":
        return None
    low = str(raw).lower()
    if ctype == "date":
        return date.today() if low == "today" else date.fromisoformat(str(raw))
    if ctype == "timestamp":
        return datetime.now() if low in ("now", "today") else datetime.fromisoformat(str(raw))
    if ctype == "integer":
        d = Decimal(str(raw).replace(",", ""))
        if d != d.to_integral_value():
            raise ValueError("not a whole number")
        return int(d)
    if ctype == "numeric":
        return Decimal(str(raw).replace(",", "").lstrip("$"))
    return str(raw)[:2000]


def resolve_link(c, pg, parent, value):
    value = str(value).strip()
    if value.isdigit():
        q = sql.SQL("select id from {}.{} where id=%s").format(sql.Identifier(pg), sql.Identifier(parent["name"]))
        row = c.execute(q, (int(value),)).fetchone()
        if row:
            return row[0]
    q = sql.SQL("select id from {}.{} where lower({}::text)=lower(%s) order by id limit 1").format(
        sql.Identifier(pg), sql.Identifier(parent["name"]), sql.Identifier(label_col(parent)))
    row = c.execute(q, (value,)).fetchone()
    return row[0] if row else None


def _tbl(design, name):
    return next(t for t in design["tables"] if t["name"] == name)


def apply_actions(biz_id, actions):
    biz = get_business(biz_id)
    design, pg = biz["design"], biz["pg_schema"]
    with connect() as c:
        for a in actions:
            op, tname = a["op"], a["table"]
            if op == "add_table":
                parts = [sql.SQL("{} integer generated always as identity primary key").format(sql.Identifier("id"))]
                parts += [_col_sql(pg, col) for col in a["columns"] if col["name"] != "id"]
                c.execute(sql.SQL("create table {}.{} ({})").format(
                    sql.Identifier(pg), sql.Identifier(tname), sql.SQL(", ").join(parts)))
                design["tables"].append({"name": tname, "columns": a["columns"]})
            elif op == "drop_table":
                c.execute(sql.SQL("drop table {}.{}").format(sql.Identifier(pg), sql.Identifier(tname)))
                design["tables"] = [t for t in design["tables"] if t["name"] != tname]
            elif op == "add_column":
                col = a["column"]
                c.execute(sql.SQL("alter table {}.{} add column {}").format(
                    sql.Identifier(pg), sql.Identifier(tname), _col_sql(pg, col)))
                _tbl(design, tname)["columns"].append(col)
            elif op == "drop_column":
                c.execute(sql.SQL("alter table {}.{} drop column {}").format(
                    sql.Identifier(pg), sql.Identifier(tname), sql.Identifier(a["column"])))
                t = _tbl(design, tname)
                t["columns"] = [x for x in t["columns"] if x["name"] != a["column"]]
            elif op == "add_row":
                table = _tbl(design, tname)
                names, vals = [], []
                for cname, raw in a["values"].items():
                    col = next(x for x in table["columns"] if x["name"] == cname)
                    if col["references"]:
                        parent = _tbl(design, col["references"].split(".")[0])
                        v = resolve_link(c, pg, parent, raw)
                        if v is None:
                            raise ValueError("linked record not found")
                    else:
                        v = convert_value(col["type"], raw)
                    names.append(sql.Identifier(cname))
                    vals.append(v)
                c.execute(sql.SQL("insert into {}.{} ({}) values ({})").format(
                    sql.Identifier(pg), sql.Identifier(tname), sql.SQL(", ").join(names),
                    sql.SQL(", ").join(sql.Placeholder() for _ in vals)), vals)
            elif op == "update_row":
                table = _tbl(design, tname)
                sets, vals = [], []
                for cname, raw in a["values"].items():
                    col = next(x for x in table["columns"] if x["name"] == cname)
                    if col["references"]:
                        v = resolve_link(c, pg, _tbl(design, col["references"].split(".")[0]), raw)
                        if v is None:
                            raise ValueError("linked record not found")
                    else:
                        v = convert_value(col["type"], raw)
                    sets.append(sql.SQL("{} = %s").format(sql.Identifier(cname)))
                    vals.append(v)
                c.execute(sql.SQL("update {}.{} set {} where id = any(%s)").format(
                    sql.Identifier(pg), sql.Identifier(tname), sql.SQL(", ").join(sets)), vals + [list(a["ids"])])
            elif op == "rename_table":
                new = a["new_name"]
                c.execute(sql.SQL("alter table {}.{} rename to {}").format(
                    sql.Identifier(pg), sql.Identifier(tname), sql.Identifier(new)))
                for t in design["tables"]:
                    if t["name"] == tname:
                        t["name"] = new
                    for col in t["columns"]:
                        if col["references"] and col["references"].split(".")[0] == tname:
                            col["references"] = new + ".id"
            elif op == "rename_column":
                new = a["new_name"]
                c.execute(sql.SQL("alter table {}.{} rename column {} to {}").format(
                    sql.Identifier(pg), sql.Identifier(tname), sql.Identifier(a["column"]), sql.Identifier(new)))
                for col in _tbl(design, tname)["columns"]:
                    if col["name"] == a["column"]:
                        col["name"] = new
            elif op == "add_relation":
                table = _tbl(design, tname)
                cname, parent = a["column"], a["references"]
                existing = next((x for x in table["columns"] if x["name"] == cname), None)
                if existing is None:
                    c.execute(sql.SQL("alter table {}.{} add column {} integer references {}.{} (id)").format(
                        sql.Identifier(pg), sql.Identifier(tname), sql.Identifier(cname),
                        sql.Identifier(pg), sql.Identifier(parent)))
                    table["columns"].append({"name": cname, "type": "integer", "required": False,
                                             "primary_key": False, "references": parent + ".id"})
                else:
                    c.execute(sql.SQL("alter table {}.{} add foreign key ({}) references {}.{} (id)").format(
                        sql.Identifier(pg), sql.Identifier(tname), sql.Identifier(cname),
                        sql.Identifier(pg), sql.Identifier(parent)))
                    existing["references"] = parent + ".id"
        c.execute("update datagenie_businesses set design=%s where id=%s", (json.dumps(design), biz_id))



def find_rows(biz, tname, col, value, limit=21):
    base = sql.SQL("select id from {}.{}").format(sql.Identifier(biz["pg_schema"]), sql.Identifier(tname))
    if col is None:
        q, params = base + sql.SQL(" order by id limit 2"), ()
    else:
        q = base + sql.SQL(" where lower({}::text) = lower(%s) order by id limit %s").format(sql.Identifier(col))
        params = (value, limit)
    with connect() as c:
        return [r[0] for r in c.execute(q, params).fetchall()]



def delete_business(biz_id):
    biz = get_business(biz_id)
    if not biz:
        return False
    with connect() as c:
        pg = biz["pg_schema"]
        if pg and pg.startswith("biz_"):
            c.execute(sql.SQL("drop schema if exists {} cascade").format(sql.Identifier(pg)))
        c.execute("delete from datagenie_businesses where id=%s", (biz_id,))
    return True
