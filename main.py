"""
main.py - pam_markII

Ce script Python gère un assistant vocal en temps réel avec Twilio (Media Streams) et l'API Realtime d'OpenAI (GPT-4o).
- Reçoit un appel Twilio -> renvoie un TwiML <Connect><Stream> vers un WebSocket.
- Établit une connexion WS simultanée avec OpenAI Realtime (pour STT -> GPT -> TTS).
- Transfère l'audio à la volée entre Twilio et OpenAI, permettant un dialogue vocal IA en temps réel.

Le code a été adapté pour porter le nom "pam_markII". 
Aucune modification de logique, juste des ajouts de commentaires et renaming cosmetic.

Usage:
  1) Configurez vos variables d'environnement (OPENAI_API_KEY, PORT, etc.).
  2) Lancez:  python main.py
  3) Configurez Twilio "A call comes in" -> https://<domaine>/incoming-call
  4) Appelez votre numéro Twilio : vous entendrez un message, puis le pipeline audio sera relié à l'IA.
"""

import os
import json
import base64
import asyncio
import websockets
from fastapi import FastAPI, WebSocket, Request
from fastapi.responses import HTMLResponse, JSONResponse
from fastapi.websockets import WebSocketDisconnect
from twilio.twiml.voice_response import VoiceResponse, Connect, Say, Stream
from dotenv import load_dotenv

# Charge les variables d'environnement (ex: OPENAI_API_KEY, PORT)
load_dotenv()

# Récupération de la clé OpenAI Realtime
OPENAI_API_KEY = os.getenv('OPENAI_API_KEY')

# Port par défaut = 5050, ou récupère la variable d'env PORT
PORT = int(os.getenv('PORT', 5050))

# Message système (prompt "system") pour initier la personnalité de l'IA
SYSTEM_MESSAGE = (
    "You are a helpful and bubbly AI assistant who loves to chat about "
    "anything the user is interested in and is prepared to offer them facts. "
    "You have a penchant for dad jokes, owl jokes, and rickrolling – subtly. "
    "Always stay positive, but work in a joke when appropriate."
)

# Nom de la voix (dans la config "voice" pour l'API Realtime)
VOICE = 'alloy'

# Liste d'événements OpenAI Realtime qu'on log
LOG_EVENT_TYPES = [
    'error',
    'response.content.done',
    'rate_limits.updated',
    'response.done',
    'input_audio_buffer.committed',
    'input_audio_buffer.speech_stopped',
    'input_audio_buffer.speech_started',
    'session.created'
]

# Flag d'affichage de calculs temporels éventuels
SHOW_TIMING_MATH = False

# Création de l'application FastAPI pour pam_markII
app = FastAPI()

# Vérifie qu'une clé OpenAI est bien fournie
if not OPENAI_API_KEY:
    raise ValueError('Missing the OpenAI API key. Please set it in the .env file for pam_markII.')

@app.get("/", response_class=JSONResponse)
async def index_page():
    """
    Simple endpoint GET sur racine.
    Permet de vérifier que pam_markII tourne bien.
    """
    return {"message": "pam_markII (Twilio Media Stream + OpenAI Realtime) is running!"}

@app.api_route("/incoming-call", methods=["GET", "POST"])
async def handle_incoming_call(request: Request):
    """
    Gère l'appel entrant Twilio. Renvoie un TwiML <Connect><Stream> pour connecter Twilio
    au WebSocket /media-stream (et ensuite à l'API Realtime d'OpenAI).
    
    1) Lit param. Twilio (CallSid, etc.) -> pas forcément utile
    2) Construit un Twilio VoiceResponse (TwiML) 
    3) Dans <Connect>, inclut <Stream> vers /media-stream en wss
    """
    response = VoiceResponse()
    # On ajoute un message audio (Say) pour l'appelant
    response.say("Please wait while we connect your call to the A. I. voice assistant, powered by Twilio and the Open-A.I. Realtime API")
    response.pause(length=1)
    response.say("O.K. you can start talking!")

    # Récupère le host depuis la requête 
    host = request.url.hostname

    # On crée une balise <Connect><Stream> 
    connect = Connect()
    connect.stream(url=f'wss://{host}/media-stream')
    response.append(connect)

    # On renvoie ce TwiML sous forme d'HTMLResponse (Content-Type text/xml)
    return HTMLResponse(content=str(response), media_type="application/xml")

