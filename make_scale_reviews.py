"""Generate synthetic hotel reviews in ./scale_review (same shape as ./reviews).

Usage:  python make_scale_reviews.py [N]      (default N=2000)

Output:
  scale_review/<id>.txt   title / "Hotel, City, Country" / date / body (like reviews/*.txt)
  scale_review/truth.csv  ground-truth triples per file (for measuring extraction quality)
Deterministic (fixed seed) so results are reproducible.
"""
import csv
import random
import sys
from pathlib import Path

N = int(sys.argv[1]) if len(sys.argv) > 1 else 2000
OUT = Path(__file__).parent / "scale_review"
random.seed(42)

ADJ = ["Grand", "Royal", "Creek", "Harbour", "Park", "Garden", "Skyline", "Oasis", "Crown", "Metro",
       "Sunrise", "Pearl", "Summit", "Lagoon", "Heritage", "Palm", "Aurora", "Cedar", "Maple", "Coral"]
NOUN = ["View", "Plaza", "Residency", "Palace", "Lodge", "Court", "Tower", "Bay", "Gate", "House"]
HOTELS = sorted({f"{a} {n}" for a in ADJ for n in NOUN})[:150]            # canonical "X Y"
CITIES = [("Dubai", "UAE"), ("London", "UK"), ("Oslo", "Norway"), ("Lisbon", "Portugal"), ("Tokyo", "Japan"),
          ("Beijing", "China"), ("Berlin", "Germany"), ("Paris", "France"), ("Rome", "Italy"), ("Madrid", "Spain"),
          ("Cairo", "Egypt"), ("Mumbai", "India"), ("Karachi", "Pakistan"), ("Sydney", "Australia"),
          ("Toronto", "Canada"), ("Lima", "Peru"), ("Nairobi", "Kenya"), ("Seoul", "South Korea"),
          ("Vienna", "Austria"), ("Prague", "Czechia"), ("Dublin", "Ireland"), ("Athens", "Greece"),
          ("Doha", "Qatar"), ("Bangkok", "Thailand"), ("Hanoi", "Vietnam"), ("Zurich", "Switzerland")]
FACILITIES = ["swimming pool", "free wifi", "gym", "spa", "rooftop bar", "breakfast buffet", "airport shuttle",
              "business center", "parking", "restaurant", "room service", "conference rooms", "kids club",
              "sauna", "private beach", "laundry service", "concierge", "courtyard garden"]
CUSTOMERS = ["businessmen", "families", "tourists", "senior citizens", "couples", "backpackers",
             "students", "conference attendees"]
FIRST = ["Akiko", "Ryouta", "Hsiung", "Reina", "Issac", "John", "Maria", "Omar", "Sara", "Liam", "Fatima",
         "Chen", "Ana", "Noah", "Zainab", "Ravi", "Elena", "Yusuf", "Mei", "Carlos"]
LAST = ["Kaneko", "Sato", "Chuang", "Inoue", "Collett", "Doe", "Garcia", "Khan", "Ahmed", "Smith", "Ali",
        "Wang", "Silva", "Brown", "Hussain", "Patel", "Ivanova", "Demir", "Lin", "Lopez"]
REVIEWERS = [f"{f} {l}" for f in FIRST for l in LAST]                      # 400
TITLES = ["Great stay", "Average place - great location", "Not bad", "Loved it", "Disappointing service",
          "Perfect for business", "Would return", "Nice but noisy", "Lovely staff", "Value for money"]
FILLER = ["The room was clean and the bed comfortable.", "Staff were friendly and helpful at check-in.",
          "Noise from the street was a bit much at night.", "Breakfast had plenty of choice.",
          "Check-out was quick and painless.", "The lobby looks a little dated but charming.",
          "Water pressure in the shower could be better.", "Easy to get taxis outside the entrance."]


def alias(h):
    return random.choice([f"The {h} Hotel", f"{h} Hotel", f"the {h.lower()} hotel", f"{h} Hotel & Suites"])


def join(items):
    return items[0] if len(items) == 1 else ", ".join(items[:-1]) + " and " + items[-1]


OUT.mkdir(exist_ok=True)
rows = []
for i in range(N):
    hotel = random.choice(HOTELS)
    # skewed city choice -> Dubai is a "hub" (like a real supernode)
    city, country = CITIES[0] if random.random() < 0.25 else random.choice(CITIES[1:])
    facs = random.sample(FACILITIES, random.randint(2, 4))
    custs = random.sample(CUSTOMERS, random.randint(1, 2))
    reviewer = random.choice(REVIEWERS)
    name = alias(hotel)
    body = [random.choice(FILLER), f"I stayed at {name} in {city}.", f"The hotel has {join(facs)}.",
            random.choice(FILLER), f"It is popular with {join(custs)}.", random.choice(FILLER),
            f"Reviewed by {reviewer}."]
    date = f"{random.randint(1, 12)}/{random.randint(1, 28)}/{random.randint(2016, 2024)}"
    fid = f"{i:06d}"
    (OUT / f"{fid}.txt").write_text(
        f"{random.choice(TITLES)}\n{name}, {city}, {country}\n{date}\n" + " ".join(body) + "\n", encoding="utf-8")
    canon = f"{hotel} Hotel"
    rows += [(fid, canon, "is_located_in", city)]
    rows += [(fid, canon, "has_facilities", f) for f in facs]
    rows += [(fid, canon, "has_customers", c) for c in custs]
    rows += [(fid, canon, "has_reviewer", reviewer)]

with open(OUT / "truth.csv", "w", newline="", encoding="utf-8") as f:
    w = csv.writer(f)
    w.writerow(["file", "hotel", "relation", "object"])
    w.writerows(rows)
print(f"wrote {N} reviews and {len(rows)} ground-truth triples to {OUT}")
