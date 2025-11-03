'''
Script générique pour le chunking et l'embedding de documents Markdown.
'''

import os
import logging
import pickle
import re
import string
import unicodedata
import numpy as np
import faiss
from langchain_mistralai import MistralAIEmbeddings
from langchain_openai import OpenAIEmbeddings
from langchain.text_splitter import RecursiveCharacterTextSplitter, MarkdownHeaderTextSplitter
import glob
import tiktoken
from time import sleep
# --- Configuration Générale ---
logging.basicConfig(level=logging.INFO, format='%(asctime)s - %(levelname)s - %(message)s')

# --- Chemins ---
SOURCE_DATA_PATH = r"C:\Users\DELL\Desktop\CampusGPT\scraped_documents_md"
VECTOR_STORE_FOLDER_PATH = r"C:\Users\DELL\Desktop\CampusGPT\vector_store"

# --- Clé API ---
MISTRAL_API_KEY = "lmEQ4ZHwYIZEjutVA9GZbLDJS0sJo6Vm"

# --- Paramètres de Traitement ---
MIN_CHAR_COUNT = 20
BATCH_SIZE = 150
TOKEN_THRESHOLD_FOR_HYBRID_CHUNKING = 8000

# --- Paramètres de Chunking (pour les fichiers longs) ---
CHUNK_SIZE = 1500
CHUNK_OVERLAP = 150

# --- Fonctions de Nettoyage et de Transformation ---

def normalize_text(text: str) -> str:
    """Normalise le texte : minuscules, sans accents, en préservant les sauts de ligne."""
    text = text.lower()
    text = unicodedata.normalize('NFKD', text).encode('ascii', 'ignore').decode('utf-8')
    text = text.translate(str.maketrans('', '', string.punctuation))
    text = re.sub(r'[ \t]+', ' ', text)
    text = "\n".join([line.strip() for line in text.split('\n')])
    return text.strip()

def transform_source_path(filename: str) -> str:
    """Transforme le nom du fichier source pour correspondre à une URL potentielle."""
    return filename.replace(".md", "").replace("_", "/")

# --- Fonctions de Chunking ---

def get_hybrid_chunks(content: str, filename: str):
    """Découpe un long document en se basant sur les titres Markdown."""
    headers_to_split_on = [("#", "H1"), ("##", "H2"), ("###", "H3"), ("####", "H4"), ("#####", "H5")]
    markdown_splitter = MarkdownHeaderTextSplitter(headers_to_split_on=headers_to_split_on)
    initial_chunks = markdown_splitter.split_text(content)

    recursive_splitter = RecursiveCharacterTextSplitter(chunk_size=CHUNK_SIZE, chunk_overlap=CHUNK_OVERLAP)
    final_chunks = []
    for chunk in initial_chunks:
        if len(chunk.page_content) > CHUNK_SIZE:
            sub_chunks = recursive_splitter.create_documents([chunk.page_content])
            for sub_chunk in sub_chunks:
                sub_chunk.metadata = chunk.metadata.copy()
                final_chunks.append(sub_chunk)
        else:
            final_chunks.append(chunk)
    
    texts_for_embedding = []
    texts_for_storage = []
    final_metadata_list = []

    source_url = transform_source_path(filename)

    for chunk in final_chunks:
        if len(chunk.page_content.strip()) < MIN_CHAR_COUNT:
            continue

        header_values = [v for k, v in sorted(chunk.metadata.items()) if k.startswith('H')]
        concatenated_title = " - ".join(header_values)

        # Contenu enrichi pour l'embedding (normalisé)
        enriched_content_for_embedding = f"{concatenated_title}\n{chunk.page_content}"
        normalized_for_embedding = normalize_text(enriched_content_for_embedding)
        texts_for_embedding.append(normalized_for_embedding)

        # Le contenu enrichi (titre + contenu brut) est conservé pour le stockage
        enriched_content_for_storage = f"{concatenated_title}\n{chunk.page_content}"
        texts_for_storage.append(enriched_content_for_storage)

        metadata = {
            'source': source_url,
            'titre_concatene': concatenated_title
        }
        final_metadata_list.append(metadata)

    return texts_for_embedding, texts_for_storage, final_metadata_list

# --- Fonction Principale ---

