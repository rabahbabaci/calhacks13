"""Turn a floor plan, a cited square footage, or a vision report into room polygons.

No network. A traced plan is the shell. A cited area with no drawing sizes one box per
vision room. With neither, the boxes are the fixed walk-order schematic.
"""
import re
from collections import deque

from PIL import Image

SQM_PER_SQFT = 0.092903
ROOM_W = 4.0
ROOM_D = 3.5
GAP = 0.6
WALL_H = 2.6
ICON_Y = 1.4
DOOR_M = 0.9

DISCLAIMER = (
    "The shell is a floor plan or a row of boxes sized from a listing, not a survey. "
    "Hazard markers sit in the room with the same name."
)


def hazard_id(h):
    return f"{h['hazard']}|{h.get('location', '')}"


def _shoelace(poly):
    area = 0.0
    for i, p in enumerate(poly):
        q = poly[(i + 1) % len(poly)]
        area += p[0] * q[1] - q[0] * p[1]
    return area / 2


def _ccw(poly):
    if _shoelace(poly) < 0:
        poly = list(reversed(poly))
    return [[round(p[0], 2), round(p[1], 2)] for p in poly]


def _centroid(poly):
    area = _shoelace(poly)
    if abs(area) < 1e-6:
        return (sum(p[0] for p in poly) / len(poly), sum(p[1] for p in poly) / len(poly))
    cx = cy = 0.0
    for i, p in enumerate(poly):
        q = poly[(i + 1) % len(poly)]
        cross = p[0] * q[1] - q[0] * p[1]
        cx += (p[0] + q[0]) * cross
        cy += (p[1] + q[1]) * cross
    return (cx / (6 * area), cy / (6 * area))


def _bedroom(name):
    n = (name or "").lower()
    return "bed" in n or n.strip() in {"br", "primary", "master"}


def _norm(name):
    return re.sub(r"[^a-z0-9]+", " ", (name or "").lower()).strip()


def match_room(name, rooms):
    """Match a vision room to a plan room. One bedroom on the plan matches any bedroom name."""
    beds = [r for r in rooms if _bedroom(r["name"])]
    if _bedroom(name) and len(beds) == 1:
        return beds[0]
    key = _norm(name)
    for room in rooms:
        if _norm(room["name"]) == key:
            return room
    return None


def _edge_point(poly, which):
    """A point on the north or south edge, nudged toward the centroid."""
    best_i = 0
    best = None
    for i, p in enumerate(poly):
        q = poly[(i + 1) % len(poly)]
        z = (p[1] + q[1]) / 2
        if best is None or (which == "north" and z > best) or (which == "south" and z < best):
            best = z
            best_i = i
    p = poly[best_i]
    q = poly[(best_i + 1) % len(poly)]
    mx, mz = (p[0] + q[0]) / 2, (p[1] + q[1]) / 2
    cx, cz = _centroid(poly)
    dx, dz = cx - mx, cz - mz
    dist = (dx * dx + dz * dz) ** 0.5 or 1
    nudge = min(0.35, dist * 0.4)
    return [round(mx + dx / dist * nudge, 2), ICON_Y, round(mz + dz / dist * nudge, 2)]


def _place_icon(poly, location):
    """Sit the marker on a wall. Ceiling and floor notes stay on the wall, high or low."""
    text = (location or "").lower()
    xs = [p[0] for p in poly]
    zs = [p[1] for p in poly]
    x0, z0, x1, z1 = min(xs), min(zs), max(xs), max(zs)
    inset = 0.07
    y = 2.2 if "ceiling" in text else (0.38 if any(w in text for w in ("floor", "lower", "baseboard")) else ICON_Y)
    if "door" in text:
        return [round((x0 + x1) / 2, 2), y, round(z0 + inset, 2)]
    return [round((x0 + x1) / 2, 2), y, round(z1 - inset, 2)]


