"""Finds the BBMP ward a location falls in.

static/wards.geojson holds the 243 ward boundaries of the 2022 delimitation (from KSRSAC, shared by
DataMeet under CC BY-SA 2.5 India), simplified to about 10 metres so it also loads fast on phones.
"""
import json
import threading
from functools import lru_cache
from pathlib import Path

WARDS_FILE = Path(__file__).parent / "static" / "wards.geojson"
_wards = None
_lock = threading.Lock()


def _load():
    global _wards
    with _lock:
        if _wards is None:
            data = json.loads(WARDS_FILE.read_text(encoding="utf-8"))
            wards = []
            for f in data["features"]:
                rings = f["geometry"]["coordinates"]
                xs = [x for x, _ in rings[0]]
                ys = [y for _, y in rings[0]]
                wards.append({
                    "no": f["properties"]["no"],
                    "name": f["properties"]["name"],
                    "rings": rings,
                    "box": (min(xs), min(ys), max(xs), max(ys)),
                })
            _wards = wards
    return _wards


def _inside(ring, x, y):
    inside = False
    j = len(ring) - 1
    for i in range(len(ring)):
        xi, yi = ring[i]
        xj, yj = ring[j]
        if (yi > y) != (yj > y) and x < (xj - xi) * (y - yi) / (yj - yi) + xi:
            inside = not inside
        j = i
    return inside


@lru_cache(maxsize=20000)
def ward_for(lat, lon):
    """(ward number, ward name) for a location, or None outside the BBMP area."""
    try:
        wards = _load()
    except Exception as exc:
        print("Could not load ward boundaries:", exc, flush=True)
        return None
    x, y = float(lon), float(lat)
    for w in wards:
        x1, y1, x2, y2 = w["box"]
        if not (x1 <= x <= x2 and y1 <= y <= y2):
            continue
        if _inside(w["rings"][0], x, y) and not any(_inside(h, x, y) for h in w["rings"][1:]):
            return w["no"], w["name"]
    return None


def ward_dict(lat, lon):
    found = ward_for(round(float(lat), 5), round(float(lon), 5))
    if found is None:
        return None
    return {"no": found[0], "name": found[1]}


if __name__ == "__main__":
    for name, lat, lon in [
        ("Koramangala", 12.9352, 77.6245),
        ("Jayanagar", 12.9250, 77.5838),
        ("Malleshwaram", 13.0035, 77.5709),
        ("Whitefield", 12.9698, 77.7500),
        ("Mysuru (outside)", 12.2958, 76.6394),
    ]:
        print(f"{name}: {ward_dict(lat, lon)}")
