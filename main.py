import os
from typing import List, Optional, Literal
from fastapi import FastAPI, HTTPException, UploadFile, File, Form
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel, Field
from math import radians, sin, cos, asin, sqrt
from bson import ObjectId

from database import db, create_document, get_documents
from schemas import AppUser, Pharmacy, Medicine, Inventory, Order, OrderItem

app = FastAPI(title="FASO TiiM Roogo API")

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# Utilities

def haversine_km(lat1: float, lon1: float, lat2: float, lon2: float) -> float:
    """Calculate the great circle distance between two points on the earth (km)."""
    # convert decimal degrees to radians
    lon1, lat1, lon2, lat2 = map(radians, [lon1, lat1, lon2, lat2])
    # haversine formula
    dlon = lon2 - lon1
    dlat = lat2 - lat1
    a = sin(dlat / 2) ** 2 + cos(lat1) * cos(lat2) * sin(dlon / 2) ** 2
    c = 2 * asin(sqrt(a))
    r = 6371  # Radius of earth in kilometers
    return c * r

# Healthcheck
@app.get("/")
def read_root():
    return {"message": "FASO TiiM Roogo Backend Running"}

@app.get("/test")
def test_database():
    response = {
        "backend": "✅ Running",
        "database": "❌ Not Available",
        "database_url": "❌ Not Set",
        "database_name": "❌ Not Set",
        "connection_status": "Not Connected",
        "collections": []
    }
    try:
        if db is not None:
            response["database"] = "✅ Available"
            response["database_url"] = "✅ Set" if os.getenv("DATABASE_URL") else "❌ Not Set"
            response["database_name"] = db.name if hasattr(db, 'name') else "✅ Connected"
            _ = db.list_collection_names()
            response["collections"] = _
            response["database"] = "✅ Connected & Working"
            response["connection_status"] = "Connected"
    except Exception as e:
        response["database"] = f"⚠️ Error: {str(e)[:80]}"
    return response

# -----------------------------
# Catalog and Pharmacy Endpoints
# -----------------------------

class SearchResponseItem(BaseModel):
    inventory_id: str
    pharmacy_id: str
    pharmacy_name: str
    pharmacy_address: str
    medicine_name: str
    dci: Optional[str] = None
    barcode: Optional[str] = None
    price: float
    stock: int
    distance_km: Optional[float] = None

@app.get("/api/pharmacies", response_model=List[Pharmacy])
def list_pharmacies():
    return get_documents("pharmacy")

class SearchQueryParams(BaseModel):
    q: Optional[str] = None
    barcode: Optional[str] = None
    latitude: Optional[float] = None
    longitude: Optional[float] = None

@app.get("/api/search", response_model=List[SearchResponseItem])
def search_inventories(q: Optional[str] = None,
                       barcode: Optional[str] = None,
                       latitude: Optional[float] = None,
                       longitude: Optional[float] = None):
    if not q and not barcode:
        raise HTTPException(status_code=400, detail="Provide 'q' or 'barcode'")

    # Find matching medicines
    med_filter = {}
    if q:
        med_filter = {"$or": [
            {"name": {"$regex": q, "$options": "i"}},
            {"dci": {"$regex": q, "$options": "i"}}
        ]}
    if barcode:
        med_filter = {"barcode": barcode}

    meds = list(db["medicine"].find(med_filter))
    if not meds:
        return []
    med_ids = [str(m["_id"]) for m in meds]

    # Fetch inventories that reference these medicines and have stock
    inventories = list(db["inventory"].find({"medicine_id": {"$in": med_ids}, "stock": {"$gt": 0}}))
    if not inventories:
        return []

    # Build response joining with pharmacy
    pharmacy_ids = list({inv["pharmacy_id"] for inv in inventories})
    pharmacies_map = {str(p["_id"]): p for p in db["pharmacy"].find({"_id": {"$in": [ObjectId(pid) for pid in pharmacy_ids]}})}

    resp: List[SearchResponseItem] = []
    for inv in inventories:
        ph = pharmacies_map.get(inv["pharmacy_id"]) or {}
        distance = None
        if latitude is not None and longitude is not None and ph.get("latitude") is not None:
            try:
                distance = round(haversine_km(latitude, longitude, float(ph["latitude"]), float(ph["longitude"])), 2)
            except Exception:
                distance = None
        resp.append(SearchResponseItem(
            inventory_id=str(inv["_id"]),
            pharmacy_id=inv["pharmacy_id"],
            pharmacy_name=ph.get("name", "Unknown"),
            pharmacy_address=ph.get("address", ""),
            medicine_name=inv.get("medicine_name"),
            dci=inv.get("dci"),
            barcode=inv.get("barcode"),
            price=float(inv.get("price", 0)),
            stock=int(inv.get("stock", 0)),
            distance_km=distance
        ))
    # Sort by distance then price
    resp.sort(key=lambda x: (x.distance_km if x.distance_km is not None else 1e9, x.price))
    return resp

