from decimal import Decimal, InvalidOperation
from psycopg import sql
import db

I = sql.Identifier
SQ = sql.SQL


def _ref(c):
    r = c.get("references")
    return r.split(".")[0] if r else None


def _fk(table, parent):
    for c in table["columns"]:
        if _ref(c) == parent:
            return c["name"]
    return None


def _tmap(biz):
    return {t["name"]: t for t in biz["design"]["tables"]}


def teacher_info(biz, m):
    if m.get("record_table") != "teachers" or m.get("record_id") is None:
        return None
    T = _tmap(biz)
    if not all(n in T for n in ("teachers", "classes", "students")):
        return None
    ct = _fk(T["classes"], "teachers")
    sc = _fk(T["students"], "classes")
    if not ct or not sc:
        return None
    return {"ct": ct, "sc": sc}


def _classes(S, info):
    return SQ("select id from {}.{} where {} = %s").format(I(S), I("classes"), I(info["ct"]))


def _students(S, info):
    return SQ("select id from {}.{} where {} in (").format(I(S), I("students"), I(info["sc"])) + _classes(S, info) + SQ(")")


def rules(biz, m, info):
    S, rid = biz["pg_schema"], m["record_id"]
    cls, stu = _classes(S, info), _students(S, info)
    out = []
    for t in biz["design"]["tables"]:
        n = t["name"]
        if n == "teachers":
            out.append((t, SQ("id = %s"), [rid]))
        elif n == "classes":
            out.append((t, SQ("{} = %s").format(I(info["ct"])), [rid]))
        elif n == "students":
            out.append((t, SQ("{} in (").format(I(info["sc"])) + cls + SQ(")"), [rid]))
        elif "fee" in n or "payment" in n:
            continue
        else:
            col = _fk(t, "students")
            if col:
                out.append((t, SQ("{} in (").format(I(col)) + stu + SQ(")"), [rid]))
            elif _fk(t, "teachers"):
                out.append((t, SQ("{} = %s").format(I(_fk(t, "teachers"))), [rid]))
    return out


def rows(biz, table, scoped, term, limit=200):
    _, where, params = scoped
    params = list(params)
    colsql = SQ(", ").join(I(c["name"]) for c in table["columns"])
    q = SQ("select {} from {}.{} where (").format(colsql, I(biz["pg_schema"]), I(table["name"])) + where + SQ(")")
    if term:
        searchable = [c["name"] for c in table["columns"] if not c["references"]]
        if searchable:
            q = q + SQ(" and (") + SQ(" or ").join(
                SQ("{}::text ilike %s").format(I(n)) for n in searchable) + SQ(")")
            params += ["%" + term + "%"] * len(searchable)
    q = q + SQ(" order by id desc limit %s")
    params.append(limit)
    with db.connect() as c:
        return c.execute(q, params).fetchall()


# ---------- grades (teacher writes) ----------
def _gcols(biz):
    r = _tmap(biz).get("results")
    if not r:
        return None
    stu = _fk(r, "students")
    sub = _fk(r, "subjects")
    term = "term" if any(c["name"] == "term" for c in r["columns"]) else None
    score = next((c["name"] for c in r["columns"]
                  if c["type"] in ("numeric", "integer") and not c["references"] and c["name"] != "id"
                  and any(k in c["name"] for k in ("score", "mark", "grade"))), None)
    if not stu or not score:
        return None
    return r, stu, sub, term, score


def my_students(biz, m, info):
    S = biz["pg_schema"]
    lab = db.label_col(db.get_table(biz, "students"))
    q = SQ("select id, {} from {}.{} where {} in (").format(I(lab), I(S), I("students"), I(info["sc"])) \
        + _classes(S, info) + SQ(") order by {}, id").format(I(lab))
    with db.connect() as c:
        return [(r[0], str(r[1])) for r in c.execute(q, [m["record_id"]]).fetchall()]


