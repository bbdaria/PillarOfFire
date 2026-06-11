"""Known Large Events — the pre-known intelligence layer.

This module is intentionally self-contained and dependency-free so the demo
keeps running fully offline (matching the project's design principle):

  * geocoding is MOCKED via a small address/city gazetteer (or explicit lat/lng);
  * Excel import parses .csv natively and .xlsx via the stdlib (zipfile + XML),
    so no openpyxl/pandas are required;
  * matchIncidentToKnownEvents does the spatial + time-window matching that
    turns a nearby planned gathering into a dispatcher-facing context alert.

Nothing here makes operational decisions — it surfaces cautious, operational
*considerations* for a human dispatcher. Decision-support only.
"""
from __future__ import annotations

import base64
import csv
import io
import math
import os
import re
import zipfile
import xml.etree.ElementTree as ET
from datetime import datetime, timedelta, timezone
from typing import Dict, List, Optional, Tuple

from models import EventContextMatch, EventLocation, Incident, KnownEvent
from store import store

# --- tunables (all overridable via env, so this stays a *config*, not magic) ---
PROXIMITY_METERS = float(os.environ.get("KE_PROXIMITY_METERS", "800"))
STARTING_SOON_HOURS = float(os.environ.get("KE_STARTING_SOON_HOURS", "12"))
RECENTLY_ENDED_HOURS = float(os.environ.get("KE_RECENTLY_ENDED_HOURS", "6"))
# A gathering at/above this many people escalates an "inside+active" alert.
MASS_PARTICIPANTS = int(os.environ.get("KE_MASS_PARTICIPANTS", "1000"))

VALID_TYPES = {"political", "cultural", "private", "religious",
               "sports", "festival", "other"}

TYPE_HE = {
    "political": "פוליטי/הפגנה", "cultural": "תרבות", "private": "אירוע פרטי",
    "religious": "דתי", "sports": "ספורט", "festival": "פסטיבל", "other": "אחר",
}


# --- geocoding (mocked) ----------------------------------------------------

# A tiny address→coordinate gazetteer. Real deployments would call a geocoder
# behind this same shape; for the hackathon a lookup table is enough, and the
# caller may always pass explicit lat/lng to bypass it.
CITY_GAZETTEER: Dict[str, Tuple[float, float]] = {
    "תל אביב": (32.0809, 34.7806), "tel aviv": (32.0809, 34.7806),
    "ירושלים": (31.7683, 35.2137), "jerusalem": (31.7683, 35.2137),
    "חיפה": (32.7940, 34.9896), "haifa": (32.7940, 34.9896),
    "באר שבע": (31.2520, 34.7915), "beer sheva": (31.2520, 34.7915),
    "ראשון לציון": (31.9730, 34.7925),
    "נתניה": (32.3215, 34.8532), "netanya": (32.3215, 34.8532),
    "אשדוד": (31.8044, 34.6553), "אילת": (29.5577, 34.9519), "eilat": (29.5577, 34.9519),
    "רעים": (31.3850, 34.4500), "ראם": (31.3850, 34.4500), "reim": (31.3850, 34.4500),
    "שדרות": (31.5250, 34.5950), "אופקים": (31.3100, 34.6200),
    "כיכר רבין": (32.0808, 34.7805), "כיכר הבימה": (32.0739, 34.7790),
}


def geocode(raw_address: str, city: str = "") -> Optional[Tuple[float, float]]:
    """Best-effort mock geocode: match the most specific gazetteer key present.

    Returns (lat, lng) or None when nothing is recognised.
    """
    text = f"{raw_address} {city}".strip().lower()
    best_key, best_coords = None, None
    for key, coords in CITY_GAZETTEER.items():
        if key.lower() in text and (best_key is None or len(key) > len(best_key)):
            best_key, best_coords = key, coords
    return best_coords