def _box(i, name, x0, width, depth, hazards):
    poly = _ccw([[x0, 0], [x0 + width, 0], [x0 + width, depth], [x0, depth]])
    return {
        "id": f"room_{i}",
        "name": name,
        "polygon": poly,
        "height": WALL_H,
        "door": {"wall": "south", "center": 0.5, "width": DOOR_M},
        "seen": True,
        "hazards": hazards,
    }


def _vision_groups(vision):
    grouped = {}
    order = []

    def add(name):
        if name not in grouped:
            grouped[name] = []
            order.append(name)

    hazards = sorted((vision or {}).get("hazards") or [], key=lambda h: h.get("first_seen_s") or 0)
    for h in hazards:
        add(h.get("room") or "room")
        grouped[h.get("room") or "room"].append(h)
    for entry in (vision or {}).get("rooms") or []:
        name = entry["room"] if isinstance(entry, dict) else entry
        add(name or "room")
    return order, grouped


def _icons_for(hazards, poly):
    icons = []
    for h in hazards:
        icons.append({"hazard_id": hazard_id(h), "icon": _place_icon(poly, h.get("location"))})
    return icons


def listing_shell(vision, square_feet):
    order, grouped = _vision_groups(vision)
    if not order:
        return None
    each = square_feet * SQM_PER_SQFT / len(order)
    depth = (each * ROOM_D / ROOM_W) ** 0.5
    width = each / depth
    rooms = []
    x = 0.0
    for i, name in enumerate(order):
        poly = _ccw([[x, 0], [x + width, 0], [x + width, depth], [x, depth]])
        rooms.append(_box(i, name, x, width, depth, _icons_for(grouped[name], poly)))
        x += width + GAP
    return rooms


def schematic_rooms(vision):
    order, grouped = _vision_groups(vision)
    rooms = []
    for i, name in enumerate(order):
        x0 = i * (ROOM_W + GAP)
        poly = _ccw([[x0, 0], [x0 + ROOM_W, 0], [x0 + ROOM_W, ROOM_D], [x0, ROOM_D]])
        rooms.append(_box(i, name, x0, ROOM_W, ROOM_D, _icons_for(grouped[name], poly)))
    return rooms


def _binary(image):
    im = image.convert("L")
    if _rendered(im):
        return None
    w, h = im.size
    px = im.load()
    wall = [[px[x, y] < 80 for x in range(w)] for y in range(h)]
    return wall, w, h


def _rendered(im):
    hist = im.histogram()
    return sum(hist[40:220]) / max(1, sum(hist)) > 0.25


def _dilate(wall, w, h, radius):
    out = [row[:] for row in wall]
    for y in range(h):
        for x in range(w):
            if not wall[y][x]:
                continue
            for dy in range(-radius, radius + 1):
                for dx in range(-radius, radius + 1):
                    nx, ny = x + dx, y + dy
                    if 0 <= nx < w and 0 <= ny < h:
                        out[ny][nx] = True
    return out


def _flood_outside(wall, w, h):
    outside = [[False] * w for _ in range(h)]
    q = deque()
    for x in range(w):
        for y in (0, h - 1):
            if not wall[y][x]:
                q.append((x, y))
    for y in range(h):
        for x in (0, w - 1):
            if not wall[y][x]:
                q.append((x, y))
    while q:
        x, y = q.popleft()
        if outside[y][x] or wall[y][x]:
            continue
        outside[y][x] = True
        for dx, dy in ((1, 0), (-1, 0), (0, 1), (0, -1)):
            nx, ny = x + dx, y + dy
            if 0 <= nx < w and 0 <= ny < h and not outside[ny][nx] and not wall[ny][nx]:
                q.append((nx, ny))
    return outside


