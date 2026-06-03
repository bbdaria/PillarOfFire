"""Prerecorded Hebrew demo calls + a tiny location gazetteer.

Scenario:
  call-1 + call-2  -> same incident (gas-station explosion/fire on Herzl St,
                      Tel Aviv) seen from two different callers.
  call-3           -> a separate, unrelated traffic accident on Route 6.
  call-4           -> noisy / partial call, weakly related to the Herzl event.

Each call is split into chunks to simulate a real-time transcript stream.
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
}

DEMO_CALLS = {
    "call-1": {
        "title": "פיצוץ בתחנת דלק - הרצל",
        "chunks": [
            "הלו, מוקד? יש פיצוץ ענק!",
            "בתחנת הדלק ברחוב הרצל בתל אביב.",
            "אני רואה עשן שחור וריח חזק של גז.",
            "יש אנשים פצועים על הרצפה, לפחות שניים.",
            "תשלחו אמבולנס מהר בבקשה, זה דחוף!",
        ],
    },
    "call-2": {
        "title": "שריפה ליד תחנת דלק - הרצל",
        "chunks": [
            "שלום, אני מתקשר מרחוב הרצל.",
            "יש שריפה גדולה ליד תחנת הדלק.",
            "שתי מכוניות עולות באש ואנשים צועקים.",
            "אני מריח גז חזק, אני חושש מפיצוץ נוסף.",
            "יש המון עשן, אני כמעט לא רואה כלום.",
        ],
    },
    "call-3": {
        "title": "תאונת דרכים - כביש 6",
        "chunks": [
            "כן שלום, הייתה תאונת דרכים.",
            "בכביש 6 ליד חדרה.",
            "שתי מכוניות התנגשו במהירות.",
            "יש נהג אחד פצוע אבל הוא בהכרה.",
            "צריך גרר ואמבולנס בבקשה.",
        ],
    },
    "call-4": {
        "title": "שיחה רעשנית / מידע חלקי",
        "chunks": [
            "הלו? הלו? אתם שומעים אותי?",
            "אני... לא בטוח... יש רעש חזק מאוד.",
            "נראה לי שזה ברחוב הרצל... יש עשן?",
            "הקשר גרוע, אני לא...",
        ],
    },
}
