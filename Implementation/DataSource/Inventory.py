"""Stock alerts (POSTGRES alerts). Called whenever a product's stock changes."""


def refresh_alert(store, product):
    """Open an alert when stock drops below the seller's threshold; resolve it
    when the product is restocked. Writes through store.ops (memory or PostgreSQL)."""
    ops = store.ops
    with store.lock:
        open_alert = ops.open_alert(product["product_id"])
        low = product["stock"] < product.get("threshold_limit", 0)
        now = store.now().isoformat()
        if low:
            if open_alert is None:
                ops.insert_alert({"product_id": product["product_id"], "seller_id": product["seller_id"],
                                  "current_stock": product["stock"],
                                  "threshold_limit": product["threshold_limit"],
                                  "alert_status": "Open", "updated_at": now})
            elif (open_alert["current_stock"], open_alert["threshold_limit"]) != \
                    (product["stock"], product["threshold_limit"]):
                ops.update_alert(open_alert["alert_id"], {"current_stock": product["stock"],
                                                          "threshold_limit": product["threshold_limit"],
                                                          "updated_at": now})
        elif open_alert is not None:
            ops.update_alert(open_alert["alert_id"], {"alert_status": "Resolved", "current_stock": product["stock"],
                                                      "threshold_limit": product["threshold_limit"],
                                                      "updated_at": now})
