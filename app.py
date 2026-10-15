import time
import os, re, json, glob, secrets
from functools import wraps
from flask import Flask, render_template, request, redirect, url_for, abort, flash, session, jsonify
from werkzeug.security import generate_password_hash, check_password_hash
from designer import design
from validator import validate_schema
import db
import genie
import askdata
import genie_ext

app = Flask(__name__)
app.secret_key = os.getenv("SECRET_KEY") or secrets.token_hex(32)
app.config.update(SESSION_COOKIE_HTTPONLY=True, SESSION_COOKIE_SAMESITE="Lax")

TYPE_LABELS = {"text": "Text", "integer": "Whole number", "numeric": "Amount",
               "boolean": "Yes / No", "date": "Date", "timestamp": "Date and time"}
INPUT = {"text": "text", "integer": "number", "numeric": "number",
         "date": "date", "timestamp": "datetime-local"}

app.jinja_env.filters["pretty"] = lambda n: n.replace("_", " ").title()
app.jinja_env.globals["TYPE_LABELS"] = TYPE_LABELS
app.jinja_env.globals["INPUT"] = INPUT

try:
    db.init_registry()
    db.init_users()
except Exception as e:
    print("Database not ready:", type(e).__name__)


def login_required(f):
    @wraps(f)
    def wrapper(*args, **kwargs):
        if not session.get("uid"):
            return redirect(url_for("login"))
        return f(*args, **kwargs)
    return wrapper


def owned(biz_id):
    """Returns the business only if the logged-in user owns it, otherwise 404."""
    biz = db.get_business(biz_id)
    if not biz or db.owner_of(biz_id) != session.get("uid"):
        abort(404)
    return biz


def active_biz(biz_id):
    biz = owned(biz_id)
    if biz["status"] != "active":
        abort(404)
    return biz


# ---------- security ----------
app.config["SESSION_COOKIE_SECURE"] = bool(os.getenv("RENDER"))
FAILS = {}


def too_many(key):
    now = time.time()
    FAILS[key] = [t for t in FAILS.get(key, []) if now - t < 600]
    return len(FAILS[key]) >= 5


def csrf_token():
    if "csrf" not in session:
        session["csrf"] = secrets.token_hex(16)
    return session["csrf"]


app.jinja_env.globals["csrf_token"] = csrf_token


@app.before_request
def csrf_protect():
    if request.method == "POST":
        sent = request.form.get("csrf_token") or request.headers.get("X-CSRF-Token") or ""
        if not secrets.compare_digest(sent, session.get("csrf", "")):
            abort(400)


@app.errorhandler(400)
def bad_request(e):
    return "Your session expired or the request was not valid. Go back, refresh the page and try again.", 400


@app.after_request
def security_headers(resp):
    resp.headers["X-Content-Type-Options"] = "nosniff"
    resp.headers["X-Frame-Options"] = "DENY"
    resp.headers["Referrer-Policy"] = "same-origin"
    resp.headers["Content-Security-Policy"] = (
        "default-src 'self'; script-src 'self' 'unsafe-inline'; style-src 'self' 'unsafe-inline'; "
        "img-src 'self' data:; frame-ancestors 'none'; form-action 'self'; base-uri 'self'")
    return resp


# ---------- accounts ----------
@app.route("/signup", methods=["GET", "POST"])
def signup():
    error = None
    if request.method == "POST":
        email = (request.form.get("email") or "").strip().lower()
        pw = request.form.get("password") or ""
        if "@" not in email or len(email) > 200:
            error = "Please enter a valid email."
        elif len(pw) < 8:
            error = "Password must be at least 8 characters."
        else:
            uid = db.create_user(email, generate_password_hash(pw))
            if uid is None:
                error = "That email is already registered. Try logging in."
            else:
                session.clear()
                session["uid"], session["email"] = uid, email
                return redirect(url_for("my"))
    return render_template("signup.html", error=error)


