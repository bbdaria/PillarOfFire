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

# Extra prerecorded calls used by the "upload a recording" flow. They are not
# part of the live demo scenario but produce meaningful structured data (and a
# cross-incident merge suggestion) when uploaded, simulating a real recording.
UPLOAD_CALLS = {
    "call-rec-1": {
        "title": "הקלטה: עד נוסף - הרצל",
        "chunks": [
            "מקליט הודעה למוקד.",
            "ראיתי את הפיצוץ ליד תחנת הדלק ברחוב הרצל בתל אביב.",
            "יש הרבה עשן והגיעו כוחות כיבוי.",
            "נראה שיש עוד פצוע אחד שלא טופל.",
        ],
    },
    "call-rec-2": {
        "title": "הקלטה: היפוך משאית - כביש 6",
        "chunks": [
            "הקלטה מהשטח.",
            "משאית התהפכה בכביש 6 ליד חדרה.",
            "יש פקק ענק והנהג לכוד בתא.",
            "צריך חילוץ וכבאית בדחיפות.",
        ],
    },
}

ALL_CALLS = {**DEMO_CALLS, **UPLOAD_CALLS}

# Which dispatcher handles each scripted demo call. call-1 (Daria) and call-2
# (Noa) describe the SAME gas-station event from two operators — this is what
# makes the headline merge suggestion cross-dispatcher.
CALL_DISPATCHER = {
    "call-1": "d-daria",
    "call-2": "d-noa",
    "call-3": "d-noa",
    "call-4": "d-daria",
}