def create_vector_store():
    """Fonction principale qui orchestre le processus de chunking et d'embedding."""
    try:
        embeddings_model = MistralAIEmbeddings(model="mistral-embed", api_key=MISTRAL_API_KEY)
    except Exception as e:
        logging.error(f"Erreur d'initialisation du modèle d'embedding : {e}")
        return

    all_texts_for_embedding = []
    all_texts_for_storage = []
    all_chunk_metadata = []
    
    logging.info(f"Début du scan des fichiers .md dans : {SOURCE_DATA_PATH}")
    markdown_files = glob.glob(os.path.join(SOURCE_DATA_PATH, '**', '*.md'), recursive=True)

    if not markdown_files:
        logging.warning("Aucun fichier .md trouvé.")
        return

    logging.info(f"{len(markdown_files)} fichiers .md trouvés. Début du traitement.")

    for file_path in markdown_files:
        filename = os.path.basename(file_path)
        try:
            with open(file_path, 'r', encoding='utf-8') as f:
                content = f.read()
            
            if len(content) < MIN_CHAR_COUNT:
                logging.warning(f"  Fichier ignoré (trop court) : {filename}")
                continue

            try:
                encoding = tiktoken.get_encoding("cl100k_base")
                num_tokens = len(encoding.encode(content))
            except Exception as e:
                logging.warning(f"  Impossible de compter les tokens pour {filename} avec tiktoken, utilisation de la longueur du texte. Erreur: {e}")
                # Fallback to character count if tiktoken fails
                num_tokens = len(content)

            if num_tokens > TOKEN_THRESHOLD_FOR_HYBRID_CHUNKING:
                logging.info(f"  -> Fichier long détecté ({num_tokens} tokens). Application du chunking hybride pour : {filename}")
                embed_texts, store_texts, metadata_list = get_hybrid_chunks(content, filename)
                all_texts_for_embedding.extend(embed_texts)
                all_texts_for_storage.extend(store_texts)
                all_chunk_metadata.extend(metadata_list)
                logging.info(f"    - Fichier découpé en {len(embed_texts)} chunks.")
            else:
                logging.info(f"  -> Fichier court détecté ({num_tokens} tokens). Traitement en un seul chunk : {filename}")
                normalized_for_embedding = normalize_text(content)
                all_texts_for_embedding.append(normalized_for_embedding)
                all_texts_for_storage.append(content)
                source_url = transform_source_path(filename)
                all_chunk_metadata.append({'source': source_url})

        except Exception as e:
            logging.error(f"  Erreur lors du traitement du fichier {filename} : {e}")

    if not all_texts_for_embedding:
        logging.error("Aucun chunk n'a pu être créé. Arrêt du processus.")
        return

    logging.info(f"Total de chunks créés : {len(all_texts_for_embedding)}. Lancement de l'embedding...")

    embeddings = []
    for i in range(0, len(all_texts_for_embedding), BATCH_SIZE):
        batch_texts = all_texts_for_embedding[i:i + BATCH_SIZE]
        try:
            batch_embeddings = embeddings_model.embed_documents(batch_texts)
            embeddings.extend(batch_embeddings)
            logging.info(f"    Lot {i//BATCH_SIZE + 1}/{(len(all_texts_for_embedding) - 1)//BATCH_SIZE + 1} traité.")
        except Exception as e:
            logging.error(f"Erreur d'embedding pour le lot index {i} : {e}")
            return

    embeddings_np = np.array(embeddings, dtype='float32')

    try:
        dimension = embeddings_np.shape[1]
        index = faiss.IndexFlatL2(dimension)
        index = faiss.IndexIDMap(index)
        ids = np.arange(len(all_texts_for_embedding))
        index.add_with_ids(embeddings_np, ids)

        os.makedirs(VECTOR_STORE_FOLDER_PATH, exist_ok=True)
        faiss.write_index(index, os.path.join(VECTOR_STORE_FOLDER_PATH, "faiss_index.idx"))
        
        with open(os.path.join(VECTOR_STORE_FOLDER_PATH, "index_mapping.pkl"), 'wb') as f:
            pickle.dump({"texts": all_texts_for_storage, "metadata": all_chunk_metadata}, f)
        
        logging.info(f"Index et mapping sauvegardés dans : {VECTOR_STORE_FOLDER_PATH}")
        logging.info("Processus terminé avec succès !")

    except Exception as e:
        logging.error(f"Une erreur est survenue lors de la sauvegarde de l'index Faiss ou du mapping : {e}")

if __name__ == "__main__":
    create_vector_store()