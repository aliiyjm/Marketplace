"""Screens: seller dashboard, my products, product form (SellerService).
seller_id always comes from the session (g.user.seller_id), never from the form."""
from flask import (Blueprint, abort, flash, g, redirect, render_template,
                   request, url_for)

from Interfaces.Validators import ValidationError
from View.MainViews.PY.security import flash_errors, seller_required, services

bp = Blueprint("seller", __name__, url_prefix="/seller")


@bp.get("")
@seller_required
def dashboard():
    return render_template("seller/dashboard.html", d=services().seller.dashboard(g.user.seller_id))


@bp.get("/products")
@seller_required
def products():
    page, q, status = services().seller.products(g.user.seller_id, request.args)
    return render_template("seller/products.html", page=page, q=q, status=status)


def _form_page(product=None, form=None, status=200):
    svc = services().seller
    return render_template("seller/product_form.html", product=product, form=form or {},
                           categories=svc.categories(),
                           preview=svc.dynamo_json(product) if product else None), status


def _form_echo():
    """Re-fill the form after a validation error (text only, length-limited)."""
    data = {k: request.form.get(k, "")[:2000] for k in
            ("name", "description", "category_id", "price", "stock", "threshold_limit", "sizes", "colors", "visible")}
    data["attrs"] = list(zip(request.form.getlist("attr_key")[:20], request.form.getlist("attr_value")[:20]))
    return data


@bp.route("/products/new", methods=["GET", "POST"])
@seller_required
def new_product():
    if request.method == "POST":
        try:
            pid = services().seller.create(g.user.seller_id, request.form)
            flash(f"Product {pid} created.", "ok")
            return redirect(url_for("seller.products"))
        except ValidationError as e:
            flash_errors(e)
            return _form_page(form=_form_echo(), status=400)
    return _form_page(form={"visible": "1", "threshold_limit": "5", "attrs": [("Material", ""), ("Fit", "")]})


@bp.route("/products/<product_id>/edit", methods=["GET", "POST"])
@seller_required
def edit_product(product_id):
    svc = services().seller
    product = svc.product(g.user.seller_id, product_id)
    if product is None:
        abort(404)
    if request.method == "POST":
        try:
            svc.update(g.user.seller_id, product_id, request.form)
            flash("Changes saved.", "ok")
            return redirect(url_for("seller.edit_product", product_id=product_id))
        except ValidationError as e:
            flash_errors(e)
            return _form_page(product, _form_echo(), 400)
    form = {"name": product.name, "description": product.description, "category_id": str(product.category_id),
            "price": f"{product.price:.2f}", "stock": str(product.stock),
            "threshold_limit": str(product.threshold_limit), "sizes": ", ".join(product.sizes),
            "colors": ", ".join(product.colors), "visible": "1" if product.status == "active" else "",
            "attrs": list(product.attributes.items())}
    return _form_page(product, form)


@bp.route("/products/<product_id>/delete", methods=["GET", "POST"])
@seller_required
def delete_product(product_id):
    svc = services().seller
    product = svc.product(g.user.seller_id, product_id)
    if product is None:
        abort(404)
    if request.method == "POST":
        svc.delete(g.user.seller_id, product_id)
        flash(f"“{product.name}” was deleted.", "ok")
        return redirect(url_for("seller.products"))
    return render_template("seller/confirm_delete.html", product=product)
