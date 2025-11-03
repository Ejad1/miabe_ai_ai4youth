import os
import glob
import logging
import base64
from io import BytesIO
from typing import List, Optional

# LangChain & OpenAI
from langchain_openai import ChatOpenAI
from langchain_core.messages import HumanMessage
# Correction de l'importation Pydantic pour la compatibilité v2
from pydantic import BaseModel, Field

# Outils de traitement de fichiers
from pdf2image import convert_from_path
from PIL import Image

# --- Configuration ---
logging.basicConfig(level=logging.INFO, format='%(asctime)s - %(levelname)s - %(message)s')

# ATTENTION: Pour des raisons de sécurité, il est fortement recommandé d'utiliser des variables d'environnement.
# Si vous choisissez de mettre la clé ici, assurez-vous de ne pas commettre ce fichier dans un dépôt public.
OPENAI_API_KEY = "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"

# Vérification que la clé a été remplacée
if OPENAI_API_KEY == "VOTRE_CLE_API_OPENAI_ICI":
    raise ValueError("Veuillez remplacer 'VOTRE_CLE_API_OPENAI_ICI' par votre clé API OpenAI réelle dans le script.")

BASE_INPUT_DIR = r"C:\Users\DELL\Desktop\CampusGPT\doc"
BASE_OUTPUT_DIR = r"C:\Users\DELL\Desktop\CampusGPT\doc_md_gpt_vision"
SUPPORTED_EXTENSIONS = ['.pdf', '.png', '.jpg', '.jpeg']

# --- 1. Définition de la Structure de Sortie avec Pydantic ---

class Section(BaseModel):
    """Représente une section unique du document."""
    heading: Optional[str] = Field(description="Le titre de cette section. Peut être nul s'il n'y en a pas.")
    content: str = Field(description="Le contenu textuel de la section.")

class Table(BaseModel):
    """Représente un tableau extrait du document."""
    caption: Optional[str] = Field(description="Titre ou description du tableau.")
    headers: List[str] = Field(description="Liste des en-têtes de colonnes du tableau.")
    rows: List[List[str]] = Field(description="Liste des lignes du tableau, chaque ligne étant une liste de cellules.")

class StructuredContent(BaseModel):
    """Schéma pour le contenu structuré extrait d'un document visuel."""
    title: str = Field(description="Le titre principal et le plus important du document.")
    sections: List[Section] = Field(description="La liste des sections qui composent le corps principal du document.")
    tables: List[Table] = Field(description="La liste des tableaux extraits du document, y compris ceux qui s'étendent sur plusieurs pages.")

# --- 2. Fonctions de Traitement d'Images et de PDF ---

def image_to_base64_uri(image: Image.Image, format="PNG") -> str:
    """Convertit un objet PIL Image en une chaîne data URI Base64."""
    buffered = BytesIO()
    image.save(buffered, format=format)
    img_str = base64.b64encode(buffered.getvalue()).decode('utf-8')
    return f"data:image/{format.lower()};base64,{img_str}"

def get_images_from_file(filepath: str) -> List[str]:
    """Crée une liste d'images encodées en Base64 à partir d'un fichier (PDF ou image)."""
    base64_images = []
    try:
        if filepath.lower().endswith('.pdf'):
            # Convertit chaque page du PDF en image
            pages = convert_from_path(filepath)
            for page in pages:
                base64_images.append(image_to_base64_uri(page))
            logging.info(f"{len(pages)} pages converties en images pour {os.path.basename(filepath)}.")
        elif filepath.lower().endswith(('.png', '.jpg', '.jpeg')):
            # Charge l'image directement
            with Image.open(filepath) as img:
                base64_images.append(image_to_base64_uri(img))
            logging.info(f"Image chargée : {os.path.basename(filepath)}.")
        return base64_images
    except Exception as e:
        logging.error(f"Erreur lors de la conversion du fichier {filepath} en image(s): {e}")
        logging.error("Assurez-vous que Poppler est installé et dans le PATH de votre système.")
        return []

# --- 3. Chaîne d'Extraction Structurée Multimodale ---

