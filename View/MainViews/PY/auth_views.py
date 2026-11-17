"""Screen: sign in / create account (AuthService)."""
from flask import (Blueprint, flash, g, redirect, render_template, request,
                   session, url_for)

from Interfaces.Validators import ValidationError
from View.MainViews.PY.security import csrf_token, safe_next, services

bp = Blueprint("auth", __name__, url_prefix="/account")


def _start_session(user, remember=False):
    # A new session on login prevents session fixation.
    session.clear()
    session["uid"] = user.id_user
    session.permanent = remember
    csrf_token()


@bp.get("/access")
def access():
    if g.user:
        return redirect(url_for("shop.home"))
    return render_template("auth/access.html", next=safe_next(request.args.get("next"), ""),
                           signin_errors=[], register_errors=[], form={})


@bp.post("/signin")
def signin():
    nxt = safe_next(request.form.get("next"), "")
    try:
        user, remember = services().auth.sign_in(request.form)
    except ValidationError as e:
        form = {"email": request.form.get("email", "")[:120]}
        return render_template("auth/access.html", next=nxt, signin_errors=e.errors,
                               register_errors=[], form=form), 401
    _start_session(user, remember)
    flash(f"Welcome back, {user.first_name}!", "ok")
    return redirect(safe_next(nxt))


@bp.post("/register")
def register():
    nxt = safe_next(request.form.get("next"), "")
    try:
        user = services().auth.register(request.form)
    except ValidationError as e:
        keep = ("name", "email", "role", "store_name", "tax_id")
        form = {k: request.form.get(k, "")[:120] for k in keep}
        return render_template("auth/access.html", next=nxt, signin_errors=[],
                               register_errors=e.errors, form=form), 400
    _start_session(user)
    flash(f"Your account is ready, {user.first_name}.", "ok")
    return redirect(url_for("seller.dashboard") if user.is_seller else safe_next(nxt))


@bp.post("/signout")
def signout():
    session.clear()
    flash("You signed out.", "ok")
    return redirect(url_for("shop.home"))
