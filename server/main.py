import itertools
from datetime import datetime, timedelta
from fastapi import FastAPI, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from typing import List, Optional
from pydantic import BaseModel, Field
from mock_data import inventory_items, orders, demand_forecasts, backlog_items, spending_summary, monthly_spending, category_spending, recent_transactions, purchase_orders

app = FastAPI(title="Factory Inventory Management System")

# In-memory store for restocking orders created via POST /api/restock-orders.
# Owned here (not in mock_data) because it is runtime state, not JSON-loaded data.
# Tests must mutate these in place (restock_orders.clear()) rather than rebinding,
# otherwise the route handlers keep pointing at the old objects.
restock_orders: list = []
_restock_seq = itertools.count(1)

# Trend multiplier for restock urgency: rising demand is prioritised, falling demand
# de-prioritised. Values are heuristic and only affect ordering, never quantities.
TREND_WEIGHT = {'increasing': 1.5, 'stable': 1.0, 'decreasing': 0.6}

# Quarter mapping for date filtering
QUARTER_MAP = {
    'Q1-2025': ['2025-01', '2025-02', '2025-03'],
    'Q2-2025': ['2025-04', '2025-05', '2025-06'],
    'Q3-2025': ['2025-07', '2025-08', '2025-09'],
    'Q4-2025': ['2025-10', '2025-11', '2025-12']
}

def filter_by_month(items: list, month: Optional[str]) -> list:
    """Filter items by month/quarter based on order_date field"""
    if not month or month == 'all':
        return items

    if month.startswith('Q'):
        # Handle quarters
        if month in QUARTER_MAP:
            months = QUARTER_MAP[month]
            return [item for item in items if any(m in item.get('order_date', '') for m in months)]
    else:
        # Direct month match
        return [item for item in items if month in item.get('order_date', '')]

    return items

def apply_filters(items: list, warehouse: Optional[str] = None, category: Optional[str] = None,
                 status: Optional[str] = None) -> list:
    """Apply common filters to a list of items"""
    filtered = items

    if warehouse and warehouse != 'all':
        filtered = [item for item in filtered if item.get('warehouse') == warehouse]

    if category and category != 'all':
        filtered = [item for item in filtered if item.get('category', '').lower() == category.lower()]

    if status and status != 'all':
        filtered = [item for item in filtered if item.get('status', '').lower() == status.lower()]

    return filtered

# CORS middleware
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# Data models
class InventoryItem(BaseModel):
    id: str
    sku: str
    name: str
    category: str
    warehouse: str
    quantity_on_hand: int
    reorder_point: int
    unit_cost: float
    location: str
    last_updated: str
    lead_time_days: int = 14

class Order(BaseModel):
    id: str
    order_number: str
    customer: str
    items: List[dict]
    status: str
    order_date: str
    expected_delivery: str
    total_value: float
    actual_delivery: Optional[str] = None
    warehouse: Optional[str] = None
    category: Optional[str] = None

class DemandForecast(BaseModel):
    id: str
    item_sku: str
    item_name: str
    current_demand: int
    forecasted_demand: int
    trend: str
    period: str

class BacklogItem(BaseModel):
    id: str
    order_id: str
    item_sku: str
    item_name: str
    quantity_needed: int
    quantity_available: int
    days_delayed: int
    priority: str
    has_purchase_order: Optional[bool] = False

class PurchaseOrder(BaseModel):
    id: str
    backlog_item_id: str
    supplier_name: str
    quantity: int
    unit_cost: float
    expected_delivery_date: str
    status: str
    created_date: str
    notes: Optional[str] = None

class CreatePurchaseOrderRequest(BaseModel):
    backlog_item_id: str
    supplier_name: str
    quantity: int
    unit_cost: float
    expected_delivery_date: str
    notes: Optional[str] = None

class RestockCandidate(BaseModel):
    sku: str
    name: str
    category: str
    warehouse: str
    quantity_on_hand: int
    reorder_point: int
    unit_cost: float
    lead_time_days: int
    current_demand: int
    forecasted_demand: int
    trend: str
    recommended_quantity: int
    restock_cost: float
    urgency_score: float

