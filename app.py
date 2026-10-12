import os, secrets
from functools import wraps
from flask import Flask, render_template, request, redirect, url_for, abort, flash, session
from werkzeug.security import generate_password_hash, check_password_hash
from designer import design
from validator import validate_schema
import db

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
        row = db.user_by_email(email)
        if row and check_password_hash(row[2], request.form.get("password") or ""):
            session.clear()
            session["uid"], session["email"] = row[0], row[1]
            return redirect(url_for("my"))
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


# ---------- final product ----------
@app.route("/view/<biz_id>")
@login_required
def view(biz_id):
    biz = active_biz(biz_id)
    table = db.get_table(biz, request.args.get("t", "")) or biz["design"]["tables"][0]
    q = (request.args.get("q") or "").strip()[:100]
    return render_template("view.html", biz=biz, table=table, q=q,
                           rows=db.search_rows(biz, table, q),
                           opts=db.fk_options(biz, table),
                           counts=db.row_counts(biz), totals=db.totals(biz))


if __name__ == "__main__":
    app.run(debug=True)
