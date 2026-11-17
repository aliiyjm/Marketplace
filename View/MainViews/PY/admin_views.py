"""Screens: admin users (the original CRUD), categories and orders."""
from flask import (Blueprint, abort, flash, g, redirect, render_template,
                   request, url_for)

from Interfaces.PayServiceInterface.PayDTO import ORDER_STATUSES
from Interfaces.UserServiceInterface.UserDTO import ROLES
from Interfaces.Validators import ValidationError
from View.MainViews.PY.security import admin_required, flash_errors, services

bp = Blueprint("admin", __name__, url_prefix="/admin")


def _api_errors():
    """In DATA_BACKEND=aws mode the API can fail: show a friendly message."""
    try:
        from View.MainViews.Utils.api_client import ApiError
        return (ValidationError, ApiError)
    except ImportError:   # pragma: no cover
        return (ValidationError,)


# ---------- users ----------
@bp.get("/users")
@admin_required
def users():
    try:
        rows, q, role = services().users.list(request.args)
    except _api_errors() as e:
        flash_errors(e)
        rows, q, role = [], "", ""
    return render_template("admin/users.html", users=rows, q=q, role=role, roles=ROLES)


@bp.route("/users/new", methods=["GET", "POST"])
@admin_required
def new_user():
    data = {"name": "", "email": "", "role": "buyer"}
    if request.method == "POST":
        data = {k: request.form.get(k, "")[:120] for k in ("name", "email", "role")}
        try:
            uid = services().users.create(request.form, created_at=services().store.now().isoformat())
            flash(f"User added with ID {uid}.", "ok")
            return redirect(url_for("admin.users"))
        except _api_errors() as e:
            flash_errors(e)
    return render_template("admin/user_form.html", data=data, editing=False, roles=ROLES)


@bp.route("/users/<user_id>/edit", methods=["GET", "POST"])
@admin_required
def edit_user(user_id):
    user = services().users.get(user_id)
    if user is None:
        abort(404)
    data = {"id_user": user.id_user, "name": user.name, "email": user.email, "role": user.role}
    if request.method == "POST":
        data.update({k: request.form.get(k, "")[:120] for k in ("name", "email", "role")})
        if user_id == g.user.id_user and data["role"] != "admin":
            flash("You cannot remove your own admin role.", "error")
        else:
            try:
                services().users.update(user_id, request.form)
                flash("Changes saved.", "ok")
                return redirect(url_for("admin.users"))
            except _api_errors() as e:
                flash_errors(e)
    return render_template("admin/user_form.html", data=data, editing=True, roles=ROLES)


@bp.route("/users/<user_id>/delete", methods=["GET", "POST"])
@admin_required
def delete_user(user_id):
    user = services().users.get(user_id)
    if user is None:
        flash("That user no longer exists.", "error")
        return redirect(url_for("admin.users"))
    if user_id == g.user.id_user:
        flash("You cannot delete your own account.", "error")
        return redirect(url_for("admin.users"))
    if request.method == "POST":
        try:
            services().users.delete(user_id)
            flash("User deleted.", "ok")
        except _api_errors() as e:
            flash_errors(e)
        return redirect(url_for("admin.users"))
    return render_template("admin/user_confirm_delete.html", user=user)


# ---------- categories ----------
@bp.route("/categories", methods=["GET", "POST"])
@admin_required
def categories():
    svc = services().admin
    form = {"is_active": "1"}
    if request.method == "POST":
        form = {k: request.form.get(k, "")[:60] for k in ("name", "slug", "parent_id", "is_active")}
        try:
            svc.create_category(request.form)
            flash("Category created.", "ok")
            return redirect(url_for("admin.categories"))
        except ValidationError as e:
            flash_errors(e)
    return render_template("admin/categories.html", rows=svc.categories(), parents=svc.top_categories(),
                           form=form, editing=None)


@bp.route("/categories/<int:category_id>/edit", methods=["GET", "POST"])
@admin_required
def edit_category(category_id):
    svc = services().admin
    cat = svc.category(category_id)
    if cat is None:
        abort(404)
    form = {"name": cat.name, "slug": cat.slug, "parent_id": str(cat.parent_id or ""),
            "is_active": "1" if cat.is_active else ""}
    if request.method == "POST":
        form = {k: request.form.get(k, "")[:60] for k in ("name", "slug", "parent_id", "is_active")}
        try:
            svc.update_category(category_id, request.form)
            flash("Category updated.", "ok")
            return redirect(url_for("admin.categories"))
        except ValidationError as e:
            flash_errors(e)
    return render_template("admin/categories.html", rows=svc.categories(), parents=svc.top_categories(),
                           form=form, editing=cat)


@bp.post("/categories/<int:category_id>/toggle")
@admin_required
def toggle_category(category_id):
    try:
        active = services().admin.toggle_category(category_id)
        flash("Category activated." if active else "Category hidden from the catalog.", "ok")
    except ValidationError as e:
        flash_errors(e)
    return redirect(url_for("admin.categories"))


# ---------- orders ----------
@bp.get("/orders")
@admin_required
def orders():
    rows, status, q = services().pay.admin_orders(request.args)
    return render_template("admin/orders.html", orders=rows, status=status, q=q, statuses=ORDER_STATUSES)


@bp.get("/orders/<int:order_id>")
@admin_required
def order_detail(order_id):
    order = services().pay.admin_order(order_id)
    if order is None:
        abort(404)
    return render_template("account/order_detail.html", order=order, admin=True)


@bp.post("/orders/<int:order_id>/status")
@admin_required
def change_status(order_id):
    try:
        services().pay.change_status(order_id, request.form)
        flash(f"ORD-{order_id} updated.", "ok")
    except ValidationError as e:
        flash_errors(e)
    return redirect(url_for("admin.order_detail", order_id=order_id))
