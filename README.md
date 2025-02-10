# pam_markII (Python Edition)

This repo demonstrates a Python server that integrates:
- **Twilio Voice & Media Streams** (for telephony),
- **OpenAI Realtime API** (model gpt-4o),
- STT and TTS in real-time over websockets.

## Files
- **main.py** : The core FastAPI application. It:
  - Exposes `"/incoming-call"` to Twilio, returning TwiML `<Connect><Stream>`.
  - Manages a WebSocket `"/media-stream"` that bridges audio Twilio <-> OpenAI.

- **requirements.txt** : dependencies

- **.gitignore** : excludes `.env` etc.

## Setup

1. **Python 3.9+** recommended
2. `pip install -r requirements.txt`
3. Create a `.env` with `OPENAI_API_KEY=sk-xxxx`
4. Run local: `python main.py`
5. Expose via ngrok or deploy to a service (Railway, etc.)

## Twilio Configuration

1. In the [Twilio Console](https://console.twilio.com/), buy a phone number with Voice.
2. "A Call Comes In" → **Webhook** → `POST` → `https://your-domain/incoming-call`
3. Test: call your number, the server reads a short greeting, then uses `<Connect><Stream>` to open a WS bridging the audio with OpenAI Realtime.

## Additional Info

- If you see "application error" or timeouts, ensure your TwiML is correct and returned quickly.
- If you see no "media" events, remove `track="..."` or check inbound/outbound call direction.
- Check logs for debug messages in each step.
