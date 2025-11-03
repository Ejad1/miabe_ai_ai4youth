import logging
import pickle
import os
import faiss
import numpy as np
from typing import List, Dict, Any, Generator

# Imports LangChain - à adapter selon le fournisseur actif
from langchain_mistralai import ChatMistralAI, MistralAIEmbeddings
from langchain_openai import ChatOpenAI, OpenAIEmbeddings
from langchain_anthropic import ChatAnthropic
# from langchain_google_genai import ChatGoogleGenerativeAI, GoogleGenerativeAIEmbeddings
# from langchain_anthropic import ChatAnthropic
# from langchain_groq import ChatGroq

from langchain_core.prompts import ChatPromptTemplate
from langchain_core.output_parsers import StrOutputParser

# Imports locaux
import config
from utils.text_helpers import normalize_text

class Chatbot:
    """
    Classe principale encapsulant la logique du chatbot RAG.
    """

    def __init__(self):
        logging.info("Initialisation du Chatbot Miabé IA...")
        self._initialize_models()
        self._initialize_chains()
        self._load_vector_store()

    def _initialize_models(self):
        """
        Initialise les modèles de langage et d'embedding.
        """

        seed = 42
        top_p = 1.0

        # self.completion_model = ChatAnthropic(
        #     model=config.ANTHROPIC_COMPLETION_MODEL,
        #     api_key=config.ANTHROPIC_API_KEY,
        #     temperature=0.4,
        #     max_tokens=1000,
        #     top_p=top_p
        # )

        self.completion_model = ChatOpenAI(
            model=config.OPENAI_COMPLETION_MODEL,
            api_key=config.OPENAI_API_KEY,
            temperature=0.4,
            max_tokens=1000,
            top_p=top_p,
            seed=seed
        )

        self.classifier_model = ChatMistralAI(
            model_name=config.MISTRAL_CLASSIFIER_MODEL, 
            api_key=config.MISTRAL_API_KEY,
            temperature=0.0,
            max_tokens=10,
            top_p=top_p
        )

        self.rewriter_model = ChatMistralAI(
            model_name=config.MISTRAL_COMPLETION_MODEL, 
            api_key=config.MISTRAL_API_KEY,
            temperature=0.5,
            max_tokens=100,
            top_p=top_p
        )

        self.embedding_model = MistralAIEmbeddings(
            model=config.MISTRAL_EMBEDDING_MODEL,
            api_key=config.MISTRAL_API_KEY
        )

        logging.info("Modèles OpenAI & Mistral initialisés.")

    def _initialize_chains(self):
        """
        Crée les chaînes de traitement LangChain.
        """

        self.classifier_chain = ChatPromptTemplate.from_template(config.SYSTEM_PROMPT_CLASSIFIER) | self.classifier_model | StrOutputParser()
        self.rewriter_chain = ChatPromptTemplate.from_template(config.SYSTEM_PROMPT_REWRITER) | self.rewriter_model | StrOutputParser()
        self.rag_chain = ChatPromptTemplate.from_template(config.SYSTEM_PROMPT_RAG) | self.completion_model | StrOutputParser()
        self.predefined_chain = ChatPromptTemplate.from_template(config.SYSTEM_PROMPT_PREDEFINED_GENERATOR) | self.completion_model | StrOutputParser()
        
        logging.info("Chaînes LangChain créées.")

    def _load_vector_store(self):
        """
        Charge l'index FAISS et les données associées depuis les fichiers.
        """

        logging.info(f"Chargement du vector store depuis : {config.VECTOR_STORE_FOLDER_PATH}")
        faiss_path = os.path.join(config.VECTOR_STORE_FOLDER_PATH, config.FAISS_INDEX_FILE)
        mapping_path = os.path.join(config.VECTOR_STORE_FOLDER_PATH, config.MAPPING_FILE)
        
        try:
            self.index = faiss.read_index(faiss_path)
            with open(mapping_path, 'rb') as f:
                loaded_data = pickle.load(f)
            self.all_chunk_texts = loaded_data['texts']
            self.all_chunk_metadata = loaded_data['metadata']

            logging.info(f"Vector store chargé avec succès ({self.index.ntotal} vecteurs).")

        except Exception as e:
            logging.error(f"Erreur critique lors du chargement du vector store : {e}")
            raise

    def _classify_intent(self, question: str) -> str:
        """
        Détermine l'intention de la question de l'utilisateur.

        Il prend en entrée la question et retourne une des catégories définies dans config.INTENT_CATEGORIES.
        """

        try:
            intent = self.classifier_chain.invoke({"CONTEXT_NAME": config.CONTEXT_NAME, "question": question})
            return intent.strip() if intent.strip() in config.INTENT_CATEGORIES else "Question_Information_Generale"
        except Exception as e:
            logging.error(f"Erreur de classification: {e}")
            return "Question_Information_Generale"

    def _rewrite_query(self, history: list[dict], question: str) -> str:
        """
        Réécrit la question en utilisant l'historique pour plus de contexte.

        Il prend en entrée l'historique des messages et la question actuelle, et retourne une question reformulée autonome et complète.
        """
        
        history_str = "\n".join([f"{msg['role']}: {msg['content']}" for msg in history])
        try:
            return self.rewriter_chain.invoke({"CONTEXT_NAME": config.CONTEXT_NAME, "history_str": history_str, "question": question})
        except Exception as e:
            logging.error(f"Erreur de réécriture: {e}")
            return question

    def _find_relevant_chunks(self, question: str) -> list:
        """
        Recherche les chunks les plus pertinents dans le vector store.

        Il prend en entrée la question et retourne une liste de chunks pertinents (texte + métadonnées).
        """

        normalized_query = normalize_text(question)
        query_embedding = self.embedding_model.embed_query(normalized_query)
        query_embedding_np = np.array([query_embedding], dtype='float32')
        distances, indices = self.index.search(query_embedding_np, config.SEARCH_K)
        
        search_results = []
        if indices.size > 0:
            for idx in indices[0]:
                if idx != -1:
                    search_results.append({
                        'text': self.all_chunk_texts[idx],
                        'metadata': self.all_chunk_metadata[idx],
                    })
        return search_results

    def handle_rag_request(self, question: str, history: list[dict]) -> Generator[str, None, None]:
        """
        Orchestre le pipeline RAG complet et streame la réponse.

        Il prend en entrée la question et l'historique, et yield la réponse morceau par morceau.
        """

        logging.info(f"Question originale reçue: '{question}'")
        
        rewritten_question = self._rewrite_query(history, question)
        logging.info(f"Question réécrite: '{rewritten_question}'")

        intent = self._classify_intent(rewritten_question)
        logging.info(f"Intention détectée: {intent}")

        if intent in config.PREDEFINED_ANSWERS or intent == "Saluations":
            # Stream la réponse prédéfinie générée par l'IA
            for chunk in self.predefined_chain.stream({
                "CONTEXT_NAME": config.CONTEXT_NAME, 
                "question": question, 
                "intent": intent, 
                "french_answer": config.PREDEFINED_ANSWERS[intent]
            }):
                yield chunk
        
        elif intent == "Question_Information_Generale":
            try:
                relevant_chunks = self._find_relevant_chunks(rewritten_question)
                if not relevant_chunks:
                    yield "Je n'ai pas trouvé d'information pertinente pour répondre à votre question."
                    return

                context_str = "\n\n---\n\n".join([f"Source: {c['metadata'].get('source', 'N/A')}\nContenu: {c['text']}" for c in relevant_chunks])
                history_str = "\n".join([f"{msg['role']}: {msg['content']}" for msg in history])
                
                # Stream la réponse RAG
                for chunk in self.rag_chain.stream({
                    "CONTEXT_NAME": config.CONTEXT_NAME, 
                    "history_str": history_str, 
                    "context_str": context_str, 
                    "question": question
                }):
                    yield chunk
                    
            except Exception as e:
                logging.error(f"Erreur durant le pipeline RAG: {e}")
                yield "Désolé, une erreur technique est survenue."
        else: # Intention inconnue - réponse par défaut
            yield config.PREDEFINED_ANSWERS.get(intent, "Je ne suis pas sûr de comprendre. Pouvez-vous reformuler ?")
