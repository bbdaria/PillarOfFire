"""Configuration for the Hebrew voice-intake prototype.

Everything a non-engineer might want to tweak lives here: the spoken prompts,
the Twilio voice/language, the high-priority keyword list, and the repeat-caller
window. Keep code (triage.py / main.py) free of hard-coded strings so the
operations team can update wording and keywords without touching logic.
"""
from __future__ import annotations

# --- Twilio speech settings -------------------------------------------------
# Polly.Zeina is Amazon Polly's voice that supports Hebrew via Twilio <Say>.
# language must match for both <Say> (TTS) and <Gather input="speech"> (STT).
VOICE = "Polly.Zeina"
LANGUAGE = "he-IL"

# How long <Gather> waits for the caller to stop talking before posting the
# result, and an overall safety timeout (seconds).
SPEECH_TIMEOUT = "auto"   # Twilio auto-detects end of speech
GATHER_TIMEOUT = 8        # seconds of silence before giving up

# --- Spoken prompts (Hebrew) ------------------------------------------------
# Greeting played when the overflow call is answered. Asks the three core
# intake questions in one breath (name / location / what happened).
GREETING_TEXT = (
    "מ שלום. כל הנציגים תפוסים. "
    "אנא אמור את שמך, מאיפה אתה מתקשר, ומה קרה."
)

# Played if we answered but heard nothing to transcribe.
NO_INPUT_TEXT = (
    "לא שמענו את תשובתך. אנא נסה שוב או המתן על הקו לנציג."
)

# Final message before hang-up, after the report is captured.
CLOSING_TEXT = (
    "המידע הועבר לכוחות ההצלה. הישאר במקום בטוח."
)

# --- Triage: high-priority keywords ----------------------------------------
# If any of these appear in the transcript, the call is escalated to CRITICAL.
# Substring match (Hebrew), so root forms catch inflections (e.g. "פצוע"
# matches "פצועים"). Add/remove freely — order does not matter.
CRITICAL_KEYWORDS = [
    "ירי",      # gunfire / shooting
    "פצוע",     # injured (matches פצועים / פצועה)
    "הצילו",    # help (cry for help)
    "מחבל",     # terrorist / attacker
    "נשק",      # weapon
    "דם",       # blood
    "דקירה",    # stabbing
]

# --- Repeat-caller detection ------------------------------------------------
# A caller who rings more than once inside this window is flagged as a likely
# stressed / mass-incident caller. Tune to your call-center's reality.
REPEAT_CALLER_WINDOW_SECONDS = 300  # 5 minutes
