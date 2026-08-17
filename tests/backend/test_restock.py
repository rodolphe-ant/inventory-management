"""
Tests for restocking API endpoints (candidates and restock orders).
"""
import itertools
import re
from datetime import datetime

import pytest

import main


@pytest.fixture(autouse=True)
def reset_restock_state():
    """Reset the in-memory restock order store before every test.

    The store is a module-level list in main.py; it must be cleared in place
    (not rebound) so the route handlers keep referencing the same object.
    """
    main.restock_orders.clear()
    main._restock_seq = itertools.count(1)
    yield
    main.restock_orders.clear()
    main._restock_seq = itertools.count(1)


def _parse(ts):
    return datetime.strptime(ts, "%Y-%m-%dT%H:%M:%S")


class TestRestockCandidates:
    """Test suite for GET /api/restock/candidates."""

    def test_get_all_candidates(self, client):
        """Test getting all restock candidates and their structure."""
        response = client.get("/api/restock/candidates")
        assert response.status_code == 200

        data = response.json()
        assert isinstance(data, list)
        assert len(data) > 0

        first = data[0]
        for field in [
            "sku", "name", "category", "warehouse", "quantity_on_hand",
            "reorder_point", "unit_cost", "lead_time_days", "current_demand",
            "forecasted_demand", "trend", "recommended_quantity",
            "restock_cost", "urgency_score",
        ]:
            assert field in first, f"Missing field {field}"

    def test_candidates_positive_quantity_and_sorted_by_urgency(self, client):
        """Test that only items needing stock are returned, most urgent first."""
        data = client.get("/api/restock/candidates").json()

        for candidate in data:
            assert isinstance(candidate["recommended_quantity"], int)
            assert candidate["recommended_quantity"] > 0
            assert candidate["trend"] in ["increasing", "stable", "decreasing"]

        scores = [c["urgency_score"] for c in data]
        assert scores == sorted(scores, reverse=True)

    def test_candidates_quantity_and_cost_calculation(self, client):
        """Test recommended quantity and cost against inventory and forecast data."""
        candidates = client.get("/api/restock/candidates").json()
        inventory = {i["sku"]: i for i in client.get("/api/inventory").json()}
        forecasts = {f["item_sku"]: f for f in client.get("/api/demand").json()}

        for candidate in candidates:
            item = inventory[candidate["sku"]]
            forecast = forecasts[candidate["sku"]]
            expected_qty = forecast["forecasted_demand"] + item["reorder_point"] - item["quantity_on_hand"]
            assert candidate["recommended_quantity"] == expected_qty
            assert abs(candidate["restock_cost"] - expected_qty * item["unit_cost"]) < 0.01
            assert candidate["lead_time_days"] == item["lead_time_days"]

    def test_candidates_skus_exist_in_inventory(self, client):
        """Test that every candidate SKU is a stocked inventory item."""
        candidates = client.get("/api/restock/candidates").json()
        inventory_skus = {i["sku"] for i in client.get("/api/inventory").json()}

        for candidate in candidates:
            assert candidate["sku"] in inventory_skus

    def test_get_candidates_by_warehouse(self, client):
        """Test filtering candidates by warehouse."""
        response = client.get("/api/restock/candidates?warehouse=London")
        assert response.status_code == 200

        data = response.json()
        assert len(data) > 0
        for candidate in data:
            assert candidate["warehouse"] == "London"

    def test_get_candidates_by_category(self, client):
        """Test filtering candidates by category (case-insensitive)."""
        response = client.get("/api/restock/candidates?category=actuators")
        assert response.status_code == 200

        data = response.json()
        assert len(data) > 0
        for candidate in data:
            assert candidate["category"].lower() == "actuators"

    def test_get_candidates_unknown_warehouse_empty(self, client):
        """Test that an unknown warehouse yields an empty list, not an error."""
        response = client.get("/api/restock/candidates?warehouse=Nowhere")
        assert response.status_code == 200
        assert response.json() == []


class TestInventoryLeadTime:
    """Inventory now exposes lead_time_days used by restocking."""

    def test_inventory_has_lead_time_days(self, client):
        """Test that every inventory item carries a positive integer lead time."""
        data = client.get("/api/inventory").json()
        assert len(data) == 40

        for item in data:
            assert "lead_time_days" in item
            assert isinstance(item["lead_time_days"], int)
            assert item["lead_time_days"] > 0


