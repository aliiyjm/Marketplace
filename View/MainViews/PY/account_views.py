"""Screens: order confirmation, my orders, order detail, invoice, favorites."""
from flask import (Blueprint, abort, flash, g, redirect, render_template,
                   request, url_for)

from Interfaces.PayServiceInterface.PayDTO import ORDER_STATUSES
from Interfaces.Validators import ValidationError
from View.MainViews.PY.security import (flash_errors, login_required, safe_next,
                                        services)

bp = Blueprint("account", __name__)


def _own_order(order_id):
    order = services().pay.my_order(g.user.id_user, order_id)
    if order is None:
        abort(404)   # same answer for "does not exist" and "not yours"
    return order


@bp.get("/orders/<int:order_id>/confirmation")
@login_required
def confirmation(order_id):
    return render_template("account/confirmation.html", order=_own_order(order_id))


@bp.get("/orders")
@login_required
def orders():
    rows, status, q = services().pay.my_orders(g.user.id_user, request.args)
    return render_template("account/orders.html", orders=rows, status=status, q=q, statuses=ORDER_STATUSES)


@bp.get("/orders/<int:order_id>")
@login_required
def order_detail(order_id):
    return render_template("account/order_detail.html", order=_own_order(order_id), admin=False)


@bp.get("/orders/<int:order_id>/invoice")
@login_required
def invoice(order_id):
    order = services().pay.admin_order(order_id) if g.user.is_admin else _own_order(order_id)
    if order is None or order.invoice is None:
        abort(404)
    return render_template("account/invoice.html", order=order)


@bp.get("/favorites")
@login_required
def favorites():
    return render_template("account/favorites.html", products=services().fav.list(g.user.id_user))


@bp.post("/favorites/toggle")
@login_required
def toggle_favorite():
    try:
        now_fav = services().fav.toggle(g.user.id_user, request.form)
        flash("Saved to favorites." if now_fav else "Removed from favorites.", "ok")
    except ValidationError as e:
        flash_errors(e)
    return redirect(safe_next(request.form.get("next"), url_for("account.favorites")))
