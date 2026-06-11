"""Rule-based Hebrew analyzer.

A deterministic, keyword-driven stand-in for an LLM so the demo runs offline.
It extracts the same structured fields the real model would, using a small
Hebrew lexicon. The interface is identical to ClaudeAnalyzer, so production can
swap one for the other freely.

NOTE: decision-support only. Output is a draft for a human responder, never a
substitute for their judgement or official protocol.
"""
from __future__ import annotations

import re
from typing import List, Optional

from models import CallAnalysis, Casualties, Location, Severity
from llm.base import Analyzer
from demo_data import GAZETTEER

# --- Hebrew lexicons -------------------------------------------------------

EVENT_KEYWORDS = [
    # (event_type, [hebrew triggers])
    ("explosion", ["פיצוץ", "התפוצצות", "פצצה"]),
    ("fire", ["שריפה", "שרפה", "אש", "עולות באש", "עולה באש", "בוער"]),
    ("traffic_accident", ["תאונה", "התנגש", "התנגשו", "תאונת דרכים", "פגע"]),
    ("shooting", ["ירי", "יריות", "יורים", "נשק", "אקדח", "מטח"]),
    ("medical", ["התקף", "לא נושם", "מחוסר הכרה", "דימום"]),
    ("hazmat", ["דליפה", "כימיקל", "רעיל"]),
]

HAZARD_KEYWORDS = [
    ("smoke", ["עשן"]),
    ("gas", ["גז"]),
    ("fire", ["אש", "שריפה", "בוער", "עולות באש", "עולה באש"]),
    ("explosion", ["פיצוץ", "התפוצצות"]),
    ("vehicle", ["מכונית", "מכוניות", "רכב", "רכבים"]),
]

URGENCY_KEYWORDS = ["מהר", "דחוף", "בבקשה", "מיד", "עכשיו", "צועקים", "צועק", "פצוע", "פצועים"]

PANIC_KEYWORDS = ["צועקים", "צועק", "פיצוץ", "מהר", "דחוף", "!"]
DISTRESS_KEYWORDS = ["חושש", "פוחד", "מפחיד", "לא רואה", "המון עשן"]

# Hebrew number words -> integer (small range is enough for casualty counts).
HEB_NUMBERS = {
    "אחד": 1, "אחת": 1, "שניים": 2, "שתי": 2, "שני": 2,
    "שלושה": 3, "שלוש": 3, "ארבעה": 4, "ארבע": 4, "חמישה": 5, "חמש": 5,
}


def _find_event_type(text: str) -> str:
    for event_type, triggers in EVENT_KEYWORDS:
        if any(t in text for t in triggers):
            return event_type
    return "unknown"


def _find_hazards(text: str) -> List[str]:
    found = []
    for hazard, triggers in HAZARD_KEYWORDS:
        if any(t in text for t in triggers) and hazard not in found:
            found.append(hazard)
    return found


def _find_location(text: str) -> Location:
    # Match the longest gazetteer key present in the text (most specific).
    best_key = None
    for key in GAZETTEER:
        if key in text and (best_key is None or len(key) > len(best_key)):
            best_key = key
    if best_key:
        g = GAZETTEER[best_key]
        return Location(
            raw_text=best_key,
            normalized=g["normalized"],
            lat=g["lat"],
            lng=g["lng"],
            confidence=0.8,
        )
    # Fallback: grab a "רחוב X" / "כביש X" phrase even if not in the gazetteer.
    m = re.search(r"(רחוב\s+\S+|כביש\s+\d+)", text)
    if m:
        return Location(raw_text=m.group(1), normalized=m.group(1), confidence=0.4)
    return Location(confidence=0.0)


def _count_after(text: str, anchor_words: List[str]) -> Optional[int]:
    """Find a count in a small window around a casualty anchor WORD.

    Two pitfalls this avoids:
      - "כביש 6" leaking into the injured count -> only look near the anchor.
      - substring matches like "מת" inside "מתקשר" -> match whole words only
        (an optional single-letter Hebrew prefix is allowed, e.g. "והרוגים").
    """
    for anchor in anchor_words:
        m_anchor = re.search(rf"(?<![֐-׿])[בהוכלמש]?{re.escape(anchor)}(?![֐-׿])", text)
        if not m_anchor:
            continue
        i = m_anchor.start()
        window = text[max(0, i - 30): i + len(anchor) + 30]
        m = re.search(r"(\d+)", window)
        if m:
            return int(m.group(1))
        for word, val in HEB_NUMBERS.items():
            if word in window:
                return val
        return 1  # mentioned but no count -> at least one
    return None


def _distress(text: str) -> str:
    if any(k in text for k in PANIC_KEYWORDS) and text.count("!") >= 1:
        return "panicked"
    if any(k in text for k in DISTRESS_KEYWORDS):
        return "distressed"
    if any(k in text for k in URGENCY_KEYWORDS):
        return "concerned"
    return "calm"


