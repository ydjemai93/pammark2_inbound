import os
import json
import base64
import asyncio
import websockets
from fastapi import FastAPI, WebSocket, Request
from fastapi.responses import HTMLResponse, JSONResponse
from fastapi.websockets import WebSocketDisconnect
from twilio.twiml.voice_response import VoiceResponse, Connect, Say, Stream
from twilio.rest import Client
from dotenv import load_dotenv

load_dotenv()

# Configuration
OPENAI_API_KEY = os.getenv('OPENAI_API_KEY')
PORT = int(os.getenv('PORT', 5050))
SYSTEM_MESSAGE = (
  """Tu es Pam, une agente téléphonique IA conçue pour présenter une démo aux utilisateurs ayant rempli un formulaire sur notre site web. Ton rôle est de donner un aperçu efficace et engageant de tes capacités en quelques phrases, sans réciter mécaniquement une liste de fonctionnalités. Ton objectif est de capter l’attention et de donner envie à ton interlocuteur d’en savoir plus.

### Instructions

- **Démarre avec un accueil chaleureux** en reconnaissant la soumission du formulaire. Sois naturelle et fluide, comme un vrai conseiller clientèle.
- **Présente succinctement tes compétences** dans une phrase claire et impactante. Évite de tout détailler immédiatement : "Je peux assister sur plusieurs aspects, que ce soit la gestion administrative, le support client, l'accompagnement commercial ou même des conseils techniques."
- **Adapte-toi au fil de la conversation**. Si l'utilisateur manifeste de l'intérêt pour un domaine précis, donne quelques détails supplémentaires, mais reste concis et engageant.
- **Favorise l’interaction** en posant des questions pour encourager la discussion, comme : "Avez-vous un besoin particulier en tête ?" ou "Souhaitez-vous un exemple concret ?"
- **Utilise un ton humain et dynamique**, évite toute récitation rigide. Mets-toi à la place d’un employé qui présenterait naturellement son service lors d’un échange téléphonique.

### Approche conversationnelle

1. **Accueil chaleureux**  
   *"Bonjour [Nom si disponible] ! Merci d’avoir pris le temps de remplir notre formulaire. Je suis Pam, et je suis là pour vous faire découvrir comment je peux vous assister."*

2. **Présentation rapide et fluide**  
   *"Mon objectif est simple : faciliter votre quotidien en gérant l’administratif, en répondant aux demandes clients, en accompagnant vos ventes et en vous apportant un support technique. Bref, une assistante polyvalente et efficace !"*

3. **Engagement et personnalisation**  
   *"Dites-moi, avez-vous un besoin précis en tête ? Je peux vous donner un exemple concret de ce que je peux faire pour vous."*

4. **Réponse aux demandes avec légèreté et précision**  
   - *Si l’utilisateur demande un exemple sur la gestion des rendez-vous* → *"Bien sûr ! Je peux organiser et suivre vos rendez-vous, envoyer des rappels et même gérer les changements de planning. Vous utilisez un outil spécifique pour cela ?"*
   - *Si l’utilisateur demande comment tu aides le service client* → *"Je peux prendre en charge les demandes clients, suivre les commandes et proposer des solutions adaptées en temps réel. Vous cherchez à améliorer votre support actuel ?"*
   - *Si l’utilisateur est intéressé par les ventes* → *"Je peux aider à qualifier vos prospects, répondre aux questions sur vos offres et orienter les clients vers la meilleure solution. Vous aimeriez tester comment cela fonctionne ?"*

5. **Clôture engageante**  
   *"Si cela vous intrigue, on peut essayer une courte mise en situation ! Vous voulez voir comment je réagirais à une demande spécifique ?"*

### Points clés
- **Ne récite pas une liste de fonctionnalités**, fais une présentation fluide et naturelle.
- **Encourage l’interaction en posant des questions**, plutôt que de tout expliquer d’un bloc.
- **Reste concise et percutante**, pour garder l’attention de l’utilisateur.
- **Garde un ton dynamique et humain**, comme un vrai conseiller qui met en avant son service avec enthousiasme.
"""

)
VOICE = 'alloy'
LOG_EVENT_TYPES = [
    'error', 'response.content.done', 'rate_limits.updated',
    'response.done', 'input_audio_buffer.committed',
    'input_audio_buffer.speech_stopped', 'input_audio_buffer.speech_started',
    'session.created'
]
SHOW_TIMING_MATH = False

