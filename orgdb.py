import psycopg
from psycopg import sql
import db


def init_orgs():
    with db.connect() as c:
        c.execute("alter table datagenie_businesses add column if not exists org_type text")
        c.execute("alter table datagenie_businesses add column if not exists org_subtype text")
        c.execute("alter table datagenie_businesses add column if not exists listed boolean not null default false")
        c.execute("""create table if not exists datagenie_roles (
            id serial primary key,
            biz_id text not null,
            name text not null,
            access text not null default 'own',
            created_at timestamptz not null default now())""")
        c.execute("create unique index if not exists datagenie_roles_uq on datagenie_roles (biz_id, lower(name))")
        c.execute("""create table if not exists datagenie_members (
            id serial primary key,
            biz_id text not null,
            username text not null,
            password_hash text not null,
            display_name text not null,
            role_id integer not null references datagenie_roles(id) on delete cascade,
            record_table text,
            record_id integer,
            created_at timestamptz not null default now(),
            unique (biz_id, username))""")


def purge(biz_id):
    with db.connect() as c:
        c.execute("delete from datagenie_members where biz_id=%s", (biz_id,))
        c.execute("delete from datagenie_roles where biz_id=%s", (biz_id,))


def set_org_meta(biz_id, otype, sub, listed):
    with db.connect() as c:
        c.execute("update datagenie_businesses set org_type=%s, org_subtype=%s, listed=%s where id=%s",
                  (otype or None, sub or None, bool(listed), biz_id))


def org_meta(biz_id):
    with db.connect() as c:
        r = c.execute("select org_type, org_subtype, listed from datagenie_businesses where id=%s", (biz_id,)).fetchone()
    return {"type": r[0], "sub": r[1], "listed": r[2]} if r else {"type": None, "sub": None, "listed": False}


def type_counts():
    with db.connect() as c:
        rows = c.execute("select org_type, count(*) from datagenie_businesses "
                         "where status='active' and listed and org_type is not null group by org_type").fetchall()
    return {a: b for a, b in rows}


def sub_counts(tslug):
    with db.connect() as c:
        rows = c.execute("select org_subtype, count(*) from datagenie_businesses "
                         "where status='active' and listed and org_type=%s and org_subtype is not null group by org_subtype",
                         (tslug,)).fetchall()
    return {a: b for a, b in rows}


def list_public(tslug, sub, term):
    q = "select id, title, org_subtype from datagenie_businesses where status='active' and listed and org_type=%s"
    params = [tslug]
    if sub:
        q += " and org_subtype=%s"
        params.append(sub)
    term = (term or "").replace("%", "").replace("_", "").strip().lower()
    if term:
        q += " and lower(title) like %s"
        params.append("%" + term + "%")
    q += " order by title limit 200"
    with db.connect() as c:
        rows = c.execute(q, params).fetchall()
    return [{"id": r[0], "title": r[1], "sub": r[2]} for r in rows]


def list_roles(biz_id):
    with db.connect() as c:
        rows = c.execute("select r.id, r.name, r.access, "
                         "(select count(*) from datagenie_members m where m.role_id = r.id) "
                         "from datagenie_roles r where r.biz_id=%s order by r.name", (biz_id,)).fetchall()
    return [{"id": r[0], "name": r[1], "access": r[2], "members": r[3]} for r in rows]


def add_role(biz_id, name, access):
    try:
        with db.connect() as c:
            return c.execute("insert into datagenie_roles (biz_id, name, access) values (%s,%s,%s) returning id",
                             (biz_id, name, access)).fetchone()[0]
    except psycopg.errors.UniqueViolation:
        return None


def delete_role(biz_id, role_id):
    with db.connect() as c:
        c.execute("delete from datagenie_roles where id=%s and biz_id=%s", (role_id, biz_id))


def add_member(biz_id, username, pw_hash, display_name, role_id, rtable, rid):
    try:
        with db.connect() as c:
            return c.execute(
                "insert into datagenie_members (biz_id, username, password_hash, display_name, role_id, record_table, record_id) "
                "values (%s,%s,%s,%s,%s,%s,%s) returning id",
                (biz_id, username, pw_hash, display_name, role_id, rtable, rid)).fetchone()[0]
    except psycopg.errors.UniqueViolation:
        return None


