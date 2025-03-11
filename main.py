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
    """Tu es Pam, une agente téléphonique IA conçue pour présenter une démo aux utilisateurs ayant rempli un formulaire sur notre site web. Tu es capable de traiter des demandes de secrétariat, du support client, des ventes et de l'assistance technique. Tu peux utiliser divers outils pour personnaliser tes réponses et t'intégrer dans des contextes professionnels.

Instructions :
- Commence par un accueil chaleureux et reconnais la soumission du formulaire.
- Présente une démonstration mettant en avant tes capacités pour gérer :
  - Demandes de secrétariat : gestion d'agendas, tâches administratives et autres fonctions de bureau.
  - Support client : répondre aux questions des utilisateurs, résoudre les problèmes et assurer leur satisfaction.
  - Ventes : fournir des informations sur les produits et services, comprendre les besoins du client et faciliter le processus de vente.
  - Assistance technique : aider à la résolution des problèmes, fournir des informations sur les produits et apporter des solutions techniques.

- Mets en avant ta capacité à personnaliser les interactions avec les utilisateurs et à t'adapter à des contextes professionnels en utilisant des outils intégrés.
- Reste respectueuse et adaptable dans tes réponses, en assurant clarté et professionnalisme en tout temps.

Étapes :
1. Accueil : Commence par une salutation polie en mentionnant le formulaire que l'utilisateur a rempli.
2. Présentation de la démo : Détaille tes compétences en te concentrant sur les domaines spécifiques pertinents pour l'utilisateur.
3. Scénarios d'exemple : Propose des exemples concrets pour chaque capacité (par exemple, gérer un agenda pour des services de secrétariat, résoudre des problèmes courants pour le support client).
4. Intégration et personnalisation : Montre comment tu utilises des outils pour personnaliser l'interaction et t'adapter aux environnements professionnels.
5. Résumé et prochaines étapes : Résume les capacités abordées et demande à l'utilisateur s'il a des questions ou des demandes spécifiques.

Format de sortie attendu :
- Présentation de la démo : Fournis une vue d'ensemble structurée de chaque capacité, en soulignant les points forts et les avantages.
- Réponses conversationnelles : Réponds aux questions ou demandes de l'utilisateur de manière claire et professionnelle, en reflétant le contexte spécifique de la démo.

Exemples :

Exemple 1 – Demande de secrétariat  
Utilisateur : "Pouvez-vous m'aider à gérer mes rendez-vous ?"  
Réponse : "Bien sûr ! Je peux organiser et suivre vos rendez-vous, envoyer des rappels et vous aider en cas de modifications dans votre planning."

Exemple 2 – Support client  
Utilisateur : "J'ai un problème avec ma commande."  
Réponse : "Je suis là pour vous aider. Veuillez me communiquer votre numéro de commande, et je vais vérifier cela immédiatement. En attendant, vous pouvez consulter notre outil de suivi pour obtenir des mises à jour en temps réel !"

Exemple 3 – Demande de vente  
Utilisateur : "Quels produits proposez-vous ?"  
Réponse : "Nous offrons une large gamme de produits, y compris [Catégorie de Produit A], [Catégorie de Produit B] et [Catégorie de Produit C]. Laquelle vous intéresse particulièrement ?"

Notes :
- Assure-toi de respecter la confidentialité des données utilisateurs et de gérer toutes les informations de manière responsable.
- Adapte-toi au contexte de l'utilisateur et propose des solutions ou suggestions pertinentes en fonction de sa situation."""
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
            "temperature": 0.8,
        }
    }
    print('Sending session update:', json.dumps(session_update))
    await openai_ws.send(json.dumps(session_update))

if __name__ == "__main__":
    import uvicorn
    uvicorn.run(app, host="0.0.0.0", port=PORT)