class MockAnalyzer(Analyzer):
    def analyze(self, transcript: str) -> CallAnalysis:
        text = transcript or ""

        event_type = _find_event_type(text)
        hazards = _find_hazards(text)
        location = _find_location(text)

        injured = _count_after(text, ["פצוע", "פצועים"])
        dead = _count_after(text, ["הרוג", "הרוגים", "מת", "מתים"])
        casualties = Casualties(
            injured=injured,
            dead=dead,
            unknown=(injured is None and dead is None),
        )

        urgency = [w for w in URGENCY_KEYWORDS if w in text]
        distress = _distress(text)

        # Missing information & follow-up questions (cautious, practical).
        missing: List[str] = []
        questions: List[str] = []
        if location.confidence < 0.6:
            missing.append("מיקום מדויק לא ברור")
            questions.append("מה הכתובת המדויקת? יש ציון דרך בולט בקרבת מקום?")
        if casualties.unknown:
            missing.append("מספר הפצועים לא ידוע")
            questions.append("כמה אנשים פצועים, ומה מצבם?")
        questions.append("האם יש סכנה מיידית במקום (אש, גז, התמוטטות)?")
        questions.append("האם אתה במקום בטוח? האם הגיעו כבר כוחות חירום?")
        if event_type == "unknown":
            missing.append("סוג האירוע לא ברור מהשיחה")
            questions.insert(0, "תוכל לתאר בדיוק מה אתה רואה כרגע?")

        summary = self._summarize(event_type, location, hazards, casualties)
        severity = score_severity(event_type, hazards, casualties, distress, num_calls=1)
        next_steps = recommended_steps(event_type, hazards, casualties)

        event_tag = {"explosion": "פיצוץ", "fire": "שריפה", "traffic_accident": "תאונת דרכים",
                     "shooting": "ירי", "medical": "אירוע רפואי", "hazmat": 'חומ"ס'}
        haz_tag = {"smoke": "עשן", "gas": "גז", "fire": "אש", "explosion": "פיצוץ", "vehicle": "רכב"}
        tags = ([event_tag[event_type]] if event_type in event_tag else []) + [haz_tag.get(h, h) for h in hazards]
        ambulance_needed = bool(injured) or event_type in ("medical", "traffic_accident", "explosion", "fire", "shooting")

        return CallAnalysis(
            summary=summary,
            event_type=event_type,
            tags=tags,
            ambulance_needed=ambulance_needed,
            location=location,
            casualties=casualties,
            hazards=hazards,
            people_involved=injured,
            urgency_indicators=urgency,
            distress_level=distress,
            missing_information=missing,
            suggested_questions=questions,
            severity=severity,
            recommended_next_steps=next_steps,
        )

    @staticmethod
    def _summarize(event_type, location, hazards, casualties) -> str:
        labels = {
            "explosion": "פיצוץ", "fire": "שריפה", "traffic_accident": "תאונת דרכים",
            "shooting": "אירוע ירי", "medical": "אירוע רפואי", "hazmat": "חומ\"ס",
            "unknown": "אירוע לא מזוהה",
        }
        parts = [labels.get(event_type, event_type)]
        if location.normalized:
            parts.append(f"ב{location.normalized}")
        if hazards:
            heb_haz = {"smoke": "עשן", "gas": "גז", "fire": "אש",
                       "explosion": "פיצוץ", "vehicle": "כלי רכב"}
            parts.append("סכנות: " + ", ".join(heb_haz.get(h, h) for h in hazards))
        if casualties.injured:
            parts.append(f"{casualties.injured} פצועים")
        return ". ".join(parts) + "."


# --- Severity & next-steps (shared so the matcher can re-score incidents) ---

HAZARD_WEIGHT = {"explosion": 4, "fire": 3, "gas": 3, "smoke": 1, "vehicle": 1}


def score_severity(event_type, hazards, casualties, distress, num_calls: int = 1) -> Severity:
    score = 1
    reasons: List[str] = []

    base = {"explosion": 5, "fire": 4, "hazmat": 5, "shooting": 6,
            "traffic_accident": 3, "medical": 3, "unknown": 1}.get(event_type, 1)
    score += base
    if base > 1:
        reasons.append(f"סוג אירוע ({event_type})")

    haz_pts = sum(HAZARD_WEIGHT.get(h, 0) for h in hazards)
    if haz_pts:
        score += min(haz_pts, 4)
        reasons.append("סכנות פעילות: " + ", ".join(hazards))

    if casualties.injured:
        score += min(casualties.injured, 3)
        reasons.append(f"{casualties.injured} פצועים")
    if casualties.dead:
        score += 3
        reasons.append(f"{casualties.dead} הרוגים")

    if distress in ("distressed", "panicked"):
        score += 1
        reasons.append("מצוקה גבוהה של המתקשר")

    if num_calls >= 2:
        score += 1
        reasons.append(f"{num_calls} שיחות מאשרות את האירוע")

    score = max(1, min(score, 10))
    if score >= 9:
        label = "critical"
    elif score >= 6:
        label = "high"
    elif score >= 4:
        label = "medium"
    else:
        label = "low"

    return Severity(score=score, label=label, reasoning="; ".join(reasons) or "מידע מוגבל")


def recommended_steps(event_type, hazards, casualties) -> List[str]:
    steps = [
        "ודא מיקום מדויק וגישה לכלי חירום.",
        "שאל כמה פצועים ומה מצבם.",
    ]
    if "fire" in hazards or "explosion" in hazards or event_type in ("fire", "explosion"):
        steps.append("הזעק כיבוי אש; הרחק אנשים מאזור הסכנה.")
    if "gas" in hazards:
        steps.append("חשד לדליפת גז — הרחק מקורות הצתה ושקול פינוי.")
    if event_type == "traffic_accident":
        steps.append("שלח אמבולנס וגרר; שקול חסימת נתיב.")
    steps.append("ודא שהמתקשר במקום בטוח ושמור איתו על קשר.")
    steps.append("המלצות אלו הן תמיכה בלבד — ההחלטה בידי המוקדן/ת.")
    return steps
