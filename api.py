import logging
import os
from fastapi import FastAPI, HTTPException
from fastapi.responses import StreamingResponse

# Imports locaux
import config
from core.chatbot import Chatbot
from core.models import ChatRequest, ChatResponse

os.environ["KMP_DUPLICATE_LIB_OK"]="TRUE"


# --- Configuration du Logging ---
logging.basicConfig(level=logging.INFO, format='%(asctime)s - %(levelname)s - %(message)s')


# --- Initialisation de l'Application FastAPI ---
app = FastAPI(
    title=config.APP_TITLE,
    description="API pour le chatbot RAG de l'Université de Lomé - Miabé IA",
    version="1.0.0"
)

try:
    chatbot = Chatbot()
    logging.info("Instance du Chatbot Miabé IA prête.")
except Exception as e:
    logging.error(f"ERREUR CRITIQUE AU DÉMARRAGE: Impossible d'initialiser le Chatbot. {e}", exc_info=True)
    # Arrêter l'application si le chatbot ne peut pas démarrer
    raise RuntimeError("Le chatbot n'a pas pu être initialisé.") from e

# --- Endpoints de l'API ---

@app.get("/", summary="Endpoint de statut")
def read_root():
    """Vérifie que l'API est en ligne."""
    return {"status": "ok", "message": f"Bienvenue sur l'API de {config.APP_TITLE}"}

@app.post("/chat", summary="Point d'entrée principal du chatbot en streaming")
def handle_chat(request: ChatRequest):
    """Traite une question et streame la réponse du chatbot morceau par morceau."""
    try:
        response_generator = chatbot.handle_rag_request(request.question, request.history)
        return StreamingResponse(response_generator, media_type="text/plain")
    except Exception as e:
        logging.error(f"Erreur lors du traitement de la requête /chat: {e}", exc_info=True)
        raise HTTPException(status_code=500, detail="Une erreur interne est survenue.")
