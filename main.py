"""
main.py - pam_markII

Ce script Python gère un assistant vocal en temps réel avec Twilio (Media Streams)
et l'API Realtime d'OpenAI pour la synthèse vocale via streaming realtime TTS HD.
Il gère à la fois les appels entrants et sortants.

Usage :
  1) Configurez vos variables d'environnement (OPENAI_API_KEY, TWILIO_ACCOUNT_SID, etc.).
  2) Lancez : python main.py
  3) Pour un appel entrant, configurez Twilio pour pointer vers https://<votre_domaine>/incoming-call
  4) Pour un appel sortant, effectuez un POST sur /make-outbound-call en fournissant le numéro "to"
"""

import os
import json
import base64
import asyncio
import websockets
from fastapi import FastAPI, WebSocket, Request
from fastapi.responses import HTMLResponse, JSONResponse
from twilio.twiml.voice_response import VoiceResponse, Connect, Say, Stream
from dotenv import load_dotenv

# Charger les variables d'environnement
load_dotenv()

# Récupérer les clés API et autres variables
OPENAI_API_KEY = os.getenv('OPENAI_API_KEY')
TWILIO_ACCOUNT_SID = os.getenv('TWILIO_ACCOUNT_SID')
TWILIO_AUTH_TOKEN = os.getenv('TWILIO_AUTH_TOKEN')
TWILIO_PHONE_NUMBER = os.getenv('TWILIO_PHONE_NUMBER')
SERVER = os.getenv('SERVER')  # ex: yourdomain.ngrok.io (sans protocole)

# Port par défaut
PORT = int(os.getenv('PORT', 5050))

# Configuration TTS via OpenAI Audio API (streaming realtime TTS HD)
TTS_MODEL = "tts-1-hd"
TTS_VOICE = "alloy"

