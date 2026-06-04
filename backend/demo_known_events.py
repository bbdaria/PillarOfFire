"""Seed demo data for the Known Large Events layer.

Five planned gatherings across the country, with time windows computed relative
to *now* so the demo always has a currently-active event (the Nova festival at
Re'im) that the gunfire demo call (call-5) will match — reproducing the headline
"known event nearby" alert. The rest are spread across the coming days to make
the calendar view and its filters meaningful.

Idempotent: seeding twice won't duplicate events (keyed by a stable demo id).
"""
from __future__ import annotations

from datetime import datetime, timedelta, timezone

from known_events import create_known_event
from store import store


def _iso(dt: datetime) -> str:
    return dt.isoformat()


def seed_known_events() -> None:
    """Insert the demo known events if they aren't already present."""
    if any(e.source == "manual" and e.id.startswith("demo-")
           for e in store.all_known_events()):
        return  # already seeded

    now = datetime.now(timezone.utc)

    specs = [
        # 1. Large music festival near Re'im — ACTIVE now (the Nova scenario).
        ("demo-nova", {
            "name": "פסטיבל מוזיקה נובה - רעים",
            "type": "festival", "expected_participants": 3500,
            "start_time": _iso(now - timedelta(hours=4)),
            "end_time": _iso(now + timedelta(hours=8)),
            "lat": 31.3850, "lng": 34.4500, "radius_meters": 1500,
            "organizer": "הפקות טבע ומוזיקה",
            "police_notes": "מתחם פתוח, גישה יחידה בכביש 232. תיאום עם משטרת הנגב.",
            "risk_notes": "ריכוז קהל צעיר גדול בשטח פתוח; מורכבות פינוי גבוהה.",
            "description": "מסיבת טבע גדולה עם אלפי משתתפים בשטח פתוח סמוך לרעים.",
        }),
        # 2. Demonstration in Tel Aviv — starting soon (this evening).
        ("demo-demo-tlv", {
            "name": "הפגנה בכיכר הבימה",
            "type": "political", "expected_participants": 12000,
            "start_time": _iso(now + timedelta(hours=6)),
            "end_time": _iso(now + timedelta(hours=10)),
            "address": "כיכר הבימה", "city": "תל אביב", "radius_meters": 400,
            "organizer": "מטה המחאה",
            "police_notes": "חסימות תנועה בשדרות רוטשילד; ניידות במקום.",
            "risk_notes": "קהל גדול, פוטנציאל לעומס ולחיכוכים.",
            "description": "הפגנה מתוכננת עם עשרות אלפי משתתפים במרכז תל אביב.",
        }),
        # 3. Football game in Haifa — tomorrow.
        ("demo-haifa-game", {
            "name": "משחק כדורגל - אצטדיון סמי עופר",
            "type": "sports", "expected_participants": 30000,
            "start_time": _iso(now + timedelta(days=1, hours=2)),
            "end_time": _iso(now + timedelta(days=1, hours=4)),
            "lat": 32.7889, "lng": 34.9650, "radius_meters": 500,
            "city": "חיפה", "address": "אצטדיון סמי עופר",
            "organizer": "ההתאחדות לכדורגל",
            "police_notes": "מאבטחים בכניסות; הפרדת אוהדים.",
            "risk_notes": "צפיפות קהל בכניסה וביציאה.",
            "description": "משחק ליגה עם קהל גדול באצטדיון סמי עופר.",
        }),
        # 4. Private large wedding — in two days.
        ("demo-wedding", {
            "name": "חתונה גדולה - גני האירועים ראשון לציון",
            "type": "private", "expected_participants": 800,
            "start_time": _iso(now + timedelta(days=2, hours=11)),
            "end_time": _iso(now + timedelta(days=2, hours=17)),
            "lat": 31.9730, "lng": 34.7925, "radius_meters": 150,
            "city": "ראשון לציון",
            "organizer": "אולמי השרון",
            "police_notes": "מתחם סגור, חניון תת-קרקעי.",
            "risk_notes": "מאות אורחים במבנה סגור; יציאות חירום מוגבלות.",
            "description": "אירוע פרטי גדול במתחם אולמות סגור.",
        }),
        # 5. Cultural event in Jerusalem — in three days.
        ("demo-jlm-culture", {
            "name": "פסטיבל אור בעיר העתיקה",
            "type": "cultural", "expected_participants": 20000,
            "start_time": _iso(now + timedelta(days=3, hours=10)),
            "end_time": _iso(now + timedelta(days=3, hours=16)),
            "lat": 31.7767, "lng": 35.2345, "radius_meters": 700,
            "city": "ירושלים", "address": "העיר העתיקה",
            "organizer": "עיריית ירושלים",
            "police_notes": "סמטאות צרות, גישה מוגבלת לכלי רכב.",
            "risk_notes": "צפיפות גבוהה בסמטאות; מורכבות פינוי וגישה.",
            "description": "פסטיבל תרבות לילי עם קהל גדול בעיר העתיקה בירושלים.",
        }),
    ]

    for event_id, payload in specs:
        evt = create_known_event(payload, source="manual", event_id=event_id)
        store.upsert_known_event(evt)
