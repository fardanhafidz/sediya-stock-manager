from sqlmodel import SQLModel, Field, Relationship
from typing import Optional, List
from datetime import datetime
from enum import Enum


class UserRole(str, Enum):
    OPERATOR = "OPERATOR"
    ADMIN = "ADMIN"
    VENDOR = "VENDOR"


class ProcurementStatus(str, Enum):
    PENDING = "PENDING"
    WAITING_VENDOR = "WAITING_VENDOR"
    COMPARING = "COMPARING"
    APPROVED = "APPROVED"
    REJECTED = "REJECTED"


# ─── USERS ───────────────────────────────────────────────
class User(SQLModel, table=True):
    __tablename__ = "users"
    id: Optional[int] = Field(default=None, primary_key=True)
    phone_number: str = Field(unique=True, index=True)
    name: str
    role: UserRole
    is_active: bool = Field(default=True)
    created_at: datetime = Field(default_factory=datetime.utcnow)


# ─── MATERIALS ───────────────────────────────────────────
class Material(SQLModel, table=True):
    __tablename__ = "materials"
    id: Optional[int] = Field(default=None, primary_key=True)
    name: str = Field(index=True)
    unit: str  # sak, liter, buah, dll
    current_stock: float = Field(default=0)
    minimum_stock: float  # threshold stok kritis
    avg_daily_usage: float = Field(default=0)
    updated_at: datetime = Field(default_factory=datetime.utcnow)


# ─── USAGE LOGS ──────────────────────────────────────────
class UsageLog(SQLModel, table=True):
    __tablename__ = "usage_logs"
    id: Optional[int] = Field(default=None, primary_key=True)
    material_id: int = Field(foreign_key="materials.id")
    quantity_used: float
    reported_by: int = Field(foreign_key="users.id")
    raw_message: str  # pesan asli dari operator
    logged_at: datetime = Field(default_factory=datetime.utcnow)


# ─── VENDORS ─────────────────────────────────────────────
class Vendor(SQLModel, table=True):
    __tablename__ = "vendors"
    id: Optional[int] = Field(default=None, primary_key=True)
    name: str
    phone_number: str = Field(unique=True)
    materials_supplied: str  # JSON list of material IDs
    is_active: bool = Field(default=True)


# ─── PROCUREMENT REQUESTS ────────────────────────────────
class ProcurementRequest(SQLModel, table=True):
    __tablename__ = "procurement_requests"
    id: Optional[int] = Field(default=None, primary_key=True)
    material_id: int = Field(foreign_key="materials.id")
    requested_qty: float
    status: ProcurementStatus = Field(default=ProcurementStatus.PENDING)
    triggered_by: str  # "AUTO" atau nomor admin
    created_at: datetime = Field(default_factory=datetime.utcnow)
    resolved_at: Optional[datetime] = None


# ─── PRICE COMPARISONS ───────────────────────────────────
class PriceComparison(SQLModel, table=True):
    __tablename__ = "price_comparisons"
    id: Optional[int] = Field(default=None, primary_key=True)
    procurement_id: int = Field(foreign_key="procurement_requests.id")
    vendor_id: int = Field(foreign_key="vendors.id")
    quoted_price: Optional[float] = None
    lead_time_days: Optional[int] = None
    notes: Optional[str] = None
    responded_at: Optional[datetime] = None
    raw_response: Optional[str] = None  # pesan asli vendor
