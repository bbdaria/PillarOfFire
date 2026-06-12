"""Auto-Operator: Twilio voice overflow intake for the 100 call center.

A guided, natural conversation: the system greets the caller in Hebrew and asks a
few short questions, using <Gather input="speech"> so Twilio detects end-of-speech
automatically (no key press). The spoken answers drive the flow immediately, and
the whole call is also recorded (caller track) and re-transcribed afterward by our
ivrit STT for an accurate Hebrew transcript in the dashboard.

Twilio has no Hebrew TTS, so prompts are pre-synthesized with macOS `say` (Carmit)
and played via <Play>. Decision-support only.
"""
from __future__ import annotations

import hashlib
import os
import subprocess
from datetime import datetime, timezone, timedelta
from typing import Dict, List, Optional, Tuple
from xml.sax.saxutils import quoteattr

# --- Hebrew prompts --------------------------------------------------------
GREETING = "משטרה שלום."
CLOSING = "תודה רבה. המידע הועבר לכוחות ההצלה. הישאר במקום בטוח."

# Few, broad questions — "what happened" also asks about casualties, so the caller
# is never asked again about something they already described.
STEPS: List[Dict] = [
    {"key": "name", "clip": "q_name.wav", "text": "מה שמך?"},
    {"key": "location", "clip": "q_location.wav",
     "text": "מאיפה אתה מתקשר? אמור את הכתובת או המיקום המדויק."},
    # "what happened" is open-ended — wait longer for silence so the caller can
    # describe the event (with natural pauses) without being cut off.
    {"key": "what", "clip": "q_what.wav", "speech_timeout": "6",
     "text": "מה קרה? תאר את האירוע, וציין אם יש נפגעים וכמה."},
]
STEP_LABEL = {"name": "שם", "location": "מיקום", "what": "תיאור"}

GREETING_CLIP = "greeting.wav"
CLOSING_CLIP = "closing.wav"

# --- synthesis / recognition config ----------------------------------------
TTS_VOICE = os.environ.get("HEBREW_TTS_VOICE", "Carmit")  # macOS `say` voice
AUDIO_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), "voice_audio")
SAY_LANG = "he-IL"  # <Gather> speech-recognition language
# Bias recognition toward emergency vocabulary.
SPEECH_HINTS = ("שריפה, פיצוץ, תאונה, תאונת דרכים, פצוע, פצועים, הרוג, ירי, יריות, "
                "מחבל, נשק, דם, דקירה, אמבולנס, משטרה, כבאית, עזרה, הצילו, גז, עשן, פיגוע, הצפה, רעידת אדמה, מפולת")

# --- Keyword triage --------------------------------------------------------
CRITICAL_KEYWORDS = ["ירי", "פצוע", "הצילו", "מחבל", "נשק", "דם", "דקירה", "פיגוע", "הצפה", "רעידת אדמה", "מפולת"]


def triage(transcript: str) -> Tuple[str, List[str]]:
    text = transcript or ""
    matched = [k for k in CRITICAL_KEYWORDS if k in text]
    return ("CRITICAL" if matched else "STANDARD"), matched


# --- Caller-ID repeat tracking (stress detection) --------------------------
_WINDOW = timedelta(minutes=int(os.environ.get("VOICE_REPEAT_WINDOW_MIN", "10")))
_CALLERS: Dict[str, List[datetime]] = {}


def _prune(caller_id: str, now: datetime) -> List[datetime]:
    times = [t for t in _CALLERS.get(caller_id, []) if t >= now - _WINDOW]
    _CALLERS[caller_id] = times
    return times


def record_call(caller_id: str) -> Tuple[int, bool]:
    if not caller_id:
        return 1, False
    now = datetime.now(timezone.utc)
    times = _prune(caller_id, now)
    times.append(now)
    return len(times), len(times) > 1


def caller_stats(caller_id: str) -> Tuple[int, bool]:
    if not caller_id:
        return 1, False
    count = max(len(_prune(caller_id, datetime.now(timezone.utc))), 1)
    return count, count > 1


def reset_callers() -> None:
    _CALLERS.clear()


# --- Per-call conversation state -------------------------------------------
_SESSIONS: Dict[str, Dict] = {}  # call_sid -> {step, answers, caller_id}


def start_session(call_sid: str, caller_id: str) -> None:
    _SESSIONS[call_sid] = {"step": 0, "answers": {}, "caller_id": caller_id}