@app.route("/login", methods=["GET", "POST"])
def login():
    error = None
    if request.method == "POST":
        email = (request.form.get("email") or "").strip().lower()
        if too_many(email):
            return render_template("login.html", error="Too many failed attempts. Wait 10 minutes and try again.")
        row = db.user_by_email(email)
        if row and check_password_hash(row[2], request.form.get("password") or ""):
            session.clear()
            session["uid"], session["email"] = row[0], row[1]
            return redirect(url_for("my"))
        FAILS.setdefault(email, []).append(time.time())
        error = "Wrong email or password."
    return render_template("login.html", error=error)


@app.route("/logout")
def logout():
    session.clear()
    return redirect(url_for("login"))


@app.route("/my")
@login_required
def my():
    return render_template("my.html", items=db.list_businesses(session["uid"]))


# ---------- design ----------
@app.route("/")
@login_required
def home():
    return render_template("index.html", error=request.args.get("error"))


@app.route("/design", methods=["POST"])
@login_required
def make_design():
    description = (request.form.get("description") or "").strip()
    if len(description) < 10:
        return redirect(url_for("home", error="Please describe your business in a sentence or two."))
    schema, source, secs = design(description)
    if not schema:
        return redirect(url_for("home", error="Gemma took too long. Please try again."))
    ok, cleaned, errors = validate_schema(schema)
    if not ok:
        return redirect(url_for("home", error="Sorry, I could not design that. Try describing it differently."))
    biz_id = db.save_pending(description, cleaned)
    db.set_owner(biz_id, session["uid"])
    return redirect(url_for("review", biz_id=biz_id, src=source, t=secs))


@app.route("/review/<biz_id>")
@login_required
def review(biz_id):
    biz = owned(biz_id)
    if biz["status"] == "active":
        return redirect(url_for("dashboard", biz_id=biz_id))
    return render_template("review.html", biz=biz, src=request.args.get("src"), secs=request.args.get("t"))


@app.route("/approve/<biz_id>", methods=["POST"])
@login_required
def approve(biz_id):
    biz = owned(biz_id)
    try:
        db.create_tables(biz_id)
    except Exception as e:
        print("Create failed:", type(e).__name__, e)
        return render_template("review.html", biz=biz, src=None, secs=None,
                               error="Something went wrong while building your database. Please try again."), 500
    return redirect(url_for("dashboard", biz_id=biz_id))


# ---------- manage ----------
@app.route("/dashboard/<biz_id>")
@login_required
def dashboard(biz_id):
    biz = active_biz(biz_id)
    return render_template("dashboard.html", biz=biz, counts=db.row_counts(biz), current=None)


@app.route("/dashboard/<biz_id>/<tname>")
@login_required
def table_page(biz_id, tname):
    biz = active_biz(biz_id)
    table = db.get_table(biz, tname) or abort(404)
    return render_template("table.html", biz=biz, table=table, current=tname,
                           rows=db.list_rows(biz, table), opts=db.fk_options(biz, table))


@app.route("/dashboard/<biz_id>/<tname>/add", methods=["POST"])
@login_required
def add_record(biz_id, tname):
    biz = active_biz(biz_id)
    table = db.get_table(biz, tname) or abort(404)
    errors = db.insert_row(biz, table, request.form)
    if errors:
        for e in errors:
            flash(e, "err")
    else:
        flash("Saved.", "ok")
    return redirect(url_for("table_page", biz_id=biz_id, tname=tname))


@app.route("/dashboard/<biz_id>/<tname>/delete/<int:row_id>", methods=["POST"])
@login_required
def delete_record(biz_id, tname, row_id):
    biz = active_biz(biz_id)
    table = db.get_table(biz, tname) or abort(404)
    err = db.delete_row(biz, table, row_id)
    flash(err or "Deleted.", "err" if err else "ok")
    return redirect(url_for("table_page", biz_id=biz_id, tname=tname))


CURRENCIES = {"USD": "$", "LRD": "L$", "EUR": "EUR ", "GBP": "GBP ", "NGN": "NGN ", "GHS": "GHS "}


def money(v, cur="USD"):
    return f"{CURRENCIES.get(cur, '')}{float(v):,.2f}"


app.jinja_env.globals["money"] = money
app.jinja_env.globals["CURRENCIES"] = CURRENCIES


def bars(items):
    top = max([float(i["value"]) for i in items] or [0]) or 1
    return [{"label": i["label"], "value": i["value"],
             "pct": max(2, round(float(i["value"]) / top * 100)), "money": i.get("money", False)} for i in items]


