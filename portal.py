import re, secrets, time
from functools import wraps
from flask import Blueprint, render_template, request, redirect, url_for, abort, flash, session
from werkzeug.security import generate_password_hash, check_password_hash
import db
import orgdb

bp = Blueprint("org", __name__)

ORG_TYPES = {
    "school": ("Schools", "Primary and high schools, universities and training centers",
               [("high_school", "High School"), ("university", "University"), ("trade_school", "Trade School"),
                ("primary_school", "Primary School"), ("nursery", "Nursery and Daycare")]),
    "health": ("Hospitals and Clinics", "Hospitals, clinics and laboratories",
               [("hospital", "Hospital"), ("clinic", "Clinic"), ("laboratory", "Laboratory")]),
    "pharmacy": ("Pharmacies", "Drug stores and chemists", []),
    "hotel": ("Hotels", "Hotels, guest houses and lodges",
              [("hotel", "Hotel"), ("guest_house", "Guest House"), ("lodge", "Lodge")]),
    "retail": ("Shops and Supermarkets", "Retail and wholesale businesses",
               [("supermarket", "Supermarket"), ("boutique", "Boutique"), ("hardware", "Hardware Store"),
                ("wholesale", "Wholesale")]),
    "food": ("Restaurants and Cafes", "Restaurants, cafes and bakeries", []),
    "farm": ("Farms", "Crop, poultry and fishing businesses",
             [("crop", "Crop Farm"), ("poultry", "Poultry"), ("fishing", "Fishing")]),
    "community": ("Churches and NGOs", "Churches, charities and community groups", []),
    "other": ("Other", "Any other kind of organization", []),
}
ROLE_HINTS = {
    "school": ["Student", "Teacher", "Parent", "Bursar"],
    "health": ["Doctor", "Nurse", "Patient", "Pharmacist"],
    "pharmacy": ["Pharmacist", "Cashier", "Customer"],
    "hotel": ["Receptionist", "Manager", "Guest"],
    "retail": ["Cashier", "Manager", "Customer"],
    "food": ["Waiter", "Chef", "Manager"],
    "farm": ["Worker", "Manager", "Buyer"],
    "community": ["Member", "Leader", "Volunteer"],
    "default": ["Staff", "Member", "Manager"],
}
ACCESS = {"own": "Sees only their own records", "view_all": "Can view everything (read only)"}
USER_RE = re.compile(r"^[a-z0-9][a-z0-9._-]{2,29}$")
ROLE_RE = re.compile(r"^[A-Za-z][A-Za-z0-9 ]{1,39}$")
FAILS = {}

try:
    orgdb.init_orgs()
except Exception as e:
    print("Organizations not ready:", type(e).__name__)


def pretty(n):
    return str(n).replace("_", " ").title()


def too_many(key):
    now = time.time()
    FAILS[key] = [t for t in FAILS.get(key, []) if now - t < 600]
    return len(FAILS[key]) >= 5


def gen_password():
    return "".join(secrets.choice("abcdefghjkmnpqrstuvwxyz23456789") for _ in range(8))


def admin_biz(f):
    @wraps(f)
    def wrapper(biz_id, *a, **k):
        if not session.get("uid"):
            return redirect(url_for("login"))
        biz = db.get_business(biz_id)
        if not biz or db.owner_of(biz_id) != session["uid"] or biz["status"] != "active":
            abort(404)
        return f(biz, *a, **k)
    return wrapper


def public_biz(biz_id):
    biz = db.get_business(biz_id)
    if not biz or biz["status"] != "active":
        abort(404)
    return biz


# ---------- finding an organization ----------
@bp.route("/find")
def find():
    counts = orgdb.type_counts()
    types = [{"slug": s, "label": v[0], "desc": v[1], "count": counts.get(s, 0)} for s, v in ORG_TYPES.items()]
    return render_template("find.html", mode="types", types=types)


@bp.route("/find/<tslug>")
def find_type(tslug):
    t = ORG_TYPES.get(tslug)
    if not t:
        abort(404)
    sub = request.args.get("sub", "")
    term = (request.args.get("q") or "").strip()[:60]
    subs = t[2]
    if subs and not sub:
        counts = orgdb.sub_counts(tslug)
        return render_template("find.html", mode="subs", tslug=tslug, label=t[0],
                               subs=[{"slug": s, "label": l, "count": counts.get(s, 0)} for s, l in subs],
                               total=sum(counts.values()))
    sub_label = dict(subs).get(sub) if sub and sub != "all" else None
    if sub and sub != "all" and sub_label is None:
        abort(404)
    orgs = orgdb.list_public(tslug, sub if sub_label else None, term)
    return render_template("find.html", mode="orgs", tslug=tslug, label=t[0], sub=sub, sub_label=sub_label,
                           q=term, orgs=orgs, labels=dict(subs), has_subs=bool(subs))


