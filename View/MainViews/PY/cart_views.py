"""Screens: cart and checkout (CartService, PayService)."""
from flask import (Blueprint, flash, g, redirect, render_template, request,
                   url_for)

from Interfaces.PayServiceInterface.PayDTO import MX_STATES, PAYMENT_METHODS
from Interfaces.Validators import ValidationError
from View.MainViews.PY.security import (flash_errors, login_required, safe_next,
                                        services)

bp = Blueprint("cart", __name__)


@bp.get("/cart")
@login_required
def view():
    cart = services().cart.view(g.user.id_user)
    for notice in cart.notices:
        flash(notice, "info")
    return render_template("cart/cart.html", cart=cart)


@bp.post("/cart/add")
@login_required
def add():
    product_id = request.form.get("product_id", "")
    back = safe_next(request.form.get("next"), url_for("shop.home"))
    try:
        services().cart.add(g.user.id_user, request.form)
    except ValidationError as e:
        flash_errors(e)
        return redirect(back)
    flash("Added to your cart.", "ok")
    if request.form.get("go") == "cart":
        return redirect(url_for("cart.view"))
    return redirect(url_for("shop.product", product_id=product_id))


@bp.post("/cart/<line_id>/update")
@login_required
def update(line_id):
    try:
        services().cart.update(g.user.id_user, line_id, request.form.get("qty"))
    except ValidationError as e:
        flash_errors(e)
    return redirect(url_for("cart.view"))


@bp.post("/cart/<line_id>/remove")
@login_required
def remove(line_id):
    services().cart.remove(g.user.id_user, line_id)
    flash("Item removed.", "ok")
    return redirect(url_for("cart.view"))


@bp.route("/checkout", methods=["GET", "POST"])
@login_required
def checkout():
    svc = services()
    form = {}
    if request.method == "POST":
        try:
            order_id = svc.pay.place_order(g.user.id_user, request.form)
            return redirect(url_for("account.confirmation", order_id=order_id))
        except ValidationError as e:
            flash_errors(e)
            # Keep the address, NEVER echo card data back into the page.
            form = {k: request.form.get(k, "")[:120] for k in ("street", "city", "state", "zip_code", "method")}
    cart = svc.cart.view(g.user.id_user)
    if cart.empty:
        flash("Your cart is empty.", "info")
        return redirect(url_for("cart.view"))
    return render_template("cart/checkout.html", cart=cart, form=form,
                           states=MX_STATES, methods=PAYMENT_METHODS)
