"""Screens: Home / catalog and product detail (MainService)."""
from flask import (Blueprint, Response, abort, flash, g, redirect, render_template, request,
                   url_for)

from Interfaces.MainServiceInterface.MainDTO import SORTS
from Interfaces.Validators import ValidationError
from View.MainViews.PY.security import flash_errors, login_required, services

bp = Blueprint("shop", __name__)


@bp.get("/")
def home():
    page = services().main.home(request.args)
    return render_template("shop/home.html", page=page, sorts=SORTS)


@bp.get("/product/<product_id>")
def product(product_id):
    uid = g.user.id_user if g.user else None
    data = services().main.product_page(product_id, uid)
    if data is None:
        abort(404)
    return render_template("shop/product.html", data=data, p=data.product)


@bp.get("/media/products/<product_id>/<name>")
def product_photo(product_id, name):
    """Photos of the memory backend (demo/tests). With AWS, <img> points to S3."""
    photo = services().main.product_photo(product_id, name)
    if photo is None:
        abort(404)
    content_type, data = photo
    resp = Response(data, mimetype=content_type)
    resp.headers["Cache-Control"] = "public, max-age=86400"
    resp.headers["Content-Disposition"] = "inline"
    return resp


@bp.post("/product/<product_id>/reviews")
@login_required
def add_review(product_id):
    try:
        services().main.add_review(g.user.id_user, product_id, request.form)
        flash("Thanks! Your review was published.", "ok")
    except ValidationError as e:
        flash_errors(e)
    return redirect(url_for("shop.product", product_id=product_id, _anchor="reviews"))