app = FastAPI()

if not OPENAI_API_KEY:
    raise ValueError('Missing the OpenAI API key. Please set it in the .env file.')

# ---------------------------
# SECTION APPEL SORTANT (NE PAS MODIFIER)
# ---------------------------
TWILIO_ACCOUNT_SID = os.getenv('TWILIO_ACCOUNT_SID')
TWILIO_AUTH_TOKEN = os.getenv('TWILIO_AUTH_TOKEN')
TWILIO_FROM_NUMBER = os.getenv('TWILIO_FROM_NUMBER')
twilio_client = Client(TWILIO_ACCOUNT_SID, TWILIO_AUTH_TOKEN)

@app.get("/", response_class=JSONResponse)
async def index_page():
    return {"message": "Twilio Media Stream Server is running!"}

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
        call = twilio_client.calls.create(
            to=to_number,
            from_=TWILIO_FROM_NUMBER,
            url=f"https://{request.url.hostname}/call-connected"
        )
        return JSONResponse({
            "status": "call_initiated",
            "call_sid": call.sid
        })
    except Exception as e:
        return JSONResponse({"error": f"Erreur Twilio: {str(e)}"}, status_code=500)

@app.post("/call-connected")
def handle_call_connection(request: Request):
    """
    Génère le TwiML de connexion au WebSocket quand l'appel sortant est décroché.
    """
    response = VoiceResponse()
    connect = Connect()
    connect.stream(url=f"wss://{request.url.hostname}/media-stream")
    response.append(connect)
    return HTMLResponse(content=str(response), media_type="application/xml")

# ---------------------------
# NOUVELLE SECTION : APPEL ENTRANT
# ---------------------------
@app.api_route("/incoming-call", methods=["GET", "POST"])
async def handle_incoming_call(request: Request):
    """
    Génère le TwiML pour les appels entrants.
    Ce TwiML inclut un <Connect><Stream> redirigeant vers le WebSocket /media-stream.
    """
    response = VoiceResponse()
    response.say("Bienvenue, vous êtes connecté à notre assistant vocal.")
    response.pause(length=1)
    response.say("Vous pouvez commencer à parler.")
    host = request.url.hostname
    connect = Connect()
    connect.stream(url=f"wss://{host}/media-stream")
    response.append(connect)
    return HTMLResponse(content=str(response), media_type="application/xml")

