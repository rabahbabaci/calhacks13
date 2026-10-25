"""Floor layout from a vision report. No LLM and no photogrammetry.

Each named room gets its own size. A hallway is a corridor; other rooms attach to it.
Without a hallway, public rooms sit in one row and bedrooms or baths sit behind them.
Hazard markers sit on a wall, placed from the location text.
"""
import re

WALL_H = 2.6
INSET = 0.07
SIZES = {
    "hallway": (1.8, 6.0),
    "living": (5.2, 4.4),
    "dining": (3.8, 3.6),
    "kitchen": (3.6, 3.4),
    "bedroom": (4.0, 3.6),
    "bath": (2.2, 2.4),
    "closet": (1.6, 1.8),
    "room": (3.8, 3.4),
}


def hazard_id(h):
    return f"{h['hazard']}|{h.get('location', '')}"


def _norm(name):
    return re.sub(r"[^a-z0-9]+", " ", (name or "").lower()).strip()


def room_kind(name):
    n = _norm(name)
    if any(word in n for word in ("hall", "corridor")):
        return "hallway"
    if any(word in n for word in ("bath", "toilet", "restroom", "wc")):
        return "bath"
    if "kitchen" in n or "cook" in n:
        return "kitchen"
    if "dining" in n or n.startswith("din"):
        return "dining"
    if any(word in n for word in ("liv", "lounge", "family")):
        return "living"
    if any(word in n for word in ("bed", "primary", "master")) or n in {"br"}:
        return "bedroom"
    if any(word in n for word in ("closet", "wardrobe", "pantry")):
        return "closet"
    return "room"


def _size(kind):
    return SIZES.get(kind, SIZES["room"])


def _poly(x0, z0, width, depth):
    return [
        [round(x0, 2), round(z0, 2)],
        [round(x0 + width, 2), round(z0, 2)],
        [round(x0 + width, 2), round(z0 + depth, 2)],
        [round(x0, 2), round(z0 + depth, 2)],
    ]


def _bounds(poly):
    xs = [p[0] for p in poly]
    zs = [p[1] for p in poly]
    return min(xs), min(zs), max(xs), max(zs)


def _wall_point(poly, wall, t, y):
    x0, z0, x1, z1 = _bounds(poly)
    t = min(0.86, max(0.14, t))
    if wall == "north":
        return [round(x0 + (x1 - x0) * t, 2), round(y, 2), round(z1 - INSET, 2)]
    if wall == "south":
        return [round(x0 + (x1 - x0) * t, 2), round(y, 2), round(z0 + INSET, 2)]
    if wall == "east":
        return [round(x1 - INSET, 2), round(y, 2), round(z0 + (z1 - z0) * t, 2)]
    return [round(x0 + INSET, 2), round(y, 2), round(z0 + (z1 - z0) * t, 2)]


def _place_on_wall(poly, location, door_wall, index, count):
    text = (location or "").lower()
    opposite = {"north": "south", "south": "north", "east": "west", "west": "east"}
    wall = opposite.get(door_wall, "north")
    if any(word in text for word in ("window", "sill", "sliding")):
        wall = "north"
    elif "door" in text:
        wall = door_wall
    elif any(word in text for word in ("corner", "beside")):
        wall = "west" if index % 2 == 0 else "east"
    y = 1.35
    if any(word in text for word in ("ceiling", "above", "upper")):
        y = WALL_H - 0.32
    elif any(word in text for word in ("floor", "lower", "baseboard", "along the lower")):
        y = 0.38
    elif any(word in text for word in ("sill", "counter", "faucet", "sink")):
        y = 1.05
    t = (index + 1) / (count + 1)
    return _wall_point(poly, wall, t, y)


def _stack(items, z0, toward):
    """Place rooms in a row along X. toward is 1 (north of z0) or -1 (south of z0)."""
    x = 0.0
    placed = []
    for item in items:
        width, depth = item["size"]
        z = z0 if toward > 0 else z0 - depth
        door = "south" if toward > 0 else "north"
        poly = _poly(x, z, width, depth)
        placed.append({**item, "poly": poly, "door": door, "x": x, "width": width})
        x += width
    return placed, x


