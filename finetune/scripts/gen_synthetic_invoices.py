"""Deterministic synthetic invoice generator → raw/invoices.jsonl as {input: invoice text, output: JSON}.
The output JSON is the flat target (vendor_name, invoice_number, invoice_date, due_date, currency, subtotal, tax, total).
No LLM involved, so labels are exact — the eval is exact-match on fields."""
from __future__ import annotations
import json, random, sys
from datetime import date, timedelta
from pathlib import Path

N = int(sys.argv[1]) if len(sys.argv) > 1 else 240
rng = random.Random(7)
VENDORS = ["Northwind Analytics LLC", "Blue Yonder Logistics", "Fabrikam Services Inc", "Tailspin Toys", "Adventure Works Supply",
           "Coho Vineyard", "Wingtip Electrical", "Litware Consulting", "Proseware Systems", "Margie's Travel Co"]
CLIENTS = ["Contoso Dental Group PC", "Alpine Ski House", "Woodgrove Bank", "Trey Research", "Lucerne Publishing", "Humongous Insurance"]
ITEMS = [("Consulting hours", 150.0), ("Appliance setup", 3500.0), ("Monthly service fee", 600.0), ("On-site training (hours)", 150.0),
         ("Hardware: Mac mini 24GB", 999.0), ("Support retainer", 400.0), ("Data migration", 1200.0), ("Annual license", 2400.0)]
CUR = ["USD", "USD", "USD", "EUR", "CAD"]
LAYOUTS = [
    "{vendor}\n{vaddr}\n\nINVOICE\n\nInvoice Number: {num}\nInvoice Date: {idate}\nDue Date: {ddate}\nCurrency: {cur}\n\nBill To:\n{client}\n\n{lines}\n\n{sub_label}: {sub}\nTax ({taxpct}%): {tax}\nTOTAL DUE: {total}\n\nPayment terms: Net {net}.",
    "INVOICE #{num}\nFrom: {vendor}, {vaddr}\nTo: {client}\nDate: {idate}    Due: {ddate}    ({cur})\n\n{lines}\n\nSubtotal {sub}\nTax {taxpct}% {tax}\nAmount due {total}",
    "{vendor}\n{vaddr}\n{client}\n\nTax Invoice {num}\nIssued {idate} · Payable by {ddate} · All amounts in {cur}\n\n{lines}\n\nNet: {sub}\nVAT {taxpct}%: {tax}\nGross total: {total}\nTerms: {net} days",
]
def money(x: float) -> str: return f"{x:,.2f}"
out = Path("raw/invoices.jsonl"); out.parent.mkdir(exist_ok=True)
with out.open("w") as f:
    for i in range(N):
        vendor = rng.choice(VENDORS); client = rng.choice(CLIENTS); cur = rng.choice(CUR)
        num = f"{rng.choice(['INV','IN','#','INV-'])}{rng.randint(1000, 99999)}"
        idate = date(2026, rng.randint(1, 8), rng.randint(1, 28)); net = rng.choice([15, 30, 30, 45, 60]); ddate = idate + timedelta(days=net)
        k = rng.randint(1, 4); lines_l = []; sub = 0.0
        for _ in range(k):
            d, p = rng.choice(ITEMS); q = rng.randint(1, 6); a = round(q * p, 2); sub += a
            lines_l.append(f"{d:<34} {q:>3} {money(p):>12} {money(a):>12}")
        sub = round(sub, 2); taxpct = rng.choice([0, 5, 7.5, 8.25, 10.25, 20]); tax = round(sub * taxpct / 100, 2); total = round(sub + tax, 2)
        text = rng.choice(LAYOUTS).format(vendor=vendor, vaddr=f"{rng.randint(100,9999)} {rng.choice(['Harbor Ave','Pine St','Main St','5th Ave'])}, {rng.choice(['Seattle, WA','Portland, OR','Denver, CO','Austin, TX'])}",
                                          num=num, idate=idate.isoformat(), ddate=ddate.isoformat(), cur=cur, client=client, lines="\n".join(lines_l),
                                          sub_label=rng.choice(["Subtotal", "Sub-total", "Net"]), sub=money(sub), taxpct=taxpct, tax=money(tax), total=money(total), net=net)
        label = {"vendor_name": vendor, "invoice_number": num, "invoice_date": idate.isoformat(), "due_date": ddate.isoformat(),
                 "currency": cur, "subtotal": sub, "tax": tax, "total": total}
        f.write(json.dumps({"input": text, "output": json.dumps(label, separators=(",", ":"))}) + "\n")
print(f"wrote {N} pairs to {out}")