def _components(wall, outside, w, h):
    seen = [[False] * w for _ in range(h)]
    comps = []
    for y in range(h):
        for x in range(w):
            if wall[y][x] or outside[y][x] or seen[y][x]:
                continue
            cells = []
            q = deque([(x, y)])
            seen[y][x] = True
            while q:
                cx, cy = q.popleft()
                cells.append((cx, cy))
                for dx, dy in ((1, 0), (-1, 0), (0, 1), (0, -1)):
                    nx, ny = cx + dx, cy + dy
                    if 0 <= nx < w and 0 <= ny < h and not seen[ny][nx] and not wall[ny][nx] and not outside[ny][nx]:
                        seen[ny][nx] = True
                        q.append((nx, ny))
            if len(cells) >= 40:
                comps.append(cells)
    return comps


def _outline(cells):
    """Rectilinear corners of a filled component, in image pixels (x, y)."""
    filled = set(cells)
    minx = min(c[0] for c in cells)
    maxx = max(c[0] for c in cells)
    miny = min(c[1] for c in cells)
    maxy = max(c[1] for c in cells)
    if len(cells) > 0.85 * (maxx - minx + 1) * (maxy - miny + 1):
        return [(minx, miny), (maxx, miny), (maxx, maxy), (minx, maxy)]
    start = min(cells, key=lambda c: (c[1], c[0]))
    dirs = [(1, 0), (0, 1), (-1, 0), (0, -1)]
    x, y = start
    d = 0
    corners = [(x, y)]
    for _ in range(len(cells) * 8):
        turned = False
        for turn in (3, 0, 1, 2):
            nd = (d + turn) % 4
            dx, dy = dirs[nd]
            nx, ny = x + dx, y + dy
            lx, ly = nx + dirs[(nd + 3) % 4][0], ny + dirs[(nd + 3) % 4][1]
            if (nx, ny) in filled and (lx, ly) not in filled:
                if turn != 0:
                    corners.append((x, y))
                x, y = nx, ny
                d = nd
                turned = True
                break
        if not turned or (x, y) == start and len(corners) > 2:
            break
    if len(corners) < 4:
        return [(minx, miny), (maxx, miny), (maxx, maxy), (minx, maxy)]
    return corners


def _opening_px(wall, w, h):
    """A doorway: a short hole in a wall, bounded by wall pixels on both sides."""
    best = 0
    for y in range(h):
        xs = [x for x in range(w) if wall[y][x]]
        for a, b in zip(xs, xs[1:]):
            gap = b - a - 1
            if 8 <= gap <= 48:
                best = max(best, gap)
    for x in range(w):
        ys = [y for y in range(h) if wall[y][x]]
        for a, b in zip(ys, ys[1:]):
            gap = b - a - 1
            if 8 <= gap <= 48:
                best = max(best, gap)
    return best


def _door_px(wall, outside, w, h):
    """Widest gap in the southern exterior wall, in pixels."""
    best = 0
    for y in range(h - 2, 0, -1):
        run = 0
        for x in range(w):
            above = y > 0 and not wall[y - 1][x] and not outside[y - 1][x]
            below = y + 1 < h and outside[y + 1][x]
            if above and below and not wall[y][x]:
                run += 1
                best = max(best, run)
            else:
                run = 0
        if best >= 6:
            return best
    return 0


def _scale_bar_px(wall, outside, w, h):
    best = 0
    for y in range(int(h * 0.85), h):
        run = 0
        for x in range(w):
            if wall[y][x] and outside[y][x - 1 if x else 0]:
                run += 1
                best = max(best, run)
            else:
                run = 0
    return best if best >= 8 else 0


def trace_plan(image, square_feet=None, scale_bar_meters=None):
    """Return room polygons in meters, or None when the image is not a closed line plan.

    scale is "drawing" when a scale bar was used, "door" when a doorway was taken as 0.9 m.
    """
    parsed = _binary(image)
    if parsed is None:
        return None
    wall, w, h = parsed
    closed = _dilate(wall, w, h, 16)
    outside = _flood_outside(closed, w, h)
    comps = _components(closed, outside, w, h)
    if not comps:
        return None
    door = _opening_px(wall, w, h)
    bar = _scale_bar_px(wall, outside, w, h) if scale_bar_meters else 0
    if bar:
        meters_per = scale_bar_meters / bar
        scale = "drawing"
    elif door >= 6:
        meters_per = DOOR_M / door
        scale = "door"
    else:
        return None
    rooms = []
    for i, cells in enumerate(comps):
        corners = _outline(cells)
        cx = sum(c[0] for c in corners) / len(corners)
        cy = sum(c[1] for c in corners) / len(corners)
        pad = 8
        corners = [(c[0] + (pad if c[0] >= cx else -pad), c[1] + (pad if c[1] >= cy else -pad)) for c in corners]
        poly = [[c[0] * meters_per, c[1] * meters_per] for c in corners]
        rooms.append({
            "id": f"room_{i}",
            "name": f"room {i + 1}",
            "polygon": _ccw(poly),
            "height": WALL_H,
            "door": {"wall": "south", "center": 0.5, "width": DOOR_M},
            "seen": False,
            "hazards": [],
        })
    _close_gaps(rooms)
    if square_feet:
        want = square_feet * SQM_PER_SQFT
        have = sum(abs(_shoelace(r["polygon"])) for r in rooms)
        if have > 0:
            factor = (want / have) ** 0.5
            for room in rooms:
                room["polygon"] = _ccw([[p[0] * factor, p[1] * factor] for p in room["polygon"]])
    return {"rooms": rooms, "scale": scale}


def _rect_bounds(poly):
    xs = [p[0] for p in poly]
    zs = [p[1] for p in poly]
    return min(xs), min(zs), max(xs), max(zs)


def _set_rect(room, x0, z0, x1, z1):
    room["polygon"] = _ccw([[x0, z0], [x1, z0], [x1, z1], [x0, z1]])


def _close_gaps(rooms):
    """Pull rooms together across a thin wall so a shared wall is one edge."""
    for i, a in enumerate(rooms):
        ax0, az0, ax1, az1 = _rect_bounds(a["polygon"])
        for b in rooms[i + 1:]:
            bx0, bz0, bx1, bz1 = _rect_bounds(b["polygon"])
            if min(az1, bz1) - max(az0, bz0) > 0.2:
                if 0 < bx0 - ax1 < 1.2:
                    mid = round((ax1 + bx0) / 2, 2)
                    _set_rect(a, ax0, az0, mid, az1)
                    _set_rect(b, mid, bz0, bx1, bz1)
                elif 0 < ax0 - bx1 < 1.2:
                    mid = round((bx1 + ax0) / 2, 2)
                    _set_rect(a, mid, az0, ax1, az1)
                    _set_rect(b, bx0, bz0, mid, bz1)
            if min(ax1, bx1) - max(ax0, bx0) > 0.2:
                ax0, az0, ax1, az1 = _rect_bounds(a["polygon"])
                bx0, bz0, bx1, bz1 = _rect_bounds(b["polygon"])
                if 0 < bz0 - az1 < 1.2:
                    mid = round((az1 + bz0) / 2, 2)
                    _set_rect(a, ax0, az0, ax1, mid)
                    _set_rect(b, bx0, mid, bx1, bz1)
                elif 0 < az0 - bz1 < 1.2:
                    mid = round((bz1 + az0) / 2, 2)
                    _set_rect(a, ax0, mid, ax1, az1)
                    _set_rect(b, bx0, bz0, bx1, mid)


def _share_edge(a, b):
    """True when two polygons share a vertical or horizontal edge."""
    def edges(poly):
        out = []
        for i, p in enumerate(poly):
            q = poly[(i + 1) % len(poly)]
            if abs(p[0] - q[0]) < 0.15 or abs(p[1] - q[1]) < 0.15:
                out.append((round(min(p[0], q[0]), 1), round(min(p[1], q[1]), 1),
                            round(max(p[0], q[0]), 1), round(max(p[1], q[1]), 1)))
        return out
    return bool(set(edges(a)) & set(edges(b)))


