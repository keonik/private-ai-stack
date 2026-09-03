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

class CrashReportOH1(BaseModel):
    """Ohio OH-1 traffic crash report, page 1 (header + narrative).

    Deliberately excludes the coded boxes (crash severity, number of units, unit in error, hit/skip): in the
    flattened text stream their values sit next to the printed code legends and both small and large models
    mis-assign them. Those need a layout-aware reader; the fields below are unambiguous in the text."""
    report_number: F[str] = Field(default_factory=F, description="LOCAL REPORT NUMBER as printed, e.g. 26-29237")
    crash_datetime: F[str] = Field(default_factory=F, description="CRASH DATE / TIME as printed, MM/DD/YYYY HH:MM")
    county_code: F[int] = Field(default_factory=F, description="COUNTY number")
    locality: F[str] = Field(default_factory=F, description="Name under LOCATION: CITY, VILLAGE, TOWNSHIP, e.g. 'Ravenna (Township of)'. Not the locality code.")
    reporting_agency: F[str] = Field(default_factory=F, description="REPORTING AGENCY NAME")
    officer_name: F[str] = Field(default_factory=F, description="OFFICER'S NAME who took the report")
    animal_involved: F[bool] = Field(default_factory=F, description="True if the NARRATIVE says a deer or other animal was struck")
    pedestrian_or_cyclist_involved: F[bool] = Field(default_factory=F, description="True only if the NARRATIVE mentions a pedestrian or bicyclist; printed code legends do not count")
    alcohol_or_drugs_suspected: F[bool] = Field(default_factory=F, description="True only if the NARRATIVE mentions impairment, OVI, alcohol or drugs; printed code legends do not count")
    injury_mentioned: F[bool] = Field(default_factory=F, description="True if the NARRATIVE mentions an injury or someone transported")
    summary: F[str] = Field(default_factory=F, description="One sentence restating the NARRATIVE")

SCHEMAS = {"invoice": Invoice, "utility_bill": UtilityBill, "crash_oh1": CrashReportOH1}
