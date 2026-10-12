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


def connect():
    return psycopg.connect(os.getenv("DATABASE_URL"), autocommit=False)


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