class RestockOrderLineRequest(BaseModel):
    sku: str
    quantity: int = Field(gt=0)

class CreateRestockOrderRequest(BaseModel):
    budget: Optional[float] = Field(default=None, ge=0)
    items: List[RestockOrderLineRequest] = Field(min_length=1)

class RestockOrderLine(BaseModel):
    sku: str
    name: str
    category: str
    warehouse: str
    quantity: int
    unit_cost: float
    line_total: float
    lead_time_days: int

class RestockOrder(BaseModel):
    id: str
    order_number: str
    status: str
    submitted_at: str
    expected_delivery: str
    lead_time_days: int
    items: List[RestockOrderLine]
    total_cost: float
    budget: Optional[float] = None

# API endpoints
@app.get("/")
def root():
    return {"message": "Factory Inventory Management System API", "version": "1.0.0"}

@app.get("/api/inventory", response_model=List[InventoryItem])
def get_inventory(
    warehouse: Optional[str] = None,
    category: Optional[str] = None
):
    """Get all inventory items with optional filtering"""
    return apply_filters(inventory_items, warehouse, category)

@app.get("/api/inventory/{item_id}", response_model=InventoryItem)
def get_inventory_item(item_id: str):
    """Get a specific inventory item"""
    item = next((item for item in inventory_items if item["id"] == item_id), None)
    if not item:
        raise HTTPException(status_code=404, detail="Item not found")
    return item

@app.get("/api/orders", response_model=List[Order])
def get_orders(
    warehouse: Optional[str] = None,
    category: Optional[str] = None,
    status: Optional[str] = None,
    month: Optional[str] = None
):
    """Get all orders with optional filtering"""
    filtered_orders = apply_filters(orders, warehouse, category, status)
    filtered_orders = filter_by_month(filtered_orders, month)
    return filtered_orders

@app.get("/api/orders/{order_id}", response_model=Order)
def get_order(order_id: str):
    """Get a specific order"""
    order = next((order for order in orders if order["id"] == order_id), None)
    if not order:
        raise HTTPException(status_code=404, detail="Order not found")
    return order

@app.get("/api/demand", response_model=List[DemandForecast])
def get_demand_forecasts():
    """Get demand forecasts"""
    return demand_forecasts

@app.get("/api/backlog", response_model=List[BacklogItem])
def get_backlog():
    """Get backlog items with purchase order status"""
    # Add has_purchase_order flag to each backlog item
    result = []
    for item in backlog_items:
        item_dict = dict(item)
        # Check if this backlog item has a purchase order
        has_po = any(po["backlog_item_id"] == item["id"] for po in purchase_orders)
        item_dict["has_purchase_order"] = has_po
        result.append(item_dict)
    return result