def get_structured_extraction_chain():
    """Configure et retourne la chaîne LangChain pour l'extraction multimodale."""
    # La clé API est maintenant passée directement
    llm = ChatOpenAI(model="gpt-4o", temperature=0.0, max_tokens=4000, openai_api_key=OPENAI_API_KEY)
    structured_llm = llm.with_structured_output(StructuredContent)
    return structured_llm

def build_multimodal_prompt(base64_images: List[str], source_path: str) -> HumanMessage:
    """Construit le message multimodal pour l'API OpenAI."""
    prompt_text = """Vous êtes un expert en analyse de documents.
    Analysez la ou les images suivantes, qui représentent les pages d'un document unique.
    Extrayez les informations clés et structurez-les selon le schéma JSON demandé.
    Identifiez le titre, rédigez un résumé détaillé et décomposez le contenu en sections logiques.
    Pour les tableaux, utilisez la structure 'Table' fournie. Si un tableau s'étend sur plusieurs pages, fusionnez ses parties pour former un tableau complet et cohérent.
    Ignorez les éléments non pertinents comme les publicités, les menus de navigation ou les pieds de page.
    Le chemin du fichier source est : {source_path}"""
    
    content = [{"type": "text", "text": prompt_text.format(source_path=source_path)}]
    for img_uri in base64_images:
        content.append({
            "type": "image_url",
            "image_url": {"url": img_uri}
        })
    return HumanMessage(content=content)

# --- 4. Formatage de la Sortie en Markdown ---

def to_markdown(data: StructuredContent) -> str:
    """Convertit l'objet Pydantic StructuredContent en une chaîne Markdown."""
    md = f"# {data.title}\n\n"
    
    for section in data.sections:
        if section.heading:
            md += f"## {section.heading}\n"
        md += f"{section.content}\n\n"

    for table in data.tables:
        md += f"\n### Tableau: {table.caption or 'Sans titre'}\n\n"
        if table.headers:
            md += "|" + "|".join(table.headers) + "|\n"
            md += "|" + "--|" * len(table.headers) + "\n"
        for row in table.rows:
            md += "|" + "|".join(row) + "|\n"
        md += "\n"
        
    return md

# --- 5. Orchestrateur Principal ---

def main():
    logging.info("===== DÉBUT DU SCRIPT D'EXTRACTION VISION AVEC GPT-4o =====")
    os.makedirs(BASE_OUTPUT_DIR, exist_ok=True)

    extraction_chain = get_structured_extraction_chain()

    files_to_process = []
    for ext in SUPPORTED_EXTENSIONS:
        files_to_process.extend(glob.glob(os.path.join(BASE_INPUT_DIR, '**', f"*{ext}"), recursive=True))

    logging.info(f"{len(files_to_process)} fichier(s) à traiter.")

    for filepath in files_to_process:
        logging.info(f"--- Traitement de : {os.path.basename(filepath)} ---")
        
        base64_images = get_images_from_file(filepath)

        if not base64_images:
            logging.warning(f"Aucune image n'a pu être extraite de {filepath}, fichier ignoré.")
            continue

        try:
            prompt = build_multimodal_prompt(base64_images, filepath)
            # Correction ici : l'appel à invoke doit prendre une liste de messages
            structured_data = extraction_chain.invoke([prompt])
            markdown_content = to_markdown(structured_data)

            # Sauvegarde
            relative_path = os.path.relpath(os.path.dirname(filepath), BASE_INPUT_DIR)
            output_dir = os.path.join(BASE_OUTPUT_DIR, relative_path)
            os.makedirs(output_dir, exist_ok=True)
            base_name = os.path.splitext(os.path.basename(filepath))[0]
            md_filename = f"{base_name}.md"
            md_filepath = os.path.join(output_dir, md_filename)

            with open(md_filepath, 'w', encoding='utf-8') as f:
                f.write(markdown_content)
            logging.info(f"Succès GPT-4o Vision -> MD : {md_filepath}")

        except Exception as e:
            logging.error(f"ERREUR lors du traitement GPT-4o Vision pour {filepath}. Raison : {e}", exc_info=True)

    logging.info("\n===== SCRIPT TERMINÉ =====")

if __name__ == "__main__":
    main()