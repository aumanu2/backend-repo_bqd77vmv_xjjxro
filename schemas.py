"""
Database Schemas for FASO TiiM Roogo

Each Pydantic model represents a collection in MongoDB. The collection name is the lowercase of the class name.
"""
from typing import Optional, List, Literal
from pydantic import BaseModel, Field

# Core user accounts (customer)
class AppUser(BaseModel):
    name: str = Field(..., description="Full name")
    phone: str = Field(..., description="Phone number")
    email: Optional[str] = Field(None, description="Email address")
    address: Optional[str] = Field(None, description="Default address")

# Partner pharmacy profile
class Pharmacy(BaseModel):
    name: str
    address: str
    phone: Optional[str] = None
    latitude: float = Field(..., description="GPS latitude")
    longitude: float = Field(..., description="GPS longitude")
    opening_hours: Optional[str] = None

# Canonical medicine catalog entry
class Medicine(BaseModel):
    name: str = Field(..., description="Commercial name")
    dci: Optional[str] = Field(None, description="DCI / INN")
    barcode: Optional[str] = Field(None, description="EAN/GS1 barcode")
    category: Optional[str] = None
    requires_prescription: bool = False

# Stock record per pharmacy for a medicine
class Inventory(BaseModel):
    pharmacy_id: str
    medicine_id: str
    medicine_name: str
    dci: Optional[str] = None
    barcode: Optional[str] = None
    price: float = Field(..., ge=0)
    stock: int = Field(..., ge=0)

# Item for orders
class OrderItem(BaseModel):
    inventory_id: str
    medicine_name: str
    price: float
    quantity: int = Field(..., ge=1)
    requires_prescription: bool = False

# Order with optional prescription
class Order(BaseModel):
    user_name: str
    user_phone: str
    pharmacy_id: str
    items: List[OrderItem]
    delivery_method: Literal["delivery", "click_collect"]
    delivery_address: Optional[str] = None
    prescription_url: Optional[str] = Field(None, description="Secure link to prescription scan/photo")
    status: Literal[
        "pending_validation",
        "validated",
        "preparing",
        "out_for_delivery",
        "ready_for_pickup",
        "delivered",
        "cancelled"
    ] = "pending_validation"
    service_fee: float = 0.0
    delivery_fee: float = 0.0
    total_amount: float = 0.0