class TestRestockOrders:
    """Test suite for POST/GET /api/restock-orders."""

    def _two_line_payload(self, client, budget=None):
        candidates = client.get("/api/restock/candidates").json()
        assert len(candidates) >= 2
        items = [
            {"sku": candidates[0]["sku"], "quantity": candidates[0]["recommended_quantity"]},
            {"sku": candidates[1]["sku"], "quantity": candidates[1]["recommended_quantity"]},
        ]
        expected_total = round(candidates[0]["restock_cost"] + candidates[1]["restock_cost"], 2)
        expected_lead = max(candidates[0]["lead_time_days"], candidates[1]["lead_time_days"])
        payload = {"items": items}
        if budget is not None:
            payload["budget"] = budget
        return payload, expected_total, expected_lead

    def test_create_restock_order(self, client):
        """Test the happy path: order is created with server-computed totals and dates."""
        payload, expected_total, expected_lead = self._two_line_payload(client, budget=100000)

        response = client.post("/api/restock-orders", json=payload)
        assert response.status_code == 201

        order = response.json()
        assert re.match(r"^RST-\d{4}-0001$", order["order_number"])
        assert order["status"] == "Submitted"
        assert order["budget"] == 100000
        assert abs(order["total_cost"] - expected_total) < 0.01
        assert order["lead_time_days"] == expected_lead
        assert len(order["items"]) == 2

        for line in order["items"]:
            for field in ["sku", "name", "category", "warehouse", "quantity", "unit_cost", "line_total", "lead_time_days"]:
                assert field in line
            assert abs(line["line_total"] - line["quantity"] * line["unit_cost"]) < 0.01

        # Delivery date is submission date plus the order lead time, same ISO format as orders.json
        assert "T" in order["submitted_at"] and "T" in order["expected_delivery"]
        delta = _parse(order["expected_delivery"]) - _parse(order["submitted_at"])
        assert delta.days == expected_lead

    def test_create_restock_order_ignores_client_pricing(self, client):
        """Test that unit_cost sent by the client is ignored in favour of inventory pricing."""
        inventory = {i["sku"]: i for i in client.get("/api/inventory").json()}
        candidate = client.get("/api/restock/candidates").json()[0]

        payload = {"items": [{"sku": candidate["sku"], "quantity": 10, "unit_cost": 0.01}]}
        response = client.post("/api/restock-orders", json=payload)
        assert response.status_code == 201

        order = response.json()
        expected_unit_cost = inventory[candidate["sku"]]["unit_cost"]
        assert order["items"][0]["unit_cost"] == expected_unit_cost
        assert abs(order["total_cost"] - 10 * expected_unit_cost) < 0.01

    def test_create_restock_order_without_budget(self, client):
        """Test that budget is optional."""
        payload, expected_total, _ = self._two_line_payload(client)

        response = client.post("/api/restock-orders", json=payload)
        assert response.status_code == 201
        assert response.json()["budget"] is None
        assert abs(response.json()["total_cost"] - expected_total) < 0.01

    def test_create_restock_order_budget_exactly_total(self, client):
        """Test that an order whose total equals the budget is accepted (float edge)."""
        payload, expected_total, _ = self._two_line_payload(client)
        payload["budget"] = expected_total

        response = client.post("/api/restock-orders", json=payload)
        assert response.status_code == 201

    def test_create_restock_order_over_budget(self, client):
        """Test that exceeding the budget is rejected and nothing is stored."""
        payload, expected_total, _ = self._two_line_payload(client)
        payload["budget"] = expected_total - 1

        response = client.post("/api/restock-orders", json=payload)
        assert response.status_code == 400
        assert "budget" in response.json()["detail"].lower()

        assert client.get("/api/restock-orders").json() == []

    def test_create_restock_order_empty_items(self, client):
        """Test that an order with no items fails validation."""
        response = client.post("/api/restock-orders", json={"budget": 1000, "items": []})
        assert response.status_code == 422

    def test_create_restock_order_invalid_quantity(self, client):
        """Test that zero or negative quantities fail validation."""
        sku = client.get("/api/restock/candidates").json()[0]["sku"]

        for quantity in [0, -5]:
            response = client.post("/api/restock-orders", json={"items": [{"sku": sku, "quantity": quantity}]})
            assert response.status_code == 422

    def test_create_restock_order_negative_budget(self, client):
        """Test that a negative budget fails validation."""
        sku = client.get("/api/restock/candidates").json()[0]["sku"]
        response = client.post("/api/restock-orders", json={"budget": -1, "items": [{"sku": sku, "quantity": 1}]})
        assert response.status_code == 422

    def test_create_restock_order_nonexistent_sku(self, client):
        """Test that an unknown SKU returns 404 and stores nothing."""
        response = client.post("/api/restock-orders", json={"items": [{"sku": "NOPE-999", "quantity": 1}]})
        assert response.status_code == 404

        data = response.json()
        assert "detail" in data
        assert "not found" in data["detail"].lower()
        assert client.get("/api/restock-orders").json() == []

    def test_create_restock_order_duplicate_skus(self, client):
        """Test that repeating a SKU in one order is rejected."""
        sku = client.get("/api/restock/candidates").json()[0]["sku"]
        payload = {"items": [{"sku": sku, "quantity": 1}, {"sku": sku, "quantity": 2}]}

        response = client.post("/api/restock-orders", json=payload)
        assert response.status_code == 400
        assert "duplicate" in response.json()["detail"].lower()

    def test_get_restock_orders_newest_first(self, client):
        """Test listing returns all submitted orders, newest first, with sequential numbers."""
        payload, _, _ = self._two_line_payload(client)

        first = client.post("/api/restock-orders", json=payload).json()
        second = client.post("/api/restock-orders", json=payload).json()

        response = client.get("/api/restock-orders")
        assert response.status_code == 200

        data = response.json()
        assert isinstance(data, list)
        assert [o["order_number"] for o in data] == [second["order_number"], first["order_number"]]
        assert first["order_number"].endswith("-0001")
        assert second["order_number"].endswith("-0002")
        assert first["id"] != second["id"]

    def test_get_restock_orders_empty_by_default(self, client):
        """Test that the store starts empty (also proves per-test isolation)."""
        response = client.get("/api/restock-orders")
        assert response.status_code == 200
        assert response.json() == []
