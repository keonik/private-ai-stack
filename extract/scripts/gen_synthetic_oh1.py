"""Generate synthetic OH-1-shaped crash reports with known labels.

The real corpus this pipeline was measured on is 501 public records containing names, addresses and
phone numbers, so it can never be shown to anyone. This produces a publishable stand-in: the same
caption structure, the same flattened code legends that make these forms hard, and a ground-truth
label per document.

    python scripts/gen_synthetic_oh1.py 60 out/synthetic-oh1

Writes <n> PDFs plus labels.json ({filename: {fields...}}). Deterministic: seed 0 unless SEED is set.
"""
from __future__ import annotations

import json
import os
import random
import sys
from pathlib import Path

from reportlab.lib.pagesizes import letter
from reportlab.pdfgen import canvas

# City, county code and the agency that would actually file the report — kept consistent so the set
# does not read as obviously fake to someone who knows these forms.
PLACES = [
    ("Columbus", 25, "COLUMBUS DIVISION OF POLICE"),
    ("Dayton", 57, "DAYTON POLICE DEPARTMENT"),
    ("Toledo", 48, "TOLEDO POLICE DEPARTMENT"),
    ("Wadsworth", 52, "MEDINA COUNTY SHERIFFS OFFICE"),
    ("Springfield", 12, "OHIO STATE HIGHWAY PATROL"),
    ("Newark", 45, "LICKING COUNTY SHERIFFS OFFICE"),
]
OFFICERS = ["Reyes, Marta", "Okafor, Daniel", "Lindqvist, Erik", "Boateng, Yaa", "Kowalski, Piotr", "Nakamura, Rin"]
ROADS = ["Infirmary Rd", "Cemetery Pike", "State Route 4", "Granville Pike", "Morse Road", "Clough Pike",
         "Gravel Pit Road", "Butler St", "Navarre Ave", "Summit Pkwy"]

# Each template fixes the four booleans. The last two exist to catch the mistakes a small model
# actually makes: a citation that is not alcohol, and an animal named only in the code legend.
TEMPLATES = [
    ("Unit 1 was travelling {dir} on {road} and struck a deer that entered the roadway. The vehicle "
     "sustained front end damage. No injuries were reported.",
     dict(animal=True, ped=False, alc=False, inj=False)),
    ("Unit 1 failed to yield at the {road} intersection and struck Unit 2 in the driver side door. "
     "The driver of Unit 2 was transported to the hospital with a shoulder injury.",
     dict(animal=False, ped=False, alc=False, inj=True)),
    ("Unit 1 struck a pedestrian who was crossing {road} outside the marked crosswalk. The pedestrian "
     "was transported by medic with a leg injury.",
     dict(animal=False, ped=True, alc=False, inj=True)),
    ("Unit 1 was travelling {dir} on {road} when it left the roadway and struck a utility pole. The "
     "driver was arrested for OVI after failing field sobriety testing.",
     dict(animal=False, ped=False, alc=True, inj=False)),
    ("Unit 1 rear-ended Unit 2 which had slowed for traffic on {road}. Unit 1 was cited for ACDA. "
     "Both vehicles sustained minor damage and no injuries were reported.",
     dict(animal=False, ped=False, alc=False, inj=False)),
    ("Unit 1 was travelling {dir} on {road} and struck a bicyclist riding along the fog line. The "
     "bicyclist declined transport at the scene.",
     dict(animal=False, ped=True, alc=False, inj=False)),
    ("Units 1 and 2 collided while merging onto {road}. Unit 1 was cited for failure to yield. "
     "No injuries were reported by either driver.",
     dict(animal=False, ped=False, alc=False, inj=False)),
    ("Unit 1 swerved to avoid a deer on {road}, lost control and came to rest in a ditch. The driver "
     "complained of neck pain and was transported for evaluation.",
     dict(animal=True, ped=False, alc=False, inj=True)),
]

# Drawn on every page exactly as the state form does: the legend text is what makes a naive extractor
# report animals and fatalities on reports that mention neither.
LEGEND = [
    "INJURIES     1 - FATAL     2 - SUSPECTED SERIOUS     3 - SUSPECTED MINOR     4 - POSSIBLE     5 - NO APPARENT INJURY",
    "UNIT IN ERROR     98 - ANIMAL     99 - UNKNOWN",
    "CONDITIONS     1 - NONE     2 - ALCOHOL     3 - DRUGS     4 - FATIGUE     5 - MEDICATION",
    "PEDESTRIAN     1 - PEDESTRIAN     2 - BICYCLIST     3 - OTHER NON-MOTORIST",
]


def draw(path: Path, f: dict) -> None:
    c = canvas.Canvas(str(path), pagesize=letter)
    y = [750]

    def line(txt: str, size: int = 9, dy: int = 13) -> None:
        c.setFont("Helvetica", size)
        c.drawString(40, y[0], txt)
        y[0] -= dy

    line("OHIO TRAFFIC CRASH REPORT", 12, 20)
    line("LOCAL REPORT NUMBER *")
    line(f["report_number"])
    line("REPORTING AGENCY NAME *")
    line(f["reporting_agency"])
    line("COUNTY*")
    line(str(f["county_code"]))
    line("LOCATION: ")
    line("CITY, VILLAGE, TOWNSHIP")
    line("*")
    line(f["locality"])
    line("CRASH DATE / TIME*")
    line(f["crash_datetime"])
    line("OFFICER'S NAME*")
    line(f["officer_name"])
    y[0] -= 8
    for row in LEGEND:
        line(row, 7, 11)
    y[0] -= 8
    line("NARRATIVE")
    words, cur = f["_narrative"].split(), ""
    for w in words:
        if len(cur) + len(w) > 95:
            line(cur); cur = w
        else:
            cur = f"{cur} {w}".strip()
    if cur:
        line(cur)
    line("REPORT TAKEN BY")
    line("LAW ENFORCEMENT AGENCY")
    c.save()


def main() -> None:
    n = int(sys.argv[1]) if len(sys.argv) > 1 else 60
    out = Path(sys.argv[2] if len(sys.argv) > 2 else "out/synthetic-oh1")
    out.mkdir(parents=True, exist_ok=True)
    rng = random.Random(int(os.environ.get("SEED", "0")))
    labels = {}
    for i in range(n):
        narrative, truth = TEMPLATES[i % len(TEMPLATES)]
        city, county, agency = rng.choice(PLACES)
        road, direction = rng.choice(ROADS), rng.choice(["northbound", "southbound", "eastbound", "westbound"])
        f = {
            "report_number": f"{rng.randint(10, 99)}-{rng.randint(100000, 999999)}",
            "reporting_agency": agency,
            "county_code": county,
            "locality": city,
            "crash_datetime": f"{rng.randint(1, 12):02d}/{rng.randint(1, 28):02d}/2026 {rng.randint(0, 23):02d}:{rng.randint(0, 59):02d}",
            "officer_name": rng.choice(OFFICERS),
            "animal_involved": truth["animal"],
            "pedestrian_or_cyclist_involved": truth["ped"],
            "alcohol_or_drugs_suspected": truth["alc"],
            "injury_mentioned": truth["inj"],
            "_narrative": narrative.format(road=road, dir=direction),
        }
        name = f"oh1-synthetic-{i:03d}.pdf"
        draw(out / name, f)
        labels[name] = {k: v for k, v in f.items() if not k.startswith("_")}
    (out / "labels.json").write_text(json.dumps(labels, indent=1) + "\n")
    print(f"wrote {n} PDFs + labels.json to {out}")


if __name__ == "__main__":
    main()
