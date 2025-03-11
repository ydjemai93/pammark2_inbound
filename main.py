import os
import json
import base64
import asyncio
import websockets
from fastapi import FastAPI, WebSocket, Request
from fastapi.responses import HTMLResponse, JSONResponse
from fastapi.websockets import WebSocketDisconnect
from twilio.twiml.voice_response import VoiceResponse, Connect, Say
from twilio.rest import Client
from dotenv import load_dotenv

# Charger les variables d'environnement
load_dotenv()

OPENAI_API_KEY = os.getenv('OPENAI_API_KEY')
TWILIO_ACCOUNT_SID = os.getenv('TWILIO_ACCOUNT_SID')
TWILIO_AUTH_TOKEN = os.getenv('TWILIO_AUTH_TOKEN')
TWILIO_FROM_NUMBER = os.getenv('TWILIO_FROM_NUMBER')
PORT = int(os.getenv('PORT', 5050))

if not all([OPENAI_API_KEY, TWILIO_ACCOUNT_SID, TWILIO_AUTH_TOKEN, TWILIO_FROM_NUMBER]):
    raise ValueError("Variables d'environnement manquantes")

VOICE = 'alloy'
OPENAI_MODEL = 'gpt-4o-realtime-preview-2024-10-01'
twilio_client = Client(TWILIO_ACCOUNT_SID, TWILIO_AUTH_TOKEN)

# ✅ **Nouveau prompt naturel et conversationnel**
SYSTEM_MESSAGE = """Tu es Pam, une agente téléphonique IA conçue pour présenter une démo aux utilisateurs ayant rempli un formulaire sur notre site web. Ton rôle est de donner un aperçu efficace et engageant de tes capacités en quelques phrases, sans réciter mécaniquement une liste de fonctionnalités. Ton objectif est de capter l’attention et de donner envie à ton interlocuteur d’en savoir plus.

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


app = FastAPI()

@app.get("/", response_class=JSONResponse)
async def index_page():
    return {"message": "Twilio Media Stream Server is running!"}

# ---------------------------
# ✅ **Gestion des appels sortants**
# ---------------------------
@app.post("/outbound-call")
async def initiate_outbound_call(request: Request):
    try:
        data = await request.json()
        to_number = data.get('to')
        if not to_number or not to_number.startswith('+'):
            return JSONResponse({"error": "Numéro invalide, utilisez E.164 (ex: +33123456789)"}, status_code=400)
        call = twilio_client.calls.create(
            to=to_number,
            from_=TWILIO_FROM_NUMBER,
            url=f"https://{request.url.hostname}/incoming-call"
        )
        return JSONResponse({"status": "call_initiated", "call_sid": call.sid})
    except Exception as e:
        return JSONResponse({"error": f"Erreur Twilio: {str(e)}"}, status_code=500)

# ---------------------------
# ✅ **Gestion des appels entrants**
# ---------------------------
@app.api_route("/incoming-call", methods=["GET", "POST"])
async def handle_incoming_call(request: Request):
    response = VoiceResponse()
    response.say("Bienvenue, votre appel est en cours de connexion.")
    response.pause(length=1)
    connect = Connect()
    connect.stream(url=f"wss://{request.url.hostname}/media-stream")
    response.append(connect)
    return HTMLResponse(content=str(response), media_type="application/xml")

# ---------------------------
# ✅ **WebSocket pour la gestion du flux média**
# ---------------------------
@app.websocket("/media-stream")
async def handle_media_stream(websocket: WebSocket):
    await websocket.accept()
    print("Client connecté (Twilio)")

    try:
        async with websockets.connect(
            f'wss://api.openai.com/v1/realtime?model={OPENAI_MODEL}',
            extra_headers={"Authorization": f"Bearer {OPENAI_API_KEY}", "OpenAI-Beta": "realtime=v1"}
        ) as openai_ws:
            await initialize_session(openai_ws)
            stream_sid = None

            async def receive_from_twilio():
                nonlocal stream_sid
                async for message in websocket.iter_text():
                    data = json.loads(message)
                    if data.get('event') == 'media' and openai_ws.open:
                        await openai_ws.send(json.dumps({"type": "input_audio_buffer.append", "audio": data['media']['payload']}))
                    elif data.get('event') == 'start':
                        stream_sid = data['start']['streamSid']
                        print(f"Stream ID: {stream_sid}")

            async def send_to_twilio():
                async for message in openai_ws:
                    data = json.loads(message)
                    if data.get('type') == 'response.audio.delta' and 'delta' in data:
                        await websocket.send_json({"event": "media", "streamSid": stream_sid, "media": {"payload": data['delta']}})
                        await add_natural_pauses(openai_ws)

            await asyncio.gather(receive_from_twilio(), send_to_twilio())

    except Exception as e:
        print(f"[ERREUR] {str(e)}")
    finally:
        await websocket.close()

# ---------------------------
# ✅ **Améliorations pour une conversation plus fluide**
# ---------------------------

async def add_natural_pauses(openai_ws):
    """Ajoute une pause naturelle avant la réponse pour un rendu plus humain."""
    await openai_ws.send(json.dumps({
        "type": "response.create",
        "response": {
            "modalities": ["audio"],
            "instructions": "Ajoutez une courte pause de 0.5 seconde avant de parler."
        }
    }))

async def initialize_session(openai_ws):
    """Initialise la session OpenAI avec des réglages optimisés."""
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
            "frequency_penalty": 0.2,
            "presence_penalty": 0.4,
            "response_latency_smoothing": "aggressive",
            "max_tokens": 150
        }
    }
    print("[Session] Configuration envoyée à OpenAI")
    await openai_ws.send(json.dumps(session_update))

# ---------------------------
# ✅ **Lancement du serveur**
# ---------------------------
if __name__ == "__main__":
    import uvicorn
    uvicorn.run(app, host="0.0.0.0", port=PORT)