def record_answer_and_next(call_sid: str, speech: str) -> Optional[Dict]:
    """Store the just-spoken answer and return the next question, or None when done."""
    sess = _SESSIONS.get(call_sid)
    if sess is None:
        return None
    idx = sess["step"]
    if 0 <= idx < len(STEPS):
        sess["answers"][STEPS[idx]["key"]] = (speech or "").strip()
    nxt = idx + 1
    if nxt < len(STEPS):
        sess["step"] = nxt
        return STEPS[nxt]
    sess["step"] = len(STEPS)
    return None


def get_answers(call_sid: str) -> Dict[str, str]:
    return dict(_SESSIONS.get(call_sid, {}).get("answers", {}))


def build_transcript(call_sid: str) -> str:
    answers = get_answers(call_sid)
    parts = [f"{STEP_LABEL.get(k, k)}: {v}" for k, v in answers.items() if v]
    return ". ".join(parts)


def end_session(call_sid: str) -> None:
    _SESSIONS.pop(call_sid, None)


# --- Hebrew prompt audio (Twilio has no Hebrew TTS -> we <Play> our own) ----
_CLIPS: Dict[str, str] = {GREETING_CLIP: GREETING, CLOSING_CLIP: CLOSING}
_CLIPS.update({s["clip"]: s["text"] for s in STEPS})


def ensure_audio() -> None:
    """(Re)generate the Hebrew prompt WAVs with macOS `say` if missing/changed."""
    os.makedirs(AUDIO_DIR, exist_ok=True)
    sig = hashlib.sha1(
        ("|".join([TTS_VOICE] + [f"{k}:{v}" for k, v in sorted(_CLIPS.items())])).encode("utf-8")
    ).hexdigest()
    stamp = os.path.join(AUDIO_DIR, ".sig")
    files_exist = all(os.path.exists(os.path.join(AUDIO_DIR, c)) for c in _CLIPS)
    if files_exist and os.path.exists(stamp) and open(stamp).read().strip() == sig:
        return
    try:
        for clip, text in _CLIPS.items():
            subprocess.run(
                ["say", "-v", TTS_VOICE, "--file-format=WAVE",
                 "--data-format=LEI16@8000", "-o", os.path.join(AUDIO_DIR, clip), text],
                check=True, capture_output=True,
            )
        with open(stamp, "w") as f:
            f.write(sig)
    except Exception:
        if not files_exist:
            raise


def is_clip(name: str) -> bool:
    return name in _CLIPS


# --- TwiML builders --------------------------------------------------------
def _play(clip: str) -> str:
    return f'<Play>/voice/audio/{clip}</Play>'


def _listen(action: str, speech_timeout: str = "auto") -> str:
    """A <Gather> that ONLY listens (no nested prompt). speechTimeout=auto lets
    Twilio detect end-of-speech; a number waits N s of silence (open-ended answers)."""
    return (
        f'<Gather input="speech" language="{SAY_LANG}" speechTimeout="{speech_timeout}" '
        f'hints={quoteattr(SPEECH_HINTS)} method="POST" '
        f'action={quoteattr(action)} actionOnEmptyResult="true"/>'
    )


def _gather(clips: List[str], action: str, speech_timeout: str = "auto") -> str:
    """Play the prompt clip(s) FULLY, THEN listen for speech.

    The prompts are played *before* (not nested inside) the <Gather>. Nesting a
    prompt inside a speech <Gather> lets Twilio barge in — it begins recognising
    as soon as it hears the caller (or background noise) and cuts the prompt off
    mid-sentence. Playing first guarantees the operator finishes the sentence,
    then we open the mic to capture the answer."""
    plays = "".join(_play(c) for c in clips)
    return (
        '<?xml version="1.0" encoding="UTF-8"?>'
        '<Response>'
        f'{plays}'
        f'{_listen(action, speech_timeout)}'
        '</Response>'
    )


def question_twiml(step: Dict, action: str, intro: bool = False) -> str:
    clips = ([GREETING_CLIP] if intro else []) + [step["clip"]]
    return _gather(clips, action, step.get("speech_timeout", "auto"))


def stream_then_question(stream_url: str, action: str) -> str:
    """Start a background media stream (caller audio -> our ivrit STT, live),
    play the greeting + first question FULLY, then listen. <Start><Stream> runs
    alongside; the prompts are played before <Gather> so they can't be barged."""
    return (
        '<?xml version="1.0" encoding="UTF-8"?>'
        '<Response>'
        f'<Start><Stream url={quoteattr(stream_url)} track="inbound_track"/></Start>'
        f'{_play(GREETING_CLIP)}{_play(STEPS[0]["clip"])}'
        f'{_listen(action, "auto")}'
        '</Response>'
    )


def closing_twiml() -> str:
    return (
        '<?xml version="1.0" encoding="UTF-8"?>'
        f'<Response>{_play(CLOSING_CLIP)}<Hangup/></Response>'
    )