def place_on_plan(plan_rooms, vision):
    """Copy plan rooms and drop vision hazards into the matching room. Unmatched rooms get a box."""
    rooms = []
    for i, room in enumerate(plan_rooms):
        rooms.append({**room, "id": room.get("id") or f"room_{i}", "seen": False, "hazards": []})
    order, grouped = _vision_groups(vision)
    used = set()
    extras = []
    for name in order:
        hit = match_room(name, rooms)
        if hit is None or hit["id"] in used and _bedroom(name) and sum(1 for r in rooms if _bedroom(r["name"])) > 1:
            extras.append(name)
            continue
        if hit["id"] in used and _norm(hit["name"]) != _norm(name):
            extras.append(name)
            continue
        used.add(hit["id"])
        hit["seen"] = True
        hit["name"] = name
        hit["hazards"] = _icons_for(grouped[name], hit["polygon"])
    max_x = 0.0
    for room in rooms:
        max_x = max(max_x, max(p[0] for p in room["polygon"]))
    x = max_x + GAP
    for j, name in enumerate(extras):
        poly = _ccw([[x, 0], [x + ROOM_W, 0], [x + ROOM_W, ROOM_D], [x, ROOM_D]])
        rooms.append(_box(len(rooms) + j, name, x, ROOM_W, ROOM_D, _icons_for(grouped[name], poly)))
        x += ROOM_W + GAP
    return rooms


def placements_of(rooms):
    rows = []
    for room in rooms:
        for icon in room.get("hazards") or []:
            rows.append({"hazard_id": icon["hazard_id"], "room_id": room["id"], "icon": icon["icon"]})
    return rows


def report_from_rooms(address, rooms, shell, scale, source_url, square_feet):
    public = []
    for room in rooms:
        public.append({
            "id": room["id"],
            "name": room["name"],
            "polygon": room["polygon"],
            "height": WALL_H,
            "door": room.get("door") or {"wall": "south", "center": 0.5, "width": DOOR_M},
            "seen": bool(room.get("seen", True)),
        })
    return {
        "address": address,
        "source_url": source_url or "",
        "shell": shell,
        "scale": scale,
        "square_feet": square_feet,
        "rooms": public,
        "placements": placements_of(rooms),
        "disclaimer": DISCLAIMER,
    }


def build_report(address, kept, vision=None, image=None, scale_bar_meters=None):
    """Pick a traced plan, else boxes sized from a cited area, else the fixed schematic."""
    sqft = None
    for kind in ("sqft", "assessor_sqft"):
        for fact in kept or []:
            if fact["kind"] == kind:
                sqft = float(fact["value"])
                break
        if sqft:
            break
    plan_url = next((f["value"] for f in kept or [] if f["kind"] == "plan_url"), "")
    labels = [f["value"] for f in kept or [] if f["kind"] == "room_label"]
    if image is not None:
        traced = trace_plan(image, square_feet=sqft, scale_bar_meters=scale_bar_meters)
        if traced:
            rooms = traced["rooms"]
            if len(labels) == len(rooms):
                ordered = sorted(rooms, key=lambda r: min(p[0] for p in r["polygon"]))
                for room, name in zip(ordered, labels):
                    room["name"] = name
            if vision:
                rooms = place_on_plan(rooms, vision)
            return report_from_rooms(address, rooms, "plan", traced["scale"], plan_url, sqft)
    if sqft and _vision_groups(vision)[0]:
        return report_from_rooms(address, listing_shell(vision, sqft), "listing", "door", "", sqft)
    return report_from_rooms(address, schematic_rooms(vision or {}), "schematic", "fixed", "", None)


def layout_rooms(report):
    """The model reads hazards on each room. Placements are the contract's list of those."""
    by_room = {}
    for row in report.get("placements") or []:
        by_room.setdefault(row["room_id"], []).append({"hazard_id": row["hazard_id"], "icon": row["icon"]})
    rooms = []
    for room in report.get("rooms") or []:
        rooms.append({**room, "hazards": by_room.get(room["id"], [])})
    return rooms