# Street-level geocoding via OpenStreetMap Nominatim (free, no key). Cached and
# rate-respecting; falls back to the city gazetteer (and stays fully offline) if
# the service is unreachable.
_NOMINATIM_URL = os.environ.get("NOMINATIM_URL", "https://nominatim.openstreetmap.org/search")
_GEO_CACHE: Dict[str, Optional[Tuple[float, float]]] = {}


def geocode_precise(address: str) -> Optional[Tuple[float, float]]:
    """Resolve a free-text Israeli address to (lat, lng) at street level.

    Tries Nominatim first (street-accurate), then the city gazetteer. Results are
    cached so each distinct address hits the network at most once.
    """
    address = (address or "").strip()
    if not address:
        return None
    if address in _GEO_CACHE:
        return _GEO_CACHE[address]

    # Nominatim dislikes the Hebrew "רחוב" (street) prefix — drop it.
    query = re.sub(r"\bרחוב\b", " ", address).strip(" ,")
    coords: Optional[Tuple[float, float]] = None
    try:
        import requests
        r = requests.get(
            _NOMINATIM_URL,
            params={"format": "json", "limit": 1, "countrycodes": "il",
                    "accept-language": "he", "q": query},
            headers={"User-Agent": "PillarOfFire/1.0 (emergency dispatch demo)"},
            timeout=6,
        )
        r.raise_for_status()
        data = r.json()
        if data:
            coords = (float(data[0]["lat"]), float(data[0]["lon"]))
    except Exception:
        coords = None

    if coords is None:
        coords = geocode(address)  # offline fallback: city center
    if coords is not None:
        _GEO_CACHE[address] = coords
    return coords


# --- creation / status -----------------------------------------------------

def _parse_dt(value: str) -> Optional[datetime]:
    if not value:
        return None
    try:
        dt = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except Exception:
        return None
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    return dt


def compute_status(start_time: str, end_time: str, now: Optional[datetime] = None) -> str:
    """Derive scheduled|active|ended from the time window (cancelled is sticky)."""
    now = now or datetime.now(timezone.utc)
    start, end = _parse_dt(start_time), _parse_dt(end_time)
    if start and now < start:
        return "scheduled"
    if start and end and start <= now <= end:
        return "active"
    if end and now > end:
        return "ended"
    if start and end is None:
        return "active" if now >= start else "scheduled"
    return "scheduled"


def create_known_event(payload: dict, source: str = "manual",
                       event_id: Optional[str] = None) -> KnownEvent:
    """Build a KnownEvent from a (validated) payload, geocoding if needed."""
    loc = payload.get("location") or {}
    lat = loc.get("lat", payload.get("lat"))
    lng = loc.get("lng", payload.get("lng"))
    raw_address = loc.get("raw_address", payload.get("address", "")) or ""
    city = payload.get("city", "")
    normalized = loc.get("normalized_address", "") or raw_address
    if (lat is None or lng is None):
        hit = geocode(raw_address, city)
        if hit:
            lat, lng = hit
            if city and city not in normalized:
                normalized = f"{raw_address}, {city}".strip(", ")

    etype = (payload.get("type") or "other").strip().lower()
    if etype not in VALID_TYPES:
        etype = "other"

    start_time = payload.get("start_time", "")
    end_time = payload.get("end_time", "")
    status = payload.get("status") or compute_status(start_time, end_time)

    return KnownEvent(
        id=event_id or store.next_known_event_id(),
        name=payload.get("name", "").strip(),
        type=etype,
        description=payload.get("description", ""),
        expected_participants=int(payload.get("expected_participants") or 0),
        start_time=start_time,
        end_time=end_time,
        location=EventLocation(
            raw_address=raw_address,
            normalized_address=normalized,
            lat=lat, lng=lng,
            radius_meters=int(payload.get("radius_meters")
                              or loc.get("radius_meters") or 0),
        ),
        organizer=payload.get("organizer", ""),
        police_notes=payload.get("police_notes", ""),
        risk_notes=payload.get("risk_notes", ""),
        status=status,
        source=source,
    )