def count_bars(biz, counts):
    return bars([{"label": t["name"].replace("_", " ").title(), "value": counts.get(t["name"], 0)}
                 for t in biz["design"]["tables"]])


app.jinja_env.globals["bars"] = bars
app.jinja_env.globals["count_bars"] = count_bars


def csv_safe(v):
    v = "" if v is None else str(v)
    return "'" + v if v[:1] in ("=", "+", "-", "@") else v


@app.route("/export/<biz_id>/<table>.csv")
@login_required
def export_csv(biz_id, table):
    import csv, io
    from flask import abort, Response
    biz = active_biz(biz_id)
    t = db.get_table(biz, table)
    if not t:
        abort(404)
    rows = db.search_rows(biz, t, "", limit=50000)
    opts = db.fk_options(biz, t)
    buf = io.StringIO()
    w = csv.writer(buf)
    w.writerow(["#" if c["primary_key"] else c["name"].replace("_", " ").title() for c in t["columns"]])
    for r in rows:
        out = []
        for i, c in enumerate(t["columns"]):
            v = r[i]
            if c["type"] == "boolean" and v is not None:
                v = "Yes" if v else "No"
            elif c["references"] and v is not None:
                v = opts.get(c["name"], {}).get(v, v)
            out.append(csv_safe(v))
        w.writerow(out)
    name = "".join(ch if ch.isalnum() else "_" for ch in f"{biz['title']}_{t['name']}")[:60]
    return Response("\ufeff" + buf.getvalue(), mimetype="text/csv; charset=utf-8",
                    headers={"Content-Disposition": f"attachment; filename={name}.csv"})


# ---------- final product ----------
@app.route("/view/<biz_id>")
@login_required
def view(biz_id):
    biz = active_biz(biz_id)
    table = db.get_table(biz, request.args.get("t", "")) or biz["design"]["tables"][0]
    q = (request.args.get("q") or "").strip()[:100]
    _sh = share_info(biz_id)
    return render_template("view.html", biz=biz, table=table, q=q,
                           rows=db.search_rows(biz, table, q),
                           opts=db.fk_options(biz, table),
                           counts=db.row_counts(biz), totals=db.totals(biz),
                           currency=db.get_currency(biz_id),
                           share_url=_sh[0], share_qr=_sh[1])


@app.route("/currency/<biz_id>", methods=["POST"])
@login_required
def set_currency_route(biz_id):
    biz = active_biz(biz_id)
    code = request.form.get("currency", "")
    if code in CURRENCIES:
        db.set_currency(biz["id"], code)
    return redirect(request.referrer or url_for("view", biz_id=biz["id"]))


def share_info(biz_id):
    token = db.get_share(biz_id)
    if not token:
        return None, None
    base = request.host_url.rstrip("/")
    if os.environ.get("RENDER"):
        base = base.replace("http://", "https://")
    link = base + url_for("public_view", token=token)
    try:
        import segno
        qr = segno.make(link, error="m").svg_inline(scale=5, dark="#0b1f44", border=2)
    except Exception:
        qr = None
    return link, qr


@app.route("/share/<biz_id>", methods=["POST"])
@login_required
def share_toggle(biz_id):
    biz = active_biz(biz_id)
    db.set_share(biz["id"], request.form.get("action") == "on")
    return redirect(url_for("view", biz_id=biz["id"]))


@app.route("/s/<token>")
def public_view(token):
    from flask import abort, make_response
    biz = db.biz_by_token(token)
    if not biz:
        abort(404)
    table = db.get_table(biz, request.args.get("t", "")) or biz["design"]["tables"][0]
    q = (request.args.get("q") or "").strip()[:100]
    resp = make_response(render_template("public.html", biz=biz, table=table, q=q, token=token,
                                         rows=db.search_rows(biz, table, q),
                                         opts=db.fk_options(biz, table),
                                         counts=db.row_counts(biz), totals=db.totals(biz),
                                         currency=db.get_currency(biz["id"])))
    resp.headers["X-Robots-Tag"] = "noindex, nofollow"
    return resp