@app.get("/api/restock/candidates", response_model=List[RestockCandidate])
def get_restock_candidates(
    warehouse: Optional[str] = None,
    category: Optional[str] = None
):
    """Join demand forecasts with inventory and score each item for restocking.

    The client applies the budget; this endpoint only supplies the ranked candidates.
    """
    # Filter on the inventory side (forecasts carry no warehouse/category), then join by SKU.
    inventory_by_sku = {item["sku"]: item for item in apply_filters(inventory_items, warehouse, category)}

    candidates = []
    for forecast in demand_forecasts:
        item = inventory_by_sku.get(forecast["item_sku"])
        if item is None:
            # Forecast SKU filtered out (or not stocked at all) - nothing to restock here.
            continue

        # Target stock = cover the 30-day forecast and still land on the reorder point,
        # so the item is not immediately flagged low-stock again after the period.
        target = forecast["forecasted_demand"] + item["reorder_point"]
        recommended_quantity = max(0, target - item["quantity_on_hand"])
        if recommended_quantity == 0:
            continue

        # Urgency = share of the target that is currently missing, weighted by demand trend.
        # Normalising by target keeps cheap high-volume parts comparable with expensive
        # low-volume ones; the trend weight breaks ties toward growing demand.
        urgency_score = TREND_WEIGHT.get(forecast["trend"], 1.0) * recommended_quantity / max(1, target)

        candidates.append({
            "sku": item["sku"],
            "name": item["name"],
            "category": item["category"],
            "warehouse": item["warehouse"],
            "quantity_on_hand": item["quantity_on_hand"],
            "reorder_point": item["reorder_point"],
            "unit_cost": item["unit_cost"],
            "lead_time_days": item.get("lead_time_days", 14),
            "current_demand": forecast["current_demand"],
            "forecasted_demand": forecast["forecasted_demand"],
            "trend": forecast["trend"],
            "recommended_quantity": recommended_quantity,
            "restock_cost": round(recommended_quantity * item["unit_cost"], 2),
            "urgency_score": round(urgency_score, 4),
        })

    # Most urgent first; SKU as tiebreaker keeps the order deterministic for the greedy client pass.
    candidates.sort(key=lambda c: (-c["urgency_score"], c["sku"]))
    return candidates

@app.post("/api/restock-orders", response_model=RestockOrder, status_code=201)
def create_restock_order(request: CreateRestockOrderRequest):
    """Create a restocking order from the selected SKUs and quantities."""
    skus = [line.sku for line in request.items]
    if len(skus) != len(set(skus)):
        raise HTTPException(status_code=400, detail="Duplicate SKUs in restock order")

    inventory_by_sku = {item["sku"]: item for item in inventory_items}
    missing = [sku for sku in skus if sku not in inventory_by_sku]
    if missing:
        raise HTTPException(status_code=404, detail=f"Item not found: {', '.join(missing)}")

    # Price and lead time always come from inventory, never from the client payload,
    # so a tampered request cannot under-price an order or shorten its lead time.
    lines = []
    for line in request.items:
        item = inventory_by_sku[line.sku]
        lines.append({
            "sku": item["sku"],
            "name": item["name"],
            "category": item["category"],
            "warehouse": item["warehouse"],
            "quantity": line.quantity,
            "unit_cost": item["unit_cost"],
            "line_total": round(line.quantity * item["unit_cost"], 2),
            "lead_time_days": item.get("lead_time_days", 14),
        })

    total_cost = round(sum(l["line_total"] for l in lines), 2)
    # Half-cent tolerance absorbs float rounding between client-side and server-side sums.
    if request.budget is not None and total_cost > request.budget + 0.005:
        raise HTTPException(
            status_code=400,
            detail=f"Order total {total_cost:.2f} exceeds budget {request.budget:.2f}"
        )

    # The order arrives when its slowest line arrives.
    lead_time_days = max(l["lead_time_days"] for l in lines)
    # Naive local timestamp without microseconds to match the existing order_date format
    # (YYYY-MM-DDTHH:MM:SS); an offset-aware value would render shifted next to customer orders.
    now = datetime.now().replace(microsecond=0)
    seq = next(_restock_seq)

    order = {
        "id": f"rst-{seq}",
        "order_number": f"RST-{now.year}-{seq:04d}",
        "status": "Submitted",
        "submitted_at": now.isoformat(),
        "expected_delivery": (now + timedelta(days=lead_time_days)).isoformat(),
        "lead_time_days": lead_time_days,
        "items": lines,
        "total_cost": total_cost,
        "budget": request.budget,
    }
    restock_orders.append(order)
    return order

@app.get("/api/restock-orders", response_model=List[RestockOrder])
def get_restock_orders():
    """List submitted restocking orders, newest first."""
    return list(reversed(restock_orders))