# ---------------------------
# WebSocket pour le flux média (pour les appels entrants et sortants)
# ---------------------------
@app.websocket("/media-stream")
async def handle_media_stream(websocket: WebSocket):
    """
    Gère le flux média entre Twilio et l'API Realtime d'OpenAI.
    Pipeline: Twilio -> pam_markII -> OpenAI Realtime -> pam_markII -> Twilio
    """
    print("Client connected")
    await websocket.accept()
    try:
        async with websockets.connect(
            f'wss://api.openai.com/v1/realtime?model=gpt-4o-realtime-preview-2024-10-01',
            extra_headers={
                "Authorization": f"Bearer {OPENAI_API_KEY}",
                "OpenAI-Beta": "realtime=v1"
            }
        ) as openai_ws:
            await initialize_session(openai_ws)
            stream_sid = None
            latest_media_timestamp = 0
            last_assistant_item = None
            mark_queue = []
            response_start_timestamp_twilio = None
            
            async def receive_from_twilio():
                nonlocal stream_sid, latest_media_timestamp
                try:
                    async for message in websocket.iter_text():
                        data = json.loads(message)
                        if data.get('event') == 'media' and openai_ws.open:
                            latest_media_timestamp = int(data['media']['timestamp'])
                            audio_append = {
                                "type": "input_audio_buffer.append",
                                "audio": data['media']['payload']
                            }
                            await openai_ws.send(json.dumps(audio_append))
                        elif data.get('event') == 'start':
                            stream_sid = data['start']['streamSid']
                            print(f"Stream started: {stream_sid}")
                            response_start_timestamp_twilio = None
                            latest_media_timestamp = 0
                            last_assistant_item = None
                        elif data.get('event') == 'mark':
                            if mark_queue:
                                mark_queue.pop(0)
                except WebSocketDisconnect:
                    print("Twilio disconnected.")
                    if openai_ws.open:
                        await openai_ws.close()

            async def send_to_twilio():
                nonlocal stream_sid, last_assistant_item, response_start_timestamp_twilio
                try:
                    async for openai_message in openai_ws:
                        response_data = json.loads(openai_message)
                        if response_data.get('type') in LOG_EVENT_TYPES:
                            print(f"Received event: {response_data.get('type')}", response_data)
                        if response_data.get('type') == 'response.audio.delta' and 'delta' in response_data:
                            # Convertir et envoyer l'audio
                            audio_payload = base64.b64encode(base64.b64decode(response_data['delta'])).decode('utf-8')
                            audio_delta = {
                                "event": "media",
                                "streamSid": stream_sid,
                                "media": {
                                    "payload": audio_payload
                                }
                            }
                            await websocket.send_json(audio_delta)
                            if response_start_timestamp_twilio is None:
                                response_start_timestamp_twilio = latest_media_timestamp
                                if SHOW_TIMING_MATH:
                                    print(f"Setting start timestamp: {response_start_timestamp_twilio}ms")
                            if response_data.get('item_id'):
                                last_assistant_item = response_data['item_id']
                            await send_mark(websocket, stream_sid)
                        if response_data.get('type') == 'input_audio_buffer.speech_started':
                            print("User speech started detected.")
                            if last_assistant_item:
                                await handle_speech_started_event()
                except Exception as e:
                    print(f"Error in send_to_twilio: {e}")

            async def handle_speech_started_event():
                nonlocal response_start_timestamp_twilio, last_assistant_item
                print("Handling speech interruption.")
                if mark_queue and response_start_timestamp_twilio is not None:
                    elapsed_time = latest_media_timestamp - response_start_timestamp_twilio
                    if SHOW_TIMING_MATH:
                        print(f"Truncation time: {elapsed_time}ms")
                    if last_assistant_item:
                        truncate_event = {
                            "type": "conversation.item.truncate",
                            "item_id": last_assistant_item,
                            "content_index": 0,
                            "audio_end_ms": elapsed_time
                        }
                        await openai_ws.send(json.dumps(truncate_event))
                    await websocket.send_json({
                        "event": "clear",
                        "streamSid": stream_sid
                    })
                    mark_queue.clear()
                    last_assistant_item = None
                    response_start_timestamp_twilio = None

            async def send_mark(connection, stream_sid):
                if stream_sid:
                    mark_event = {
                        "event": "mark",
                        "streamSid": stream_sid,
                        "mark": {"name": "responsePart"}
                    }
                    await connection.send_json(mark_event)
                    mark_queue.append('responsePart')

            await asyncio.gather(receive_from_twilio(), send_to_twilio())
    except Exception as e:
        print(f"Error in media_stream: {e}")
    finally:
        await websocket.close()

async def send_initial_conversation_item(openai_ws):
    initial_conversation_item = {
        "type": "conversation.item.create",
        "item": {
            "type": "message",
            "role": "user",
            "content": [
                {
                    "type": "input_text",
                    "text": "Greet the user with 'Hello there! I am an AI voice assistant powered by Twilio and the OpenAI Realtime API. You can ask me for facts, jokes, or anything you can imagine. How can I help you?'"
                }
            ]
        }
    }
    await openai_ws.send(json.dumps(initial_conversation_item))
    await openai_ws.send(json.dumps({"type": "response.create"}))

async def initialize_session(openai_ws):
    session_update = {
        "type": "session.update",
        "session": {
            "turn_detection": {"type": "server_vad"},
            "input_audio_format": "g711_ulaw",
            "output_audio_format": "g711_ulaw",
            "voice": VOICE,
            "instructions": SYSTEM_MESSAGE,
            "modalities": ["text", "audio"],
            "temperature": 0.7,
            "frequency_penalty": 0.2,  # Réduit la répétition des phrases
            "presence_penalty": 0.4,  # Encourage l'IA à introduire des variations dans ses réponses
            "max_response_output_tokens": 150,  # Limite la longueur des réponses pour éviter de trop parler d'un coup
        }
    }
    print('Sending session update:', json.dumps(session_update))
    await openai_ws.send(json.dumps(session_update))

if __name__ == "__main__":
    import uvicorn
    uvicorn.run(app, host="0.0.0.0", port=PORT)
