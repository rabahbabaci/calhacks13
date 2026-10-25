"""Schematic floor layout from a vision report. No LLM and no photogrammetry.

Rooms are boxes along the walk, ordered by the first time a hazard was seen.
Icons sit on the far wall of their room. Two hazards in one room are spread apart.
"""
ROOM_W = 4.0
ROOM_D = 3.5
GAP = 0.6
WALL_H = 2.6
ICON_Y = 1.4


def hazard_id(h):
    return f"{h['hazard']}|{h.get('location', '')}"


def layout_from_report(report):
    grouped = {}
    order = []

    def add(name):
        if name not in grouped:
            grouped[name] = []
            order.append(name)

    hazards = sorted(report.get("hazards") or [], key=lambda h: h.get("first_seen_s") or 0)
    for h in hazards:
        add(h.get("room") or "room")
        grouped[h.get("room") or "room"].append(h)
    for entry in report.get("rooms") or []:
        name = entry["room"] if isinstance(entry, dict) else entry
        add(name or "room")

    rooms = []
    for i, name in enumerate(order):
        x0 = round(i * (ROOM_W + GAP), 2)
        found = grouped[name]
        n = len(found)
        icons = []
        for j, h in enumerate(found):
            x = x0 + ROOM_W * (j + 1) / (n + 1)
            icons.append({
                "hazard_id": hazard_id(h),
                "icon": [round(x, 2), ICON_Y, round(ROOM_D - 0.2, 2)],
            })
        rooms.append({
            "id": f"room_{i}",
            "name": name,
            "polygon": [[x0, 0], [round(x0 + ROOM_W, 2), 0], [round(x0 + ROOM_W, 2), ROOM_D], [x0, ROOM_D]],
            "height": WALL_H,
            "door": {"wall": "south", "center": 0.5, "width": 0.9},
            "hazards": icons,
        })
    return {"rooms": rooms}