def refresh_status(evt: KnownEvent) -> KnownEvent:
    """Recompute live status from the clock unless the event was cancelled."""
    if evt.status != "cancelled":
        evt.status = compute_status(evt.start_time, evt.end_time)
    return evt


# --- Excel / CSV import ----------------------------------------------------

# Columns we accept. Required for a valid row: a name + a resolvable location.
COLUMN_ALIASES = {
    "event_name": "name", "name": "name",
    "event_type": "type", "type": "type",
    "expected_participants": "expected_participants", "participants": "expected_participants",
    "start_time": "start_time", "end_time": "end_time",
    "address": "address", "city": "city",
    "lat": "lat", "lng": "lng", "longitude": "lng", "latitude": "lat",
    "radius_meters": "radius_meters", "radius": "radius_meters",
    "organizer": "organizer", "description": "description",
    "police_notes": "police_notes", "risk_notes": "risk_notes",
}


def _norm_header(h: str) -> str:
    key = (h or "").strip().lower().replace(" ", "_")
    return COLUMN_ALIASES.get(key, key)


def _xlsx_rows(data: bytes) -> List[List[str]]:
    """Parse the first worksheet of an .xlsx file using only the stdlib."""
    ns = "{http://schemas.openxmlformats.org/spreadsheetml/2006/main}"
    with zipfile.ZipFile(io.BytesIO(data)) as zf:
        shared: List[str] = []
        if "xl/sharedStrings.xml" in zf.namelist():
            root = ET.fromstring(zf.read("xl/sharedStrings.xml"))
            for si in root.findall(f"{ns}si"):
                shared.append("".join(t.text or "" for t in si.iter(f"{ns}t")))
        # Pick the first worksheet by name.
        sheet_name = next((n for n in zf.namelist()
                           if n.startswith("xl/worksheets/") and n.endswith(".xml")), None)
        if not sheet_name:
            return []
        root = ET.fromstring(zf.read(sheet_name))
        rows: List[List[str]] = []
        for row in root.iter(f"{ns}row"):
            cells: List[str] = []
            for c in row.findall(f"{ns}c"):
                v = c.find(f"{ns}v")
                text = ""
                if v is not None and v.text is not None:
                    if c.get("t") == "s":  # shared-string index
                        try:
                            text = shared[int(v.text)]
                        except (ValueError, IndexError):
                            text = ""
                    else:
                        text = v.text
                elif c.get("t") == "inlineStr":
                    t = c.find(f"{ns}is/{ns}t")
                    text = t.text if t is not None else ""
                cells.append(text)
            rows.append(cells)
        return rows


def parse_import(filename: str, content: bytes) -> List[dict]:
    """Parse an uploaded .csv or .xlsx into a list of header-keyed dicts."""
    name = (filename or "").lower()
    if name.endswith(".xlsx"):
        rows = _xlsx_rows(content)
        if not rows:
            return []
        headers = [_norm_header(h) for h in rows[0]]
        out = []
        for r in rows[1:]:
            if not any((c or "").strip() for c in r):
                continue
            out.append({headers[i]: (r[i] if i < len(r) else "")
                        for i in range(len(headers))})
        return out
    # CSV (also handles tab/semicolon via sniffing, falls back to comma).
    text = content.decode("utf-8-sig", errors="replace")
    try:
        dialect = csv.Sniffer().sniff(text[:2048], delimiters=",;\t")
    except csv.Error:
        dialect = csv.excel
    reader = csv.reader(io.StringIO(text), dialect)
    rows = [r for r in reader if any((c or "").strip() for c in r)]
    if not rows:
        return []
    headers = [_norm_header(h) for h in rows[0]]
    return [{headers[i]: (r[i] if i < len(r) else "") for i in range(len(headers))}
            for r in rows[1:]]