# Prompt système complet
SYSTEM_MESSAGE = """Tu es Pam, une agente téléphonique IA conçue pour présenter une démo aux utilisateurs ayant rempli un formulaire sur notre site web. Tu es capable de traiter des demandes de secrétariat, du support client, des ventes et de l'assistance technique. Tu peux utiliser divers outils pour personnaliser tes réponses et t'intégrer dans des contextes professionnels.

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

# Message initial de l'assistante
INITIAL_ASSISTANT_MESSAGE = "Bonjour, ici Pam. Merci d’avoir pris contact. Comment puis-je vous aider aujourd’hui ?"

# Contexte de conversation initiale
BASE_CONVERSATION = [
    { "role": "system", "content": SYSTEM_MESSAGE },
    { "role": "assistant", "content": INITIAL_ASSISTANT_MESSAGE }
]

# Limite d'historique de conversation (pour transmettre un contexte pertinent)
CONVERSATION_HISTORY_LIMIT = 4

# Création de l'application FastAPI
app = FastAPI()

if not OPENAI_API_KEY:
    raise ValueError("Missing the OpenAI API key. Please set it in the .env file for pam_markII.")

# ---------------------------
# Endpoint pour les appels entrants
# ---------------------------
@app.api_route("/incoming-call", methods=["GET", "POST"])
async def handle_incoming_call(request: Request):
    """
    Renvoie un TwiML pour un appel entrant.
    Le TwiML contient un <Connect><Stream> qui redirige l'appel vers le WS /media-stream.
    """
    response = VoiceResponse()
    response.say("Please wait while we connect your call to our AI voice assistant.")
    response.pause(length=1)
    response.say("You may start talking now.")
    host = request.url.hostname
    domain = SERVER if SERVER else host
    print(f"[{str(os.getpid())}] Génération du TwiML pour le domaine : {domain}")
    connect = Connect()
    connect.stream(url=f"wss://{domain}/media-stream")
    response.append(connect)
    print("TwiML généré :", str(response))
    return HTMLResponse(content=str(response), media_type="application/xml")

# ---------------------------
# Endpoint pour les appels sortants
# ---------------------------
@app.post("/make-outbound-call")
async def make_outbound_call(request: Request):
    """
    Déclenche un appel sortant via Twilio en utilisant le même TwiML que pour les appels entrants.
    """
    from twilio.rest import Client as TwilioRestClient
    data = await request.json()
    to_number = data.get("to")
    if not to_number:
        return JSONResponse({"error": "'to' number is required"}, status_code=400)
    if not TWILIO_ACCOUNT_SID or not TWILIO_AUTH_TOKEN:
        return JSONResponse({"error": "Twilio credentials are missing"}, status_code=500)
    client = TwilioRestClient(TWILIO_ACCOUNT_SID, TWILIO_AUTH_TOKEN)
    domain = SERVER if SERVER else request.url.hostname
    if not domain.startswith("http"):
        domain = "https://" + domain
    twiml_url = f"{domain}/incoming-call"
    print(f"Déclenchement d'un appel sortant vers {to_number} avec TwiML URL : {twiml_url}")
    try:
        call = client.calls.create(
            to=to_number,
            from_=TWILIO_PHONE_NUMBER,
            url=twiml_url
        )
        print("Appel sortant déclenché, Call SID :", call.sid)
        return JSONResponse({"success": True, "callSid": call.sid})
    except Exception as e:
        print("Erreur lors de l'appel sortant :", str(e))
        return JSONResponse({"error": str(e)}, status_code=500)

# ---------------------------
# WebSocket /media-stream
# ---------------------------
@app.websocket("/media-stream")
async def handle_media_stream(websocket: WebSocket):
    """
    WebSocket pour gérer le flux audio entre Twilio et l'API Realtime d'OpenAI.
    Pipeline : Twilio -> pam_markII -> OpenAI Realtime -> pam_markII -> Twilio
    """
    print("Client connecté (Twilio) - pam_markII media-stream")
    await websocket.accept()
    async with websockets.connect(
        'wss://api.openai.com/v1/realtime?model=gpt-4o-realtime-preview-2024-10-01',
        extra_headers={
            "Authorization": f"Bearer {OPENAI_API_KEY}",
            "OpenAI-Beta": "realtime=v1"
        }
    ) as openai_ws:
        print("Session OpenAI établie.")
        await initialize_session(openai_ws)
        await asyncio.gather(
            receive_from_twilio(websocket, openai_ws),
            send_to_twilio(websocket, openai_ws)
        )
    print("Session OpenAI fermée.")

async def receive_from_twilio(ws_twilio, openai_ws):
    """
    Reçoit l'audio et les événements de Twilio et les transmet à OpenAI.
    """
    try:
        async for message in ws_twilio.iter_text():
            print(f"Message reçu de Twilio : {message}")
            data = json.loads(message)
            if data.get("event") == "media" and openai_ws.open:
                audio_payload = data["media"]["payload"]
                print(f"Audio reçu, longueur : {len(audio_payload)} caractères")
                audio_append = {
                    "type": "input_audio_buffer.append",
                    "audio": audio_payload
                }
                await openai_ws.send(json.dumps(audio_append))
                print("Audio envoyé à OpenAI.")
            elif data.get("event") == "start":
                print("Flux entrant démarré depuis Twilio.")
    except Exception as e:
        print("Erreur dans receive_from_twilio :", e)

async def send_to_twilio(ws_twilio, openai_ws):
    """
    Reçoit les événements et l'audio TTS depuis OpenAI et les transmet à Twilio.
    """
    try:
        async for openai_message in openai_ws:
            print(f"Message reçu d'OpenAI : {openai_message}")
            response = json.loads(openai_message)
            if response.get("type") == "response.audio.delta" and response.get("delta"):
                print("Delta audio reçu de OpenAI.")
                await ws_twilio.send_json({
                    "event": "media",
                    "media": { "payload": response["delta"] }
                })
                print("Delta audio envoyé à Twilio.")
    except Exception as e:
        print("Erreur dans send_to_twilio :", e)

async def initialize_session(openai_ws):
    """
    Initialise la session Realtime d'OpenAI (voix, instructions, etc.).
    """
    session_update = {
        "type": "session.update",
        "session": {
            "turn_detection": {"type": "server_vad"},
            "input_audio_format": "g711_ulaw",
            "output_audio_format": "g711_ulaw",
            "voice": TTS_VOICE,
            "instructions": SYSTEM_MESSAGE,
            "modalities": ["text", "audio"],
            "temperature": 0.8,
        }
    }
    print("Envoi de la mise à jour de session à OpenAI :", json.dumps(session_update))
    await openai_ws.send(json.dumps(session_update))
    print("Session mise à jour envoyée.")

#---------------------------
# Démarrage du serveur
#---------------------------
if __name__ == "__main__":
    import uvicorn
    print("Démarrage du serveur sur le port", PORT)
    uvicorn.run(app, host="0.0.0.0", port=PORT)
