import os
from flask import Flask, render_template, request, redirect, url_for, abort, flash
from designer import design
from validator import validate_schema
import db

app = Flask(__name__)
app.secret_key = os.getenv("SECRET_KEY", "dev-secret-change-me")

TYPE_LABELS = {"text": "Text", "integer": "Whole number", "numeric": "Amount",
               "boolean": "Yes / No", "date": "Date", "timestamp": "Date and time"}
INPUT = {"text": "text", "integer": "number", "numeric": "number",
         "date": "date", "timestamp": "datetime-local"}

app.jinja_env.filters["pretty"] = lambda n: n.replace("_", " ").title()
app.jinja_env.globals["TYPE_LABELS"] = TYPE_LABELS
app.jinja_env.globals["INPUT"] = INPUT

try:
    db.init_registry()
except Exception as e:
    print("Database not ready:", type(e).__name__)


def active_biz(biz_id):
    biz = db.get_business(biz_id)
    if not biz or biz["status"] != "active":
        abort(404)
    return biz


@app.route("/")
def home():
    return render_template("index.html", error=request.args.get("error"))


@app.route("/design", methods=["POST"])
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
    return redirect(url_for("review", biz_id=biz_id, src=source, t=secs))


@app.route("/review/<biz_id>")
def review(biz_id):
    biz = db.get_business(biz_id)
    if not biz:
        abort(404)
    if biz["status"] == "active":
        return redirect(url_for("dashboard", biz_id=biz_id))
    return render_template("review.html", biz=biz, src=request.args.get("src"), secs=request.args.get("t"))


@app.route("/approve/<biz_id>", methods=["POST"])
def approve(biz_id):
    biz = db.get_business(biz_id)
    if not biz:
        abort(404)
    try:
        db.create_tables(biz_id)
    except Exception as e:
        print("Create failed:", type(e).__name__, e)
        return render_template("review.html", biz=biz, src=None, secs=None,
                               error="Something went wrong while building your database. Please try again."), 500
    return redirect(url_for("dashboard", biz_id=biz_id))


@app.route("/dashboard/<biz_id>")
def dashboard(biz_id):
    biz = active_biz(biz_id)
    return render_template("dashboard.html", biz=biz, counts=db.row_counts(biz), current=None)


@app.route("/dashboard/<biz_id>/<tname>")
def table_page(biz_id, tname):
    biz = active_biz(biz_id)
    table = db.get_table(biz, tname) or abort(404)
    return render_template("table.html", biz=biz, table=table, current=tname,
                           rows=db.list_rows(biz, table), opts=db.fk_options(biz, table))


@app.route("/dashboard/<biz_id>/<tname>/add", methods=["POST"])
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
def delete_record(biz_id, tname, row_id):
    biz = active_biz(biz_id)
    table = db.get_table(biz, tname) or abort(404)
    err = db.delete_row(biz, table, row_id)
    flash(err or "Deleted.", "err" if err else "ok")
    return redirect(url_for("table_page", biz_id=biz_id, tname=tname))


@app.route("/view/<biz_id>")
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