def list_members(biz_id):
    with db.connect() as c:
        rows = c.execute("select m.id, m.username, m.display_name, r.name, r.access, m.record_table, m.record_id "
                         "from datagenie_members m join datagenie_roles r on r.id = m.role_id "
                         "where m.biz_id=%s order by r.name, m.display_name", (biz_id,)).fetchall()
    keys = ["id", "username", "display_name", "role", "access", "record_table", "record_id"]
    return [dict(zip(keys, r)) for r in rows]


def delete_member(biz_id, member_id):
    with db.connect() as c:
        c.execute("delete from datagenie_members where id=%s and biz_id=%s", (member_id, biz_id))


def set_member_password(biz_id, member_id, pw_hash):
    with db.connect() as c:
        cur = c.execute("update datagenie_members set password_hash=%s where id=%s and biz_id=%s",
                        (pw_hash, member_id, biz_id))
        return cur.rowcount


def member_for_login(biz_id, username, role_name):
    with db.connect() as c:
        r = c.execute("select m.id, m.password_hash, m.display_name from datagenie_members m "
                      "join datagenie_roles r on r.id = m.role_id "
                      "where m.biz_id=%s and m.username=%s and lower(r.name) = lower(%s)",
                      (biz_id, username, role_name)).fetchone()
    return {"id": r[0], "password_hash": r[1], "display_name": r[2]} if r else None


def get_member(member_id):
    with db.connect() as c:
        r = c.execute("select m.id, m.biz_id, m.display_name, r.name, r.access, m.record_table, m.record_id "
                      "from datagenie_members m join datagenie_roles r on r.id = m.role_id where m.id=%s",
                      (member_id,)).fetchone()
    if not r:
        return None
    return dict(zip(["id", "biz_id", "display_name", "role", "access", "record_table", "record_id"], r))


def record_labels(biz, tname, ids):
    table = db.get_table(biz, tname)
    if not table or not ids:
        return {}
    q = sql.SQL("select id, {} from {}.{} where id = any(%s)").format(
        sql.Identifier(db.label_col(table)), sql.Identifier(biz["pg_schema"]), sql.Identifier(tname))
    with db.connect() as c:
        return {r[0]: str(r[1]) for r in c.execute(q, (list(ids),)).fetchall()}


def portal_rows(biz, table, cols, rid, term, limit=200):
    colsql = sql.SQL(", ").join(sql.Identifier(c["name"]) for c in table["columns"])
    q = sql.SQL("select {} from {}.{}").format(colsql, sql.Identifier(biz["pg_schema"]), sql.Identifier(table["name"]))
    where, params = [], []
    if cols:
        where.append(sql.SQL("(") + sql.SQL(" or ").join(
            sql.SQL("{} = %s").format(sql.Identifier(c)) for c in cols) + sql.SQL(")"))
        params += [rid] * len(cols)
    if term:
        searchable = [c["name"] for c in table["columns"] if not c["references"]]
        where.append(sql.SQL("(") + sql.SQL(" or ").join(
            sql.SQL("{}::text ilike %s").format(sql.Identifier(n)) for n in searchable) + sql.SQL(")"))
        params += ["%" + term + "%"] * len(searchable)
    if where:
        q = q + sql.SQL(" where ") + sql.SQL(" and ").join(where)
    q = q + sql.SQL(" order by id desc limit %s")
    params.append(limit)
    with db.connect() as c:
        return c.execute(q, params).fetchall()


if not getattr(db, "_org_purge_installed", False):
    if hasattr(db, "delete_business"):
        _orig_biz = db.delete_business

        def _delete_business(biz_id):
            result = _orig_biz(biz_id)
            purge(biz_id)
            return result
        db.delete_business = _delete_business
    if hasattr(db, "delete_user"):
        _orig_user = db.delete_user

        def _delete_user(user_id):
            ids = [b["id"] for b in db.list_businesses(user_id)]
            result = _orig_user(user_id)
            for i in ids:
                purge(i)
            return result
        db.delete_user = _delete_user
    db._org_purge_installed = True