@bp.route("/o/<biz_id>")
def org_page(biz_id):
    biz = public_biz(biz_id)
    meta = orgdb.org_meta(biz_id)
    t = ORG_TYPES.get(meta["type"])
    sub = dict(t[2]).get(meta["sub"]) if t else None
    return render_template("org.html", biz=biz, tlabel=t[0] if t else None, sub=sub,
                           roles=[r["name"] for r in orgdb.list_roles(biz_id)])


# ---------- member sign in ----------
@bp.route("/o/<biz_id>/login", methods=["GET", "POST"])
def member_login(biz_id):
    biz = public_biz(biz_id)
    wanted = (request.values.get("role") or "").strip()[:60]
    roles = {r["name"].lower(): r["name"] for r in orgdb.list_roles(biz_id)}
    if wanted.lower() not in roles:
        return redirect(url_for("org.org_page", biz_id=biz_id))
    error = None
    if request.method == "POST":
        username = (request.form.get("username") or "").strip().lower()
        key = biz_id + ":" + username
        if too_many(key):
            error = "Too many failed attempts. Wait 10 minutes and try again."
        else:
            row = orgdb.member_for_login(biz_id, username, wanted)
            if row and check_password_hash(row["password_hash"], request.form.get("password") or ""):
                session.clear()
                session["mid"], session["mbiz"], session["mname"] = row["id"], biz_id, row["display_name"]
                return redirect(url_for("org.portal", biz_id=biz_id))
            FAILS.setdefault(key, []).append(time.time())
            error = "Wrong username or password for this role."
    return render_template("o_login.html", biz=biz, role=roles[wanted.lower()], error=error)


@bp.route("/o/<biz_id>/logout")
def member_logout(biz_id):
    session.clear()
    return redirect(url_for("org.org_page", biz_id=biz_id))


def current_member(biz_id):
    mid = session.get("mid")
    if not mid or session.get("mbiz") != biz_id:
        return None
    m = orgdb.get_member(mid)
    return m if m and m["biz_id"] == biz_id else None


def visible(biz, m):
    tables = biz["design"]["tables"]
    if m["access"] == "view_all":
        return [(t, None) for t in tables]
    rt, rid = m["record_table"], m["record_id"]
    if not rt or rid is None:
        return []
    out = []
    for t in tables:
        if t["name"] == rt:
            out.append((t, ["id"]))
        else:
            cols = [c["name"] for c in t["columns"] if c["references"] and c["references"].split(".")[0] == rt]
            if cols:
                out.append((t, cols))
    return out


@bp.route("/o/<biz_id>/portal")
def portal(biz_id):
    m = current_member(biz_id)
    if not m:
        return redirect(url_for("org.org_page", biz_id=biz_id))
    biz = public_biz(biz_id)
    vis = visible(biz, m)
    if not vis:
        return render_template("portal.html", biz=biz, m=m, tabs=[], table=None, rows=[], q="", opts={})
    names = [t["name"] for t, _ in vis]
    tname = request.args.get("t", "")
    if tname not in names:
        tname = names[0]
    table, cols = next((t, c) for t, c in vis if t["name"] == tname)
    term = (request.args.get("q") or "").strip()[:100]
    rows = orgdb.portal_rows(biz, table, cols, m["record_id"], term)
    return render_template("portal.html", biz=biz, m=m, tabs=names, table=table, rows=rows, q=term,
                           opts=db.fk_options(biz, table))


# ---------- admin: settings, roles and members ----------
@bp.route("/org/<biz_id>/settings", methods=["GET", "POST"])
@admin_biz
def settings_page(biz):
    if request.method == "POST":
        otype = request.form.get("org_type", "")
        sub = request.form.get("org_subtype", "")
        if otype and otype not in ORG_TYPES:
            otype = ""
        if sub not in dict(ORG_TYPES[otype][2] if otype else []):
            sub = ""
        listed = request.form.get("listed") is not None
        if listed and not otype:
            flash("Choose a type before listing your organization.", "err")
        else:
            orgdb.set_org_meta(biz["id"], otype, sub, listed)
            flash("Settings saved.", "ok")
        return redirect(url_for("org.settings_page", biz_id=biz["id"]))
    return render_template("settings.html", biz=biz, current="__settings", types=ORG_TYPES,
                           meta=orgdb.org_meta(biz["id"]))