def _hallway_house(hall, north, south):
    north_row, north_w = _stack(north, 0, 1)
    south_row, south_w = _stack(south, 0, -1)
    length = max(_size("hallway")[1], north_w, south_w, 4.0)
    hall_d = _size("hallway")[0]
    if north_row:
        length = max(length, north_w)
    if south_row:
        length = max(length, south_w)
    hall_poly = _poly(0, 0, length, hall_d)
    for row, toward in ((north_row, 1), (south_row, -1)):
        z0 = hall_d if toward > 0 else 0
        x = 0.0
        for item in row:
            width, depth = item["size"]
            z = z0 if toward > 0 else z0 - depth
            item["poly"] = _poly(x, z, width, depth)
            item["door"] = "south" if toward > 0 else "north"
            x += width
    hall_item = {**hall, "poly": hall_poly, "door": "east"}
    return [hall_item, *north_row, *south_row]


def _row_house(public, private):
    if not private:
        row, _ = _stack(public, 0, 1)
        for item in row:
            item["door"] = "south"
        return row
    if not public:
        row, _ = _stack(private, 0, 1)
        for item in row:
            item["door"] = "south"
        return row
    south, south_w = _stack(public, 0, 1)
    for item in south:
        item["door"] = "north"
    north, _ = _stack(private, max(item["poly"][2][1] for item in south), 1)
    for item in north:
        item["door"] = "south"
    # Stretch the shorter row so the house reads as one block.
    long = max(south_w, sum(item["size"][0] for item in private))
    if south_w < long and south:
        extra = (long - south_w) / len(south)
        x = 0.0
        for item in south:
            w = item["size"][0] + extra
            d = item["size"][1]
            item["poly"] = _poly(x, 0, w, d)
            x += w
    return south + north


def _arrange(order, grouped):
    items = []
    for name in order:
        kind = room_kind(name)
        items.append({"name": name, "kind": kind, "size": _size(kind), "hazards": grouped[name]})
    halls = [item for item in items if item["kind"] == "hallway"]
    rest = [item for item in items if item["kind"] != "hallway"]
    if halls:
        rest.extend(halls[1:])
        north, south = [], []
        for item in rest:
            public = item["kind"] in {"living", "dining", "kitchen"}
            (north if public or len(north) <= len(south) else south).append(item)
        return _hallway_house(halls[0], north, south)
    public = [item for item in rest if item["kind"] in {"living", "dining", "kitchen"}]
    private = [item for item in rest if item["kind"] not in {"living", "dining", "kitchen"}]
    if public and private:
        return _row_house(public, private)
    return _row_house(rest, [])


def layout_from_report(report):
    grouped = {}
    order = []

    def add(name):
        name = name or "room"
        if name not in grouped:
            grouped[name] = []
            order.append(name)

    hazards = sorted(report.get("hazards") or [], key=lambda h: h.get("first_seen_s") or 0)
    for h in hazards:
        add(h.get("room") or "room")
        grouped[h.get("room") or "room"].append(h)
    for entry in report.get("rooms") or []:
        name = entry["room"] if isinstance(entry, dict) else entry
        add(name)

    placed = _arrange(order, grouped)
    rooms = []
    for i, item in enumerate(placed):
        found = item["hazards"]
        n = len(found)
        icons = []
        for j, h in enumerate(found):
            icons.append({
                "hazard_id": hazard_id(h),
                "icon": _place_on_wall(item["poly"], h.get("location"), item["door"], j, n),
            })
        rooms.append({
            "id": f"room_{i}",
            "name": item["name"],
            "kind": item["kind"],
            "polygon": item["poly"],
            "height": WALL_H,
            "door": {"wall": item["door"], "center": 0.5, "width": 0.9},
            "hazards": icons,
        })
    return {"rooms": rooms}