def validate_row(row: dict) -> Tuple[Optional[dict], List[str]]:
    """Validate + normalize one import row.

    Returns (clean_payload, errors). A non-empty errors list means the row is
    invalid and must be shown to the user rather than imported.
    """
    errors: List[str] = []
    name = (row.get("name") or "").strip()
    if not name:
        errors.append("חסר שם אירוע (event_name)")

    etype = (row.get("type") or "other").strip().lower()
    if etype and etype not in VALID_TYPES:
        errors.append(f"סוג אירוע לא חוקי: '{etype}'")

    def _num(key):
        val = (row.get(key) or "").strip()
        if val == "":
            return None
        try:
            return float(val)
        except ValueError:
            errors.append(f"ערך מספרי לא חוקי בעמודה {key}: '{val}'")
            return None

    lat, lng = _num("lat"), _num("lng")
    participants = _num("expected_participants")
    radius = _num("radius_meters")

    address = (row.get("address") or "").strip()
    city = (row.get("city") or "").strip()
    if lat is None or lng is None:
        if not (address or city) or geocode(address, city) is None:
            errors.append("מיקום לא ניתן לזיהוי — ספק lat/lng או כתובת/עיר מוכרת")

    for key in ("start_time", "end_time"):
        val = (row.get(key) or "").strip()
        if val and _parse_dt(val) is None:
            errors.append(f"תאריך/שעה לא חוקיים בעמודה {key}: '{val}'")

    if errors:
        return None, errors

    payload = {
        "name": name,
        "type": etype,
        "expected_participants": int(participants) if participants else 0,
        "start_time": (row.get("start_time") or "").strip(),
        "end_time": (row.get("end_time") or "").strip(),
        "address": address, "city": city,
        "lat": lat, "lng": lng,
        "radius_meters": int(radius) if radius else 0,
        "organizer": (row.get("organizer") or "").strip(),
        "description": (row.get("description") or "").strip(),
        "police_notes": (row.get("police_notes") or "").strip(),
        "risk_notes": (row.get("risk_notes") or "").strip(),
    }
    return payload, []


def preview_import(filename: str, content: bytes) -> dict:
    """Parse + validate without inserting. Returns valid previews + invalid rows."""
    rows = parse_import(filename, content)
    valid, invalid = [], []
    for i, row in enumerate(rows):
        payload, errors = validate_row(row)
        if errors:
            invalid.append({"row": i + 2, "errors": errors, "data": row})  # +2: header + 1-index
        else:
            # Build a throwaway event to show geocoded coords in the preview.
            evt = create_known_event(payload, source="excel_import", event_id="preview")
            valid.append({"row": i + 2, "payload": payload,
                          "event": evt.model_dump()})
    return {"valid": valid, "invalid": invalid,
            "total": len(rows), "valid_count": len(valid), "invalid_count": len(invalid)}


def import_known_events(payloads: List[dict]) -> List[KnownEvent]:
    """Insert previously-validated payloads as excel_import known events."""
    created = []
    for p in payloads:
        evt = create_known_event(p, source="excel_import")
        store.upsert_known_event(evt)
        created.append(evt)
    return created


def decode_upload(content_b64: str) -> bytes:
    return base64.b64decode(content_b64 or "")


# --- matching: incident → known events -------------------------------------

def _haversine_m(lat1, lng1, lat2, lng2) -> float:
    r = 6371000.0
    dlat = math.radians(lat2 - lat1)
    dlng = math.radians(lng2 - lng1)
    h = (math.sin(dlat / 2) ** 2
         + math.cos(math.radians(lat1)) * math.cos(math.radians(lat2))
         * math.sin(dlng / 2) ** 2)
    return 2 * r * math.asin(math.sqrt(h))


def _time_relation(evt: KnownEvent, now: datetime) -> str:
    start, end = _parse_dt(evt.start_time), _parse_dt(evt.end_time)
    if start and end and start <= now <= end:
        return "active"
    if start and end is None and now >= start:
        return "active"
    if start and now < start and (start - now) <= timedelta(hours=STARTING_SOON_HOURS):
        return "starting_soon"
    if end and now > end and (now - end) <= timedelta(hours=RECENTLY_ENDED_HOURS):
        return "recently_ended"
    return "scheduled"


