"""Target schemas. Every field carries a confidence so the review queue knows what to show a human."""
from __future__ import annotations
from datetime import date
from typing import Generic, TypeVar
from pydantic import BaseModel, Field, field_validator

T = TypeVar("T")

class F(BaseModel, Generic[T]):
    """A field with provenance: value, confidence 0-1, and the source snippet it came from."""
    value: T | None = None
    confidence: float = 0.0
    evidence: str = ""

class LineItem(BaseModel):
    description: F[str] = F()
    quantity: F[float] = F()
    unit_price: F[float] = F()
    amount: F[float] = F()

class Invoice(BaseModel):
    vendor_name: F[str] = F()
    vendor_address: F[str] = F()
    invoice_number: F[str] = F()
    invoice_date: F[date] = F()
    due_date: F[date] = F()
    currency: F[str] = F()
    subtotal: F[float] = F()
    tax: F[float] = F()
    total: F[float] = F()
    line_items: list[LineItem] = Field(default_factory=list)

class UtilityBill(BaseModel):
    provider: F[str] = F()
    account_number: F[str] = F()
    service_address: F[str] = F()
    billing_period_start: F[date] = F()
    billing_period_end: F[date] = F()
    usage_quantity: F[float] = F()
    usage_unit: F[str] = F()          # kWh, therms, gallons
    amount_due: F[float] = F()
    due_date: F[date] = F()

SCHEMAS = {"invoice": Invoice, "utility_bill": UtilityBill}