@bp.route("/org/<biz_id>/members")
@admin_biz
def members_page(biz):
    members = orgdb.list_members(biz["id"])
    by = {}
    for m in members:
        if m["record_table"] and m["record_id"] is not None:
            by.setdefault(m["record_table"], []).append(m["record_id"])
    labels = {tn: orgdb.record_labels(biz, tn, ids) for tn, ids in by.items()}
    meta = orgdb.org_meta(biz["id"])
    return render_template("members.html", biz=biz, current="__members", roles=orgdb.list_roles(biz["id"]),
                           members=members, labels=labels, access=ACCESS,
                           hints=ROLE_HINTS.get(meta["type"], ROLE_HINTS["default"]),
                           link=url_for("org.org_page", biz_id=biz["id"], _external=True))


def back(biz):
    return redirect(url_for("org.members_page", biz_id=biz["id"]))


@bp.route("/org/<biz_id>/roles/add", methods=["POST"])
@admin_biz
def add_role(biz):
    name = " ".join((request.form.get("name") or "").split())
    access = request.form.get("access", "")
    if not ROLE_RE.match(name) or name.lower() == "admin":
        flash("Use a role name of 2 to 40 letters or numbers. Admin is reserved.", "err")
    elif access not in ACCESS:
        flash("Choose what this role can see.", "err")
    elif orgdb.add_role(biz["id"], name, access) is None:
        flash("That role already exists.", "err")
    else:
        flash("Role added.", "ok")
    return back(biz)


@bp.route("/org/<biz_id>/roles/delete/<int:role_id>", methods=["POST"])
@admin_biz
def del_role(biz, role_id):
    orgdb.delete_role(biz["id"], role_id)
    flash("Role deleted, along with its members.", "ok")
    return back(biz)


@bp.route("/org/<biz_id>/members/add", methods=["POST"])
@admin_biz
def add_member(biz):
    f = request.form
    name = (f.get("display_name") or "").strip()[:80]
    username = (f.get("username") or "").strip().lower()
    if not name or not USER_RE.match(username):
        flash("Enter a name, and a username of 3 to 30 letters, numbers, dots, dashes or underscores.", "err")
        return back(biz)
    try:
        role_id = int(f.get("role_id", ""))
    except ValueError:
        flash("Choose a role.", "err")
        return back(biz)
    role = next((r for r in orgdb.list_roles(biz["id"]) if r["id"] == role_id), None)
    if not role:
        flash("Choose a role.", "err")
        return back(biz)
    pw = (f.get("password") or "").strip()
    if pw and len(pw) < 6:
        flash("A password must be at least 6 characters, or leave it empty to generate one.", "err")
        return back(biz)
    if not pw:
        pw = gen_password()
    rtable, rec_id = None, None
    tname = (f.get("record_table") or "").strip()
    value = (f.get("record_value") or "").strip()
    if tname or value:
        table = db.get_table(biz, tname)
        if not table or not value:
            flash("To link a record, choose the table and type the record name or number.", "err")
            return back(biz)
        with db.connect() as c:
            rec_id = db.resolve_link(c, biz["pg_schema"], table, value)
        if rec_id is None:
            flash(f"I could not find '{value}' in {pretty(tname)}.", "err")
            return back(biz)
        rtable = tname
    elif role["access"] == "own":
        flash("This role sees only its own records, so link the member to their record.", "err")
        return back(biz)
    if orgdb.add_member(biz["id"], username, generate_password_hash(pw), name, role_id, rtable, rec_id) is None:
        flash("That username is already used in this organization.", "err")
    else:
        flash(f"Member added. Username: {username}   Password: {pw}   Write the password down, it is shown only once.", "ok")
    return back(biz)


@bp.route("/org/<biz_id>/members/reset/<int:member_id>", methods=["POST"])
@admin_biz
def reset_member(biz, member_id):
    pw = gen_password()
    if orgdb.set_member_password(biz["id"], member_id, generate_password_hash(pw)):
        flash(f"New password: {pw}   Write it down, it is shown only once.", "ok")
    return back(biz)


@bp.route("/org/<biz_id>/members/delete/<int:member_id>", methods=["POST"])
@admin_biz
def del_member(biz, member_id):
    orgdb.delete_member(biz["id"], member_id)
    flash("Member removed.", "ok")
    return back(biz)