def my_subjects(biz, m):
    T = _tmap(biz)
    if "subjects" not in T:
        return []
    S = biz["pg_schema"]
    lab = db.label_col(db.get_table(biz, "subjects"))
    tcol = _fk(T["subjects"], "teachers")
    base = SQ("select id, {} from {}.{}").format(I(lab), I(S), I("subjects"))
    with db.connect() as c:
        out = []
        if tcol:
            out = c.execute(base + SQ(" where {} = %s order by {}").format(I(tcol), I(lab)), [m["record_id"]]).fetchall()
        if not out:
            out = c.execute(base + SQ(" order by {}").format(I(lab))).fetchall()
    return [(r[0], str(r[1])) for r in out]


def save_grade(biz, m, info, form):
    g = _gcols(biz)
    if not g:
        raise ValueError("This database has no results table to save grades in.")
    r, scol, subcol, tcol, ncol = g
    S = biz["pg_schema"]
    try:
        sid = int(form.get("student_id", ""))
    except ValueError:
        raise ValueError("Choose a student.")
    allowed = [s[0] for s in my_students(biz, m, info)]
    if sid not in allowed:
        raise ValueError("That student is not in your class.")
    try:
        score = Decimal(str(form.get("score", "")).strip())
    except InvalidOperation:
        raise ValueError("Enter the score as a number.")
    if not score.is_finite() or score < 0 or score > 1000:
        raise ValueError("The score must be a number between 0 and 1000.")
    cols, vals = [ncol], [score]
    if subcol:
        try:
            sub = int(form.get("subject_id", ""))
        except ValueError:
            raise ValueError("Choose a subject.")
        if sub not in [s[0] for s in my_subjects(biz, m)]:
            raise ValueError("Choose one of your subjects.")
        cols.append(subcol); vals.append(sub)
    if tcol:
        cols.append(tcol); vals.append((form.get("term") or "").strip()[:60])
    rid = (form.get("id") or "").strip()
    with db.connect() as c:
        if rid:
            q = SQ("update {}.{} set ").format(I(S), I(r["name"])) \
                + SQ(", ").join(SQ("{} = %s").format(I(x)) for x in cols) \
                + SQ(" where id = %s and {} = any(%s)").format(I(scol))
            cur = c.execute(q, vals + [int(rid), allowed])
            if cur.rowcount == 0:
                raise ValueError("You can only edit grades of your own students.")
        else:
            cols.append(scol); vals.append(sid)
            q = SQ("insert into {}.{} ({}) values ({})").format(
                I(S), I(r["name"]), SQ(", ").join(I(x) for x in cols), SQ(", ").join(SQ("%s") for _ in cols))
            c.execute(q, vals)


def grade_page(biz, m, info):
    g = _gcols(biz)
    students, subjects = my_students(biz, m, info), my_subjects(biz, m)
    out = {"students": students, "subjects": subjects, "rows": [], "has_subject": False,
           "has_term": False, "ready": bool(g)}
    if not g:
        return out
    r, scol, subcol, tcol, ncol = g
    out["has_subject"], out["has_term"] = bool(subcol), bool(tcol)
    S = biz["pg_schema"]
    sel = SQ(", ").join([I("id"), I(scol), I(subcol) if subcol else SQ("null"),
                         I(tcol) if tcol else SQ("null"), I(ncol)])
    q = SQ("select {} from {}.{} where {} in (").format(sel, I(S), I(r["name"]), I(scol)) \
        + _students(S, info) + SQ(") order by id desc limit 100")
    with db.connect() as c:
        data = c.execute(q, [m["record_id"]]).fetchall()
    sn, sb = dict(students), dict(subjects)
    out["rows"] = [{"id": d[0], "student_id": d[1], "student": sn.get(d[1], "#%s" % d[1]),
                    "subject_id": d[2], "subject": sb.get(d[2], "" if d[2] is None else "#%s" % d[2]),
                    "term": d[3] or "", "score": d[4]} for d in data]
    return out