@app.get("/api/dashboard/summary")
def get_dashboard_summary(
    warehouse: Optional[str] = None,
    category: Optional[str] = None,
    status: Optional[str] = None,
    month: Optional[str] = None
):
    """Get summary statistics for dashboard with optional filtering"""
    # Filter inventory
    filtered_inventory = apply_filters(inventory_items, warehouse, category)

    # Filter orders
    filtered_orders = apply_filters(orders, warehouse, category, status)
    filtered_orders = filter_by_month(filtered_orders, month)

    total_inventory_value = sum(item["quantity_on_hand"] * item["unit_cost"] for item in filtered_inventory)
    low_stock_items = len([item for item in filtered_inventory if item["quantity_on_hand"] <= item["reorder_point"]])
    pending_orders = len([order for order in filtered_orders if order["status"] in ["Processing", "Backordered"]])
    total_backlog_items = len(backlog_items)

    return {
        "total_inventory_value": round(total_inventory_value, 2),
        "low_stock_items": low_stock_items,
        "pending_orders": pending_orders,
        "total_backlog_items": total_backlog_items,
        "total_orders_value": sum(order["total_value"] for order in filtered_orders)
    }

@app.get("/api/spending/summary")
def get_spending_summary():
    """Get spending summary statistics"""
    return spending_summary

@app.get("/api/spending/monthly")
def get_monthly_spending():
    """Get monthly spending breakdown"""
    return monthly_spending

@app.get("/api/spending/categories")
def get_category_spending():
    """Get spending by category"""
    return category_spending

@app.get("/api/spending/transactions")
def get_recent_transactions():
    """Get recent transactions"""
    return recent_transactions

@app.get("/api/reports/quarterly")
def get_quarterly_reports():
    """Get quarterly performance reports"""
    # Calculate quarterly statistics from orders
    quarters = {}

    for order in orders:
        order_date = order.get('order_date', '')
        # Determine quarter
        if '2025-01' in order_date or '2025-02' in order_date or '2025-03' in order_date:
            quarter = 'Q1-2025'
        elif '2025-04' in order_date or '2025-05' in order_date or '2025-06' in order_date:
            quarter = 'Q2-2025'
        elif '2025-07' in order_date or '2025-08' in order_date or '2025-09' in order_date:
            quarter = 'Q3-2025'
        elif '2025-10' in order_date or '2025-11' in order_date or '2025-12' in order_date:
            quarter = 'Q4-2025'
        else:
            continue

        if quarter not in quarters:
            quarters[quarter] = {
                'quarter': quarter,
                'total_orders': 0,
                'total_revenue': 0,
                'delivered_orders': 0,
                'avg_order_value': 0
            }

        quarters[quarter]['total_orders'] += 1
        quarters[quarter]['total_revenue'] += order.get('total_value', 0)
        if order.get('status') == 'Delivered':
            quarters[quarter]['delivered_orders'] += 1

    # Calculate averages and fulfillment rate
    result = []
    for q, data in quarters.items():
        if data['total_orders'] > 0:
            data['avg_order_value'] = round(data['total_revenue'] / data['total_orders'], 2)
            data['fulfillment_rate'] = round((data['delivered_orders'] / data['total_orders']) * 100, 1)
        result.append(data)

    # Sort by quarter
    result.sort(key=lambda x: x['quarter'])
    return result

@app.get("/api/reports/monthly-trends")
def get_monthly_trends():
    """Get month-over-month trends"""
    months = {}

    for order in orders:
        order_date = order.get('order_date', '')
        if not order_date:
            continue

        # Extract month (format: YYYY-MM-DD)
        month = order_date[:7]  # Gets YYYY-MM

        if month not in months:
            months[month] = {
                'month': month,
                'order_count': 0,
                'revenue': 0,
                'delivered_count': 0
            }

        months[month]['order_count'] += 1
        months[month]['revenue'] += order.get('total_value', 0)
        if order.get('status') == 'Delivered':
            months[month]['delivered_count'] += 1

    # Convert to list and sort
    result = list(months.values())
    result.sort(key=lambda x: x['month'])
    return result

if __name__ == "__main__":
    import uvicorn
    uvicorn.run(app, host="0.0.0.0", port=8001)