@app.route("/excel/<biz_id>.xlsx")
@login_required
def export_xlsx(biz_id):
    from flask import Response
    import xlsx_export
    biz = active_biz(biz_id)
    cur = db.get_currency(biz["id"])
    data = xlsx_export.build(biz, CURRENCIES.get(cur, ""), cur)
    name = "".join(ch if ch.isalnum() else "_" for ch in biz["title"])[:60]
    return Response(data, mimetype="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
                    headers={"Content-Disposition": f"attachment; filename={name}.xlsx"})


app.config.setdefault("MAX_CONTENT_LENGTH", 3 * 1024 * 1024)


@app.route("/import", methods=["GET", "POST"])
@login_required
def import_data():
    import xlsx_import, validator
    if request.method == "GET":
        return render_template("import.html", error=None)
    f = request.files.get("file")
    name = (f.filename or "") if f else ""
    if not f or not name.lower().endswith((".xlsx", ".csv")):
        return render_template("import.html", error="Please choose an .xlsx or .csv file.")
    try:
        schema, rows = xlsx_import.build(f.read(), name)
    except ValueError as e:
        return render_template("import.html", error=str(e))
    except Exception:
        return render_template("import.html", error="That file could not be read. Try saving it again as .xlsx.")
    ok, cleaned, errors = validator.validate_schema(schema)
    if not ok or not cleaned.get("tables"):
        return render_template("import.html", error="That file could not be turned into tables.")
    data = {}
    for t in cleaned["tables"]:
        cols = [c["name"] for c in t["columns"] if not c["primary_key"]]
        data[t["name"]] = {"cols": cols, "rows": [[r.get(c) for c in cols] for r in rows.get(t["name"], [])]}
    biz_id = db.save_import(name, cleaned, data)
    db.set_owner(biz_id, session["uid"])
    return redirect(url_for("review", biz_id=biz_id))


@app.route("/manifest.json")
def manifest():
    from flask import jsonify
    r = jsonify({
        "name": "DataGenie", "short_name": "DataGenie",
        "description": "Describe your business. Let AI build your database.",
        "start_url": "/", "scope": "/", "display": "standalone",
        "background_color": "#f4f8ff", "theme_color": "#0b1f44",
        "icons": [
            {"src": "/static/icon-192.png", "sizes": "192x192", "type": "image/png", "purpose": "any"},
            {"src": "/static/icon-512.png", "sizes": "512x512", "type": "image/png", "purpose": "any"},
            {"src": "/static/icon-512.png", "sizes": "512x512", "type": "image/png", "purpose": "maskable"}]})
    r.mimetype = "application/manifest+json"
    return r


SW_JS = """self.addEventListener('install', function (e) { self.skipWaiting(); });
self.addEventListener('activate', function (e) { e.waitUntil(self.clients.claim()); });
self.addEventListener('fetch', function (e) {
  if (e.request.mode !== 'navigate') return;
  e.respondWith(fetch(e.request).catch(function () {
    return new Response('<!doctype html><meta name="viewport" content="width=device-width,initial-scale=1"><body style="font-family:Segoe UI,Arial,sans-serif;background:#f4f8ff;color:#0b1f44;text-align:center;padding:60px 20px"><h1>You are offline</h1><p>DataGenie needs an internet connection. Please reconnect and try again.</p></body>', {headers: {'Content-Type': 'text/html; charset=utf-8'}});
  }));
});
"""


@app.route("/sw.js")
def service_worker():
    from flask import Response
    r = Response(SW_JS, mimetype="application/javascript")
    r.headers["Cache-Control"] = "no-cache"
    r.headers["Service-Worker-Allowed"] = "/"
    return r


# ---------- database suggestions ----------
def load_suggestions():
    items = []
    for p in glob.glob("blueprints/*.json"):
        b = json.load(open(p, encoding="utf-8"))
        slug = os.path.basename(p)[:-5]
        items.append({"slug": slug, "title": b.get("title", slug.replace("_", " ").title()),
                      "icon": b.get("icon", "🗄️"), "category": b.get("category", "Other"),
                      "description": b.get("description", ""),
                      "tables": [t["name"] for t in b["tables"]]})
    return sorted(items, key=lambda i: i["title"])


