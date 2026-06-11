"""A tiny Hebrew location gazetteer used to geocode transcripts.

The mock analyzer matches location phrases in a transcript against this table to
drop map pins. A real deployment would call a geocoder behind the same shape.
"""
from __future__ import annotations

# Known Hebrew location phrases -> normalized name + coordinates, so the map
# can drop pins. The real system would call a geocoder behind this same shape.
GAZETTEER = {
    "רחוב הרצל תל אביב": {"normalized": "רחוב הרצל, תל אביב", "lat": 32.0613, "lng": 34.7745},
    "רחוב הרצל": {"normalized": "רחוב הרצל, תל אביב", "lat": 32.0613, "lng": 34.7745},
    "תחנת הדלק הרצל": {"normalized": "תחנת דלק, רחוב הרצל, תל אביב", "lat": 32.0620, "lng": 34.7750},
    "כביש 6 חדרה": {"normalized": "כביש 6, מחלף חדרה", "lat": 32.4370, "lng": 34.9540},
    "כביש 6": {"normalized": "כביש 6, מחלף חדרה", "lat": 32.4370, "lng": 34.9540},
    "חדרה": {"normalized": "אזור חדרה", "lat": 32.4340, "lng": 34.9196},
    # Near the Re'im / Nova festival site — used to demo the known-event alert.
    "רעים": {"normalized": "אזור רעים, עוטף עזה", "lat": 31.3855, "lng": 34.4512},
    "ראם": {"normalized": "אזור רעים, עוטף עזה", "lat": 31.3855, "lng": 34.4512},
    "מסיבה ברעים": {"normalized": "מתחם המסיבה, רעים", "lat": 31.3850, "lng": 34.4500},
}
