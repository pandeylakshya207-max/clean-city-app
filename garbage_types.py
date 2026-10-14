"""Kinds of garbage the app can recognise, and which municipal team each one needs.

The CLIP model compares the photo with the descriptions below. The text side is turned into
numbers once on a laptop (python export_types.py) and saved in models/clip_types.json, so the
server only needs the image model.
"""

# key, name shown in the app, team that should be sent, descriptions given to the model
TYPES = [
    ("mixed", "Mixed household waste", "Sweeping and collection team", [
        "a photo of mixed household garbage bags dumped on the street",
        "a photo of an overflowing public garbage bin with mixed trash",
    ]),
    ("plastic", "Plastic and dry waste", "Dry waste collection team", [
        "a photo of plastic bags, bottles and wrappers dumped on the street",
        "a photo of a heap of plastic and paper waste on the roadside",
    ]),
    ("food", "Wet and food waste", "Wet waste auto-tipper team", [
        "a photo of rotting food waste and vegetable peels dumped on the street",
        "a photo of wet kitchen waste spilling out of garbage bags",
    ]),
    ("construction", "Construction debris", "Debris truck with a loader", [
        "a photo of construction debris, broken bricks and concrete rubble dumped on the roadside",
        "a photo of a pile of sand, stones and building rubble on the street",
    ]),
    ("garden", "Garden waste", "Garden waste team", [
        "a photo of cut tree branches, dry leaves and garden waste dumped on the roadside",
        "a photo of a heap of fallen leaves and trimmed plants on the street",
    ]),
    ("bulky", "Furniture and bulky items", "Bulky waste truck", [
        "a photo of an old mattress, sofa or broken furniture dumped on the street",
        "a photo of large discarded household items on the footpath",
    ]),
    ("ewaste", "E-waste", "E-waste collection team", [
        "a photo of discarded electronics, old computers, wires and broken appliances on the street",
        "a photo of old televisions, keyboards and circuit boards dumped on the roadside",
    ]),
    ("medical", "Medical waste", "Biomedical waste team (handle with care)", [
        "a photo of used syringes, masks, gloves and medicine packets dumped on the street",
        "a photo of hospital and medical waste lying on the roadside",
    ]),
    ("dead_animal", "Dead animal", "Carcass removal team (health department)", [
        "a photo of a dead animal lying on the road",
    ]),
]

DEFAULT_TYPE = "mixed"
MIN_CONFIDENCE = 0.35

INFO = {key: {"key": key, "name": name, "team": team} for key, name, team, _ in TYPES}


def info(key):
    return INFO.get(key or DEFAULT_TYPE, INFO[DEFAULT_TYPE])