def _suggestion_for(evt: KnownEvent, alert_level: str) -> str:
    """A cautious, operational consideration (never a directive)."""
    base = {
        "festival": "שקול פוטנציאל אסון רב-נפגעים ומורכבות פינוי קהל.",
        "cultural": "שקול ריכוז קהל גדול ומורכבות גישה ופינוי.",
        "political": "שקול חשיפת קהל גדול ומתח אפשרי; תאם עם גורמי המשטרה בשטח.",
        "sports": "שקול צפיפות קהל ומורכבות גישת כלי חירום לאצטדיון.",
        "religious": "שקול ריכוז קהל גדול ורגישות; תאם פינוי מסודר.",
        "private": "שקול מספר משתתפים גדול במתחם סגור ומורכבות פינוי.",
        "other": "שקול חשיפת קהל גדול במקום.",
    }.get(evt.type, "שקול חשיפת קהל גדול במקום.")
    tail = " ודא מיקום מדויק, שאל על מספר הנפגעים, ושקול דיווח לדרג הפיקוד הרלוונטי."
    if alert_level == "critical":
        return base + tail
    return base


def _incident_coords(inc: Incident) -> Optional[Tuple[float, float]]:
    for loc in inc.locations or []:
        if loc.lat is not None and loc.lng is not None:
            return loc.lat, loc.lng
    return None


def match_incident_to_known_events(
        inc: Incident, now: Optional[datetime] = None) -> List[EventContextMatch]:
    """Return context-alert matches for one incident, strongest alert first.

    Only events that are spatially close AND time-relevant (active / starting
    soon / recently ended) become alerts; far-future or long-past events stay
    silent (they still appear subtly on the map, just without an alert).
    """
    now = now or datetime.now(timezone.utc)
    coords = _incident_coords(inc)
    if not coords:
        return []
    ilat, ilng = coords

    matches: List[EventContextMatch] = []
    for evt in store.all_known_events():
        if evt.status == "cancelled":
            continue
        loc = evt.location
        if loc.lat is None or loc.lng is None:
            continue
        dist = _haversine_m(ilat, ilng, loc.lat, loc.lng)
        radius = loc.radius_meters or 0
        if dist <= radius:
            relation = "inside"
        elif dist <= radius + PROXIMITY_METERS:
            relation = "nearby"
        else:
            continue

        time_rel = _time_relation(evt, now)
        if time_rel == "scheduled":
            continue  # spatially close but not time-relevant → no alert

        if relation == "inside" and time_rel == "active":
            level = "critical" if evt.expected_participants >= MASS_PARTICIPANTS else "important"
        elif time_rel == "active" or relation == "inside":
            level = "important"
        else:
            level = "info"

        rel_he = "בתוך" if relation == "inside" else "בקרבת"
        reason = (f"אירוע חירום זוהה {rel_he} אירוע ידוע: {evt.name}. "
                  f"{evt.expected_participants:,} משתתפים צפויים. "
                  f"סוג: {TYPE_HE.get(evt.type, evt.type)}. "
                  f"מרחק מהאירוע: {int(dist)} מ'.")

        matches.append(EventContextMatch(
            known_event_id=evt.id,
            distance_meters=int(dist),
            relation=relation,
            time_relation=time_rel,
            alert_level=level,
            reason=reason,
            name=evt.name, type=evt.type,
            expected_participants=evt.expected_participants,
            start_time=evt.start_time, end_time=evt.end_time,
            organizer=evt.organizer,
            police_notes=evt.police_notes, risk_notes=evt.risk_notes,
            suggestion=_suggestion_for(evt, level),
            lat=loc.lat, lng=loc.lng,
        ))

    order = {"critical": 0, "important": 1, "info": 2}
    matches.sort(key=lambda m: (order.get(m.alert_level, 3), m.distance_meters))
    return matches
