import os
import json
import base64
import asyncio
import websockets
from fastapi import FastAPI, WebSocket, Request
from fastapi.responses import HTMLResponse, JSONResponse
from fastapi.websockets import WebSocketDisconnect
from twilio.twiml.voice_response import VoiceResponse, Connect
from twilio.rest import Client
from dotenv import load_dotenv

# Charger les variables d'environnement
load_dotenv()

OPENAI_API_KEY = os.getenv('OPENAI_API_KEY')
TWILIO_ACCOUNT_SID = os.getenv('TWILIO_ACCOUNT_SID')
TWILIO_AUTH_TOKEN = os.getenv('TWILIO_AUTH_TOKEN')
TWILIO_PHONE_NUMBER = os.getenv('TWILIO_PHONE_NUMBER')
PORT = int(os.getenv('PORT', 5050))

# Validation des credentials
if not all([OPENAI_API_KEY, TWILIO_ACCOUNT_SID, TWILIO_AUTH_TOKEN, TWILIO_PHONE_NUMBER]):
    raise ValueError("Variables d'environnement manquantes")

# Configuration OpenAI
SYSTEM_MESSAGE = "Vous êtes un assistant vocal professionnel. Répondez de manière concise en français."
VOICE = 'alloy'
OPENAI_MODEL = 'gpt-4o-mini-realtime-preview-2024-12-17'

app = FastAPI()
twilio_client = Client(TWILIO_ACCOUNT_SID, TWILIO_AUTH_TOKEN)

@app.get("/")
async def health_check():
    return {"status": "active", "service": "Outbound Call Gateway"}

@app.post("/outbound-call")
async def initiate_outbound_call(request: Request):
    """
    Endpoint pour déclencher un appel sortant.
    Attend un JSON avec : {"to": "+33612345678"}
    """
    try:
        data = await request.json()
        to_number = data.get('to')
        
        if not to_number or not to_number.startswith('+'):
            return JSONResponse(
                {"error": "Format de numéro invalide. Utilisez le format E.164 (ex: +33123456789)"},
                status_code=400
            )

        # Création de l'appel sortant (la logique outbound reste inchangée)
        call = twilio_client.calls.create(
            to=to_number,
            from_=TWILIO_PHONE_NUMBER,
            url=f"https://{request.url.hostname}/call-connected"
        )

        return JSONResponse({
            "status": "call_initiated",
            "call_sid": call.sid
        })

    except Exception as e:
        return JSONResponse(
            {"error": f"Erreur Twilio: {str(e)}"},
            status_code=500
        )

@app.post("/incoming-call")
def handle_incoming_call(request: Request):
    """
    Endpoint pour gérer les appels entrants.
    Cet endpoint génère le TwiML qui connecte l'appelant à notre WebSocket (/media-stream).
    """
    response = VoiceResponse()
    response.say("Bienvenue, votre appel est en cours de connexion. Veuillez patienter.")
    connect = Connect()
    connect.stream(url=f"wss://{request.url.hostname}/media-stream")
    response.append(connect)
    return HTMLResponse(content=str(response), media_type="application/xml")

@app.post("/call-connected")
def handle_call_connection(request: Request):
    """
    Endpoint utilisé par les appels sortants.
    Pour conserver la compatibilité, il redirige vers le même TwiML que pour les appels entrants.
    """
    return handle_incoming_call(request)

@app.websocket("/media-stream")
async def media_stream(websocket: WebSocket):
    """
    WebSocket pour gérer le flux audio entre Twilio et l'API Realtime d'OpenAI.
    Pipeline : Twilio -> pam_markII -> OpenAI Realtime -> pam_markII -> Twilio.
    """
    await websocket.accept()
    print("\n[WebSocket] Connexion Twilio établie")

    try:
        async with websockets.connect(
            f'wss://api.openai.com/v1/realtime?model={OPENAI_MODEL}',
            extra_headers={
                "Authorization": f"Bearer {OPENAI_API_KEY}",
                "OpenAI-Beta": "realtime=v1"
            }
        ) as openai_ws:
            # Initialisation de la session OpenAI
            session_update = {
                "type": "session.update",
                "session": {
                    "turn_detection": {"type": "server_vad"},
                    "input_audio_format": "g711_ulaw",
                    "output_audio_format": "g711_ulaw",
                    "voice": VOICE,
                    "instructions": SYSTEM_MESSAGE,
                    "modalities": ["text", "audio"],
                    "temperature": 0.7
                }
            }
            print("[Media-Stream] Sending session update:", json.dumps(session_update))
            await openai_ws.send(json.dumps(session_update))

            stream_sid = None
            
            async def handle_twilio_messages():
                nonlocal stream_sid
                async for message in websocket.iter_text():
                    data = json.loads(message)
                    if data.get('event') == 'start':
                        stream_sid = data['start']['streamSid']
                        print(f"[Media-Stream] Stream ID: {stream_sid}")
                        # Déclencher la première réponse pour l'appel entrant
                        await openai_ws.send(json.dumps({
                            "type": "response.create",
                            "response": {
                                "modalities": ["audio"],
                                "instructions": "Dire 'Bonjour, je suis votre assistant vocal. Comment puis-je vous aider ?'"
                            }
                        }))
                    elif data.get('event') == 'media':
                        print("[Media-Stream] Received media from Twilio")
                        await openai_ws.send(json.dumps({
                            "type": "input_audio_buffer.append",
                            "audio": data['media']['payload']
                        }))
            
            async def handle_openai_messages():
                async for message in openai_ws:
                    data = json.loads(message)
                    if data.get('type') == 'response.audio.delta' and 'delta' in data:
                        print("[Media-Stream] Received audio delta from OpenAI")
                        await websocket.send_json({
                            "event": "media",
                            "streamSid": stream_sid,
                            "media": {"payload": data['delta']}
                        })

            await asyncio.gather(
                handle_twilio_messages(),
                handle_openai_messages()
            )

    except Exception as e:
        print(f"[ERREUR] {str(e)}")
    finally:
        await websocket.close()

if __name__ == "__main__":
    import uvicorn
    uvicorn.run(app, host="0.0.0.0", port=PORT)