@app.websocket("/media-stream")
async def handle_media_stream(websocket: WebSocket):
    """
    WebSocket /media-stream : Twilio envoie l'audio G711 ulaw en base64.
    On relaye ensuite l'audio vers OpenAI Realtime (GPT-4o).
    Le pipeline:
      Twilio -> pam_markII -> OpenAI Realtime -> pam_markII -> Twilio
    """
    print("Client connected (Twilio side) - pam_markII media-stream")

    # Accepte la connexion WS Twilio 
    await websocket.accept()

    # Connexion simultanée avec OpenAI Realtime 
    # (model=gpt-4o-realtime-preview-2024-10-01 en exemple)
    async with websockets.connect(
        'wss://api.openai.com/v1/realtime?model=gpt-4o-realtime-preview-2024-10-01',
        extra_headers={
            "Authorization": f"Bearer {OPENAI_API_KEY}",
            "OpenAI-Beta": "realtime=v1"
        }
    ) as openai_ws:
        # On initialise la session (instructions, voix, etc.)
        await initialize_session(openai_ws)

        # Variables de contexte
        stream_sid = None
        latest_media_timestamp = 0
        last_assistant_item = None
        mark_queue = []
        response_start_timestamp_twilio = None
        
        async def receive_from_twilio():
            """
            Reçoit l'audio et les événements Twilio (start, media, stop) 
            puis envoie l'audio à l'OpenAI Realtime WS.
            """
            nonlocal stream_sid, latest_media_timestamp
            try:
                async for message in websocket.iter_text():
                    data = json.loads(message)
                    if data['event'] == 'media' and openai_ws.open:
                        # On récupère l'audio base64
                        latest_media_timestamp = int(data['media']['timestamp'])
                        audio_append = {
                            "type": "input_audio_buffer.append",
                            "audio": data['media']['payload']
                        }
                        # On envoie l'audio au WS openai
                        await openai_ws.send(json.dumps(audio_append))
                    elif data['event'] == 'start':
                        stream_sid = data['start']['streamSid']
                        print(f"Incoming stream has started: {stream_sid}")
                        response_start_timestamp_twilio = None
                        latest_media_timestamp = 0
                        last_assistant_item = None
                    elif data['event'] == 'mark':
                        # On gère les 'mark' => correspondances TTS
                        if mark_queue:
                            mark_queue.pop(0)
            except WebSocketDisconnect:
                print("Client disconnected (Twilio).")
                # Si openai_ws est encore ouvert, on le ferme
                if openai_ws.open:
                    await openai_ws.close()

        async def send_to_twilio():
            """
            Reçoit les événements depuis OpenAI Realtime,
            => s'il y a de l'audio TTS, on le renvoie à Twilio en base64 G711.
            """
            nonlocal stream_sid, last_assistant_item, response_start_timestamp_twilio
            try:
                async for openai_message in openai_ws:
                    response = json.loads(openai_message)

                    # Log certains events 
                    if response['type'] in LOG_EVENT_TYPES:
                        print(f"Received event: {response['type']}", response)

                    # Cas: audio delta
                    if response.get('type') == 'response.audio.delta' and 'delta' in response:
                        # On reconvertit en base64 G711
                        audio_payload = base64.b64encode(base64.b64decode(response['delta'])).decode('utf-8')
                        audio_delta = {
                            "event": "media",
                            "streamSid": stream_sid,
                            "media": {
                                "payload": audio_payload
                            }
                        }
                        await websocket.send_json(audio_delta)

                        # On calcule potentiellement le start time
                        if response_start_timestamp_twilio is None:
                            response_start_timestamp_twilio = latest_media_timestamp
                            if SHOW_TIMING_MATH:
                                print(f"Setting start timestamp for new response: {response_start_timestamp_twilio}ms")

                        # On met à jour last_assistant_item
                        if response.get('item_id'):
                            last_assistant_item = response['item_id']

                        # On envoie un mark 
                        await send_mark(websocket, stream_sid)

                    # Interruption si user parle
                    if response.get('type') == 'input_audio_buffer.speech_started':
                        print("Speech started detected from user => interrupting IA.")
                        if last_assistant_item:
                            await handle_speech_started_event()

            except Exception as e:
                print(f"Error in send_to_twilio: {e}")

        async def handle_speech_started_event():
            """
            Gère la détection de speech_started => interruption du TTS
            en envoyant un event conversation.item.truncate
            """
            nonlocal response_start_timestamp_twilio, last_assistant_item
            print("Handling speech started event.")
            if mark_queue and response_start_timestamp_twilio is not None:
                # On calcule la position d'interruption
                elapsed_time = latest_media_timestamp - response_start_timestamp_twilio
                if SHOW_TIMING_MATH:
                    print(f"Truncation at: {elapsed_time}ms")

                if last_assistant_item:
                    # On envoie un event conversation.item.truncate
                    truncate_event = {
                        "type": "conversation.item.truncate",
                        "item_id": last_assistant_item,
                        "content_index": 0,
                        "audio_end_ms": elapsed_time
                    }
                    await openai_ws.send(json.dumps(truncate_event))

                # On envoie un clear à Twilio
                await websocket.send_json({
                    "event": "clear",
                    "streamSid": stream_sid
                })

                mark_queue.clear()
                last_assistant_item = None
                response_start_timestamp_twilio = None

        async def send_mark(connection, stream_sid):
            """
            Envoie un événement 'mark' à Twilio pour séparer 
            les blocs TTS => usage interne Twilio.
            """
            if stream_sid:
                mark_event = {
                    "event": "mark",
                    "streamSid": stream_sid,
                    "mark": {"name": "responsePart"}
                }
                await connection.send_json(mark_event)
                mark_queue.append('responsePart')

        # On exécute ces 2 coroutines en parallèle
        await asyncio.gather(receive_from_twilio(), send_to_twilio())

async def send_initial_conversation_item(openai_ws):
    """
    Si vous voulez que l'IA parle en premier, 
    décommentez l'appel dans initialize_session().
    """
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
    # On envoie un "conversation.item.create"
    await openai_ws.send(json.dumps(initial_conversation_item))
    # On demande une response.create pour lancer l'audio 
    await openai_ws.send(json.dumps({"type": "response.create"}))

async def initialize_session(openai_ws):
    """
    Initialise la session Realtime openai_ws (voix, instructions, temperature, etc.).
    """
    session_update = {
        "type": "session.update",
        "session": {
            "turn_detection": {"type": "server_vad"},
            "input_audio_format": "g711_ulaw",
            "output_audio_format": "g711_ulaw",
            "voice": VOICE,
            "instructions": SYSTEM_MESSAGE,
            "modalities": ["text", "audio"],
            "temperature": 0.8,
        }
    }
    print('Sending session update:', json.dumps(session_update))
    await openai_ws.send(json.dumps(session_update))

    # Décommentez pour IA qui parle en premier
    # await send_initial_conversation_item(openai_ws)

if __name__ == "__main__":
    import uvicorn
    # On lance uvicorn sur 0.0.0.0:PORT
    uvicorn.run(app, host="0.0.0.0", port=PORT)
