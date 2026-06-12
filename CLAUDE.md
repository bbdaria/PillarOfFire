> You are an agent developing the Pillar of Fire system,

### Role: 
You are an expert backend engineer specializing in telecom integrations, Python, and emergency response architectures. 

## Introduction

This project divides into two main ideas:
1. Dashboard system for managing and making connections between different 100 calls.
2. A auto response system answering citezens as an operator and asking informative questions to be later postprocessed by the first system in the dashboards.


## Dashboard

There are different dashboards for the different hierarchies in the 100:
1. The operator at the call center - looking at specific incidents.
2. The launcher "משגר": the operator passes the processed incidents to the launcher which then can look on a bit bigger picture and send police car etc.
3. The C2 (Command and Control) - recives info when there are big incidents which need bigger coordiantion between units (e.g. terror attacks). 

This is already quite well implemented and only needs refinements (you may remove this line when everything is done).


## Auto-Operator

### Context:
 We are building an automated overflow triage system for the Israeli 100 call center (equivalent to 911). When human dispatchers are overwhelmed, the PBX routes calls to our system. We need a backend to handle the incoming calls via Twilio, interact with the caller in Hebrew, extract emergency keywords from their speech, track repeat callers, and output a structured JSON payload for the dispatch dashboard.

**Technology Stack:**
*   **Language:** Python 3.10+
*   **Framework:** FastAPI (for handling webhooks asynchronously)
*   **Telecom:** Twilio API (using TwiML)
*   **State Management:** In-memory dictionary (for prototyping) or Redis (to track Caller ID frequencies)

### Core Requirements:

**1. Webhook Endpoints:**
*   Create a `/voice/incoming` POST endpoint to receive the initial call from Twilio. 
*   Create a `/voice/process_speech` POST endpoint to handle the transcribed text returned by Twilio.

**2. Call Flow & TwiML (Hebrew):**
*   When a call comes in, the system should answer and use the Twilio `<Say>` verb with `language="he-IL"` and `voice="Polly.Zeina"`.
*   **Greeting:** "מוקד מנהלת מאה שלום. כל הנציגים תפוסים. אנא אמור את שמך, מאיפה אתה מתקשר, ומה קרה." (Hello from the 100 center. All agents are busy. Please state your name, where you are calling from, and what happened.)
*   Use the `<Gather>` verb with `input="speech"`, `language="he-IL"`, and `action="/voice/process_speech"` to capture the caller's response. *(Note: We will eventually swap this out for our own custom STT model via Media Streams, but use Twilio's native speech gathering for this prototype).*

**3. Caller ID Tracking (Stress Detection):**
*   Extract the `From` parameter (Caller ID) from the Twilio request.
*   Maintain a counter for each phone number. If a number calls more than once within a short timeframe, set a boolean flag `is_repeat_caller` to `True`.

**4. Keyword Triage & Prioritization:**
*   Analyze the transcribed text for specific high-priority Hebrew keywords: `["ירי", "פצוע", "הצילו", "מחבל", "נשק", "דם", "דקירה"]`.
*   If any of these keywords are found, set a `priority` field to `"CRITICAL"`. Otherwise, set it to `"STANDARD"`.

**5. Data Output:**
*   Once the speech is processed, format the gathered data into a clean JSON structure containing: `call_sid`, `caller_id`, `call_count`, `transcript`, `priority`, `matched_keywords`, and `timestamp`.
*   Log this JSON cleanly to the console (this simulates sending it to the human dispatch dashboard).
*   End the call with a final `<Say>` prompt: "המידע הועבר לכוחות ההצלה. הישאר במקום בטוח." (The information has been forwarded to rescue forces. Stay in a safe place.)