# -----------------------------
# Orders
# -----------------------------

class CreateOrderRequest(BaseModel):
    user_name: str
    user_phone: str
    pharmacy_id: str
    items: List[OrderItem]
    delivery_method: Literal["delivery", "click_collect"]
    delivery_address: Optional[str] = None
    prescription_url: Optional[str] = None

class OrderResponse(BaseModel):
    order_id: str
    status: str
    total_amount: float
    delivery_fee: float
    service_fee: float

@app.post("/api/orders", response_model=OrderResponse)
def create_order(payload: CreateOrderRequest):
    # Validate inventory availability and compute totals
    total = 0.0
    requires_rx = False
    for item in payload.items:
        inv = db["inventory"].find_one({"_id": ObjectId(item.inventory_id)})
        if not inv:
            raise HTTPException(status_code=404, detail=f"Inventory not found: {item.inventory_id}")
        if inv["stock"] < item.quantity:
            raise HTTPException(status_code=400, detail=f"Insufficient stock for {inv['medicine_name']}")
        total += float(item.price) * int(item.quantity)
        if item.requires_prescription:
            requires_rx = True

    # Fees
    service_fee = round(max(0.01 * total, 100) / 100.0, 2)  # at least 1% or 1 unit
    delivery_fee = 0.0
    if payload.delivery_method == "delivery":
        # naive flat fee for MVP
        delivery_fee = 2.5

    # Require prescription URL if any item needs it
    status = "pending_validation"
    if requires_rx and not payload.prescription_url:
        raise HTTPException(status_code=400, detail="Prescription required for selected items")

    order_doc = Order(
        user_name=payload.user_name,
        user_phone=payload.user_phone,
        pharmacy_id=payload.pharmacy_id,
        items=payload.items,
        delivery_method=payload.delivery_method,
        delivery_address=payload.delivery_address,
        prescription_url=payload.prescription_url,
        status=status,
        service_fee=service_fee,
        delivery_fee=delivery_fee,
        total_amount=round(total + service_fee + delivery_fee, 2)
    )

    order_id = create_document("order", order_doc)
    return OrderResponse(order_id=order_id, status=status, total_amount=order_doc.total_amount, delivery_fee=delivery_fee, service_fee=service_fee)

class UpdateOrderStatusRequest(BaseModel):
    status: Literal["validated", "preparing", "out_for_delivery", "ready_for_pickup", "delivered", "cancelled"]

@app.get("/api/orders/{order_id}")
def get_order(order_id: str):
    doc = db["order"].find_one({"_id": ObjectId(order_id)})
    if not doc:
        raise HTTPException(status_code=404, detail="Order not found")
    doc["_id"] = str(doc["_id"])
    return doc

@app.put("/api/orders/{order_id}")
def update_order_status(order_id: str, payload: UpdateOrderStatusRequest):
    res = db["order"].update_one({"_id": ObjectId(order_id)}, {"$set": {"status": payload.status}})
    if res.matched_count == 0:
        raise HTTPException(status_code=404, detail="Order not found")
    return {"order_id": order_id, "status": payload.status}

@app.get("/api/orders")
def list_orders(user_phone: Optional[str] = None, pharmacy_id: Optional[str] = None):
    filter_q = {}
    if user_phone:
        filter_q["user_phone"] = user_phone
    if pharmacy_id:
        filter_q["pharmacy_id"] = pharmacy_id
    data = list(db["order"].find(filter_q).sort("created_at", -1))
    for d in data:
        d["_id"] = str(d["_id"])
    return data

# -----------------------------
# Simple ingestion endpoints for partners (for MVP/demo)
# -----------------------------

@app.post("/api/pharmacies")
def add_pharmacy(pharmacy: Pharmacy):
    ph_id = create_document("pharmacy", pharmacy)
    return {"id": ph_id}

@app.post("/api/medicines")
def add_medicine(medicine: Medicine):
    med_id = create_document("medicine", medicine)
    return {"id": med_id}

@app.post("/api/inventories")
def add_inventory(inventory: Inventory):
    # ensure references look like strings of ObjectId
    return {"id": create_document("inventory", inventory)}

# Optional: basic schema exposure for tooling
@app.get("/schema")
def get_schema_info():
    return {
        "collections": ["appuser", "pharmacy", "medicine", "inventory", "order"],
        "description": "Schemas defined in backend for FASO TiiM Roogo"
    }

if __name__ == "__main__":
    import uvicorn
    port = int(os.getenv("PORT", 8000))
    uvicorn.run(app, host="0.0.0.0", port=port)