@app.route("/suggestions")
@login_required
def suggestions():
    items = load_suggestions()
    return render_template("suggestions.html", items=items, cats=sorted({i["category"] for i in items}))


@app.route("/use/<slug>", methods=["POST"])
@login_required
def use_suggestion(slug):
    if not re.fullmatch(r"[a-z0-9_]{1,40}", slug) or not os.path.exists(f"blueprints/{slug}.json"):
        abort(404)
    schema = json.load(open(f"blueprints/{slug}.json", encoding="utf-8"))
    ok, cleaned, errors = validate_schema(schema)
    if not ok:
        abort(500)
    cleaned["name"] = schema.get("title", slug)
    biz_id = db.save_pending("Ready-made design: " + cleaned["name"], cleaned)
    db.set_owner(biz_id, session["uid"])
    return redirect(url_for("review", biz_id=biz_id, src="template"))


@app.route("/genie/<biz_id>/plan", methods=["POST"])
@login_required
def genie_plan(biz_id):
    biz = active_biz(biz_id)
    data = request.get_json(silent=True) or {}
    message = str(data.get("message", "")).strip()[:600]
    genie_ext.set_history(data.get("history"))
    if len(message) < 3:
        return jsonify(error="Tell me what you would like to change.")
    result = askdata.answer(biz, message) if askdata.is_question(message) else genie.plan(biz, message)
    if result is None:
        return jsonify(error="GenieAI took too long. Try a shorter request, or use the instant ones like: Delete the X table.")
    return jsonify(result)


@app.route("/genie/<biz_id>/apply", methods=["POST"])
@login_required
def genie_apply(biz_id):
    biz = active_biz(biz_id)
    data = request.get_json(silent=True) or {}
    actions, errors, _ = genie.check(biz, data.get("actions"))
    if errors or not actions:
        return jsonify(ok=False, error=" ".join(errors) or "Nothing to apply.")
    try:
        db.apply_actions(biz_id, actions)
    except Exception as e:
        print("GenieAI apply failed:", type(e).__name__, e)
        msg = ("Some records are connected to other records, so nothing was changed. Remove the connected records first."
               if "ForeignKey" in type(e).__name__ else "Could not apply the changes. Nothing was changed.")
        return jsonify(ok=False, error=msg)
    fresh = db.get_business(biz_id)
    names = {t["name"] for t in fresh["design"]["tables"]}
    target = next((x["table"] for x in actions if x["table"] in names), None)
    goto = url_for("table_page", biz_id=biz_id, tname=target) if target else url_for("dashboard", biz_id=biz_id)
    return jsonify(ok=True, goto=goto)


@app.route("/delete/<biz_id>", methods=["GET", "POST"])
@login_required
def delete_database(biz_id):
    biz = owned(biz_id)
    error = None
    if request.method == "POST":
        typed = (request.form.get("confirm") or "").strip().lower()
        if typed != biz["title"].strip().lower():
            error = "The name you typed does not match. Nothing was deleted."
        else:
            try:
                db.delete_business(biz_id)
            except Exception as e:
                print("Delete failed:", type(e).__name__, e)
                error = "Could not delete this database. Please try again."
            else:
                flash("Database deleted.", "ok")
                return redirect(url_for("my"))
    count = 0
    if biz["status"] == "active":
        try:
            count = sum(db.row_counts(biz).values())
        except Exception:
            count = 0
    return render_template("delete.html", biz=biz, error=error, count=count)


@app.route("/account/delete", methods=["GET", "POST"])
@login_required
def delete_account():
    error = None
    if request.method == "POST":
        typed = (request.form.get("confirm") or "").strip().lower()
        if typed != (session.get("email") or "").lower():
            error = "The email you typed does not match. Nothing was deleted."
        else:
            try:
                db.delete_user(session["uid"])
            except Exception as e:
                print("Account delete failed:", type(e).__name__, e)
                error = "Could not delete your account. Please try again."
            else:
                session.clear()
                return redirect(url_for("signup"))
    try:
        dbs = len(db.list_businesses(session["uid"]))
    except Exception:
        dbs = 0
    return render_template("account_delete.html", error=error, dbs=dbs)


if __name__ == "__main__":
    app.run(debug=True)
