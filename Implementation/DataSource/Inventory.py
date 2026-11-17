"""Stock alerts (POSTGRES_ALERTS). Called whenever a product's stock changes."""


def refresh_alert(store, product):
    """Open an alert when stock drops below the seller's threshold; resolve it
    when the product is restocked."""
    with store.lock:
        open_alert = next((a for a in store.alerts.values()
                           if a["product_id"] == product["product_id"] and a["alert_status"] == "Open"), None)
        low = product["stock"] < product.get("threshold_limit", 0)
        if low:
            if open_alert:
                open_alert["current_stock"] = product["stock"]
                open_alert["threshold_limit"] = product["threshold_limit"]
            else:
                alert_id = store.next_id("alerts")
                store.alerts[alert_id] = {
                    "alert_id": alert_id, "product_id": product["product_id"],
                    "current_stock": product["stock"], "threshold_limit": product["threshold_limit"],
                    "alert_status": "Open", "seller_id": product["seller_id"],
                    "updated_at": store.now().isoformat(),
                }
        elif open_alert:
            open_alert.update(alert_status="Resolved", current_stock=product["stock"],
                              threshold_limit=product["threshold_limit"],
                              updated_at=store.now().isoformat())
