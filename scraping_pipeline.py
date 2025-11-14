"""
Pipeline de Scraping Web pour Miabé IA
Collecte et conversion de données depuis les sites universitaires
"""

import os
import re
import json
import hashlib
import time
import warnings
import threading
from pathlib import Path
from datetime import datetime
from typing import Dict, List, Tuple, Optional, Any
from collections import deque
from concurrent.futures import ThreadPoolExecutor, as_completed

import requests
from bs4 import BeautifulSoup
from urllib.parse import urljoin, urlparse
from markdownify import markdownify as html_to_md
from tqdm import tqdm

warnings.filterwarnings("ignore", category=requests.packages.urllib3.exceptions.InsecureRequestWarning)

# ============================================
# CONFIGURATION
# ============================================

UNIV_LOME_URLS = {
    "site_principal": "https://paul.univ-lome.tg/",
    "portail_etudiant": "https://etu.univ-lome.tg/",
    "univ_lome": "https://univ-lome.tg/",
    "parcours": "https://paul.univ-lome.tg/parcours",
}

ACADEMIC_SOURCES = {
    "hal": "https://hal.archives-ouvertes.fr/",
    "research4life": "https://www.research4life.org/",
    "jstor": "https://www.jstor.org/",
    "isidore": "https://isidore.science/",
    "fao_stats": "http://www.fao.org/faostat",
    "ensam_biblio": "https://bibliotheques.ensam.eu/",
    "ensam_theses": "https://bibliotheques.ensam.eu/ressources-documentaires/thesesfr",
}

START_URLS = [
    UNIV_LOME_URLS["site_principal"],
    UNIV_LOME_URLS["portail_etudiant"],
    UNIV_LOME_URLS["univ_lome"],
]

SCRAPING_CONFIG = {
    "max_pages": 2000,
    "timeout": 20,
    "delay": 1,
    "num_workers": 8,
    "verify_ssl": False,
    "user_agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/91.0.4472.124 Safari/537.36",
}

DOCUMENT_EXTENSIONS = (
    '.pdf', '.doc', '.docx',
    '.xls', '.xlsx',
    '.ppt', '.pptx',
    '.odt', '.ods', '.odp',
    '.rtf', '.csv', '.json'
)

OUTPUT_ROOT = Path("scrapped_documents")

OUTPUT_STRUCTURE = {
    "html_brut": OUTPUT_ROOT / "html_brut",
    "html_hashes": OUTPUT_ROOT / "html_hashes",
    "html_texts": OUTPUT_ROOT / "html_texts",
    "converted_html": OUTPUT_ROOT / "converted_html",
    "documents": OUTPUT_ROOT / "documents",
    "document_hashes": OUTPUT_ROOT / "document_hashes",
    "converted_documents": OUTPUT_ROOT / "converted_documents",
    "metadata": OUTPUT_ROOT / "metadata.json"
}

_metadata_lock = threading.Lock()

# ============================================
# INITIALISATION
# ============================================

def create_output_structure():
    """Crée la structure de dossiers pour le stockage des données scrapées."""
    for name, path in OUTPUT_STRUCTURE.items():
        if name != "metadata":
            path.mkdir(parents=True, exist_ok=True)
    
    metadata_path = OUTPUT_STRUCTURE["metadata"]
    if not metadata_path.exists():
        with open(metadata_path, 'w', encoding='utf-8') as f:
            json.dump({}, f, ensure_ascii=False, indent=2)

# ============================================
# FONCTIONS UTILITAIRES
# ============================================

def hash_url(url: str) -> str:
    """Génère un hash SHA-256 d'une URL pour créer un nom de fichier unique."""
    return hashlib.sha256(url.encode('utf-8')).hexdigest()

def hash_content(file_path: Path) -> str:
    """Calcule le hash SHA-256 du contenu d'un fichier pour la déduplication."""
    sha256 = hashlib.sha256()
    with open(file_path, 'rb') as f:
        for chunk in iter(lambda: f.read(8192), b''):
            sha256.update(chunk)
    return sha256.hexdigest()

def normalize_text(text: str) -> str:
    """Normalise le texte en remplaçant les espaces multiples par un seul."""
    return re.sub(r'\s+', ' ', text).strip()

def load_metadata() -> Dict[str, str]:
    """Charge les métadonnées depuis le fichier JSON (thread-safe)."""
    metadata_path = OUTPUT_STRUCTURE["metadata"]
    if metadata_path.exists():
        with _metadata_lock:
            try:
                with open(metadata_path, 'r', encoding='utf-8') as f:
                    return json.load(f)
            except json.JSONDecodeError:
                return {}
    return {}

def save_metadata(metadata: Dict[str, str]):
    """Sauvegarde les métadonnées dans le fichier JSON (thread-safe)."""
    metadata_path = OUTPUT_STRUCTURE["metadata"]
    with _metadata_lock:
        with open(metadata_path, 'w', encoding='utf-8') as f:
            json.dump(metadata, f, ensure_ascii=False, indent=2)

def add_metadata_entry(hash_name: str, original_name: str, url: str = None, content_hash: str = None, duplicate_of: str = None):
    """Ajoute une entrée dans les métadonnées (thread-safe)."""
    with _metadata_lock:
        metadata_path = OUTPUT_STRUCTURE["metadata"]
        if metadata_path.exists():
            try:
                with open(metadata_path, 'r', encoding='utf-8') as f:
                    metadata = json.load(f)
            except json.JSONDecodeError:
                metadata = {}
        else:
            metadata = {}
        
        metadata[hash_name] = {
            "original_name": original_name,
            "url": url,
            "content_hash": content_hash,
            "duplicate_of": duplicate_of,
            "timestamp": datetime.now().astimezone().isoformat()
        }
        
        with open(metadata_path, 'w', encoding='utf-8') as f:
            json.dump(metadata, f, ensure_ascii=False, indent=2)

def repair_metadata_json():
    """Répare un fichier metadata.json corrompu en extrayant les entrées valides."""
    metadata_path = OUTPUT_STRUCTURE["metadata"]
    
    if not metadata_path.exists():
        return
    
    backup_path = metadata_path.with_suffix('.json.backup')
    timestamp = datetime.now().strftime('%Y%m%d_%H%M%S')
    backup_with_time = metadata_path.parent / f"metadata.json.backup_{timestamp}"
    
    try:
        with open(metadata_path, 'r', encoding='utf-8') as f:
            metadata = json.load(f)
        return
    except json.JSONDecodeError:
        with open(metadata_path, 'r', encoding='utf-8') as f:
            corrupt_content = f.read()
        with open(backup_with_time, 'w', encoding='utf-8') as f:
            f.write(corrupt_content)
        
        metadata = {}
        lines = corrupt_content.split('\n')
        current_key = None
        current_entry = {}
        in_entry = False
        
        for line in lines:
            line = line.strip()
            
            if line.startswith('"') and '": {' in line:
                if current_key and current_entry:
                    metadata[current_key] = current_entry
                
                current_key = line.split('"')[1]
                current_entry = {}
                in_entry = True
            
            elif in_entry and '"original_name"' in line:
                value = line.split(':', 1)[1].strip().rstrip(',').strip('"')
                current_entry["original_name"] = value
            
            elif in_entry and '"url"' in line:
                value = line.split(':', 1)[1].strip().rstrip(',').strip('"')
                current_entry["url"] = value if value != "null" else None
            
            elif in_entry and '"content_hash"' in line:
                value = line.split(':', 1)[1].strip().rstrip(',').strip('"')
                current_entry["content_hash"] = value if value != "null" else None
            
            elif in_entry and '"duplicate_of"' in line:
                value = line.split(':', 1)[1].strip().rstrip(',').strip('"')
                current_entry["duplicate_of"] = value if value != "null" else None
            
            elif in_entry and '"timestamp"' in line:
                value = line.split(':', 1)[1].strip().rstrip(',').strip('"')
                current_entry["timestamp"] = value
            
            elif line == '}' or line == '},':
                if current_key and current_entry:
                    metadata[current_key] = current_entry
                    current_key = None
                    current_entry = {}
                    in_entry = False
        
        if current_key and current_entry:
            metadata[current_key] = current_entry
        
        with open(metadata_path, 'w', encoding='utf-8') as f:
            json.dump(metadata, f, ensure_ascii=False, indent=2)
        
        return metadata

def get_existing_files(directory: Path) -> set:
    """Retourne l'ensemble des fichiers existants dans un dossier."""
    if directory.exists():
        return set(f.name for f in directory.iterdir() if f.is_file())
    return set()

def find_duplicate_by_content(content_hash: str) -> Optional[str]:
    """Recherche si un fichier avec le même hash de contenu existe déjà."""
    metadata = load_metadata()
    for hash_name, entry in metadata.items():
        if entry.get("content_hash") == content_hash and entry.get("duplicate_of") is None:
            return hash_name
    return None

def get_all_content_hashes() -> set:
    """Retourne l'ensemble de tous les hash de contenu existants."""
    all_hashes = set()
    
    html_hash_dir = OUTPUT_STRUCTURE["html_hashes"]
    if html_hash_dir.exists():
        for hash_file in html_hash_dir.glob("*.hash"):
            with open(hash_file, 'r', encoding='utf-8') as f:
                all_hashes.add(f.read().strip())
    
    doc_hash_dir = OUTPUT_STRUCTURE["document_hashes"]
    if doc_hash_dir.exists():
        for hash_file in doc_hash_dir.glob("*.hash"):
            with open(hash_file, 'r', encoding='utf-8') as f:
                all_hashes.add(f.read().strip())
    
    return all_hashes

def save_content_hash(hash_name: str, content_hash: str, file_type: str = "html"):
    """Sauvegarde le hash de contenu dans un fichier .hash avec retry pour eviter les conflits."""
    import random
    
    if file_type == "html":
        hash_dir = OUTPUT_STRUCTURE["html_hashes"]
    else:
        hash_dir = OUTPUT_STRUCTURE["document_hashes"]
    
    hash_file = hash_dir / f"{hash_name}.hash"
    
    max_attempts = 5
    for attempt in range(max_attempts):
        try:
            with open(hash_file, 'w', encoding='utf-8') as f:
                f.write(content_hash)
            return
        except (PermissionError, OSError):
            if attempt < max_attempts - 1:
                time.sleep(random.uniform(0.1, 0.5))

def load_content_hash(hash_name: str, file_type: str = "html") -> Optional[str]:
    """Charge le hash de contenu depuis un fichier .hash avec retry pour eviter les conflits."""
    import random
    
    if file_type == "html":
        hash_dir = OUTPUT_STRUCTURE["html_hashes"]
    else:
        hash_dir = OUTPUT_STRUCTURE["document_hashes"]
    
    hash_file = hash_dir / f"{hash_name}.hash"
    if not hash_file.exists():
        return None
    
    max_attempts = 5
    for attempt in range(max_attempts):
        try:
            with open(hash_file, 'r', encoding='utf-8') as f:
                return f.read().strip()
        except (PermissionError, OSError):
            if attempt < max_attempts - 1:
                time.sleep(random.uniform(0.1, 0.5))
    return None

def delete_old_file(hash_name: str, file_type: str = "html"):
    """Supprime un ancien fichier et son hash associé avec retry pour eviter les conflits."""
    import random
    
    max_attempts = 5
    
    if file_type == "html":
        html_file = OUTPUT_STRUCTURE["html_brut"] / f"{hash_name}.html"
        if html_file.exists():
            for attempt in range(max_attempts):
                try:
                    html_file.unlink()
                    break
                except (PermissionError, OSError):
                    if attempt < max_attempts - 1:
                        time.sleep(random.uniform(0.1, 0.5))
        
        hash_file = OUTPUT_STRUCTURE["html_hashes"] / f"{hash_name}.hash"
        if hash_file.exists():
            for attempt in range(max_attempts):
                try:
                    hash_file.unlink()
                    break
                except (PermissionError, OSError):
                    if attempt < max_attempts - 1:
                        time.sleep(random.uniform(0.1, 0.5))
    else:
        doc_dir = OUTPUT_STRUCTURE["documents"]
        for doc_file in doc_dir.glob(f"{hash_name}.*"):
            for attempt in range(max_attempts):
                try:
                    doc_file.unlink()
                    break
                except (PermissionError, OSError):
                    if attempt < max_attempts - 1:
                        time.sleep(random.uniform(0.1, 0.5))
        
        hash_file = OUTPUT_STRUCTURE["document_hashes"] / f"{hash_name}.hash"
        if hash_file.exists():
            for attempt in range(max_attempts):
                try:
                    hash_file.unlink()
                    break
                except (PermissionError, OSError):
                    if attempt < max_attempts - 1:
                        time.sleep(random.uniform(0.1, 0.5))

# ============================================
# SCRAPING
# ============================================

def scrape_page(url: str, base_domain: str) -> List[str]:
    """Scrappe une page web unique avec gestion intelligente de la déduplication."""
    found_links = []
    
    try:
        headers = {"User-Agent": SCRAPING_CONFIG["user_agent"]}
        response = requests.get(
            url,
            headers=headers,
            timeout=SCRAPING_CONFIG["timeout"],
            verify=SCRAPING_CONFIG["verify_ssl"],
            stream=True
        )
        response.raise_for_status()
        
        content_type = response.headers.get('Content-Type', '').lower()
        
        binary_content_types = [
            'application/pdf',
            'application/msword',
            'application/vnd.openxmlformats-officedocument',
            'application/vnd.ms-excel',
            'application/vnd.ms-powerpoint',
            'application/vnd.oasis.opendocument',
            'application/rtf',
            'application/zip',
            'application/octet-stream',
            'text/csv',
            'application/json',
        ]
        
        is_binary = any(binary_type in content_type for binary_type in binary_content_types)
        url_lower = url.lower()
        has_doc_extension = any(url_lower.endswith(ext) or ext in url_lower for ext in DOCUMENT_EXTENSIONS)
        
        if is_binary or has_doc_extension:
            response.close()
            download_document(url)
            return found_links
        
        response.encoding = response.apparent_encoding
        html_content = response.text
        
        content_hash_value = hashlib.sha256(html_content.encode('utf-8')).hexdigest()
        hash_name = hash_url(url)
        
        all_existing_hashes = get_all_content_hashes()
        if content_hash_value in all_existing_hashes:
            return found_links
        
        old_content_hash = load_content_hash(hash_name, file_type="html")
        
        if old_content_hash is not None:
            if old_content_hash == content_hash_value:
                return found_links
            else:
                delete_old_file(hash_name, file_type="html")
        
        html_file = OUTPUT_STRUCTURE["html_brut"] / f"{hash_name}.html"
        with open(html_file, 'w', encoding='utf-8') as f:
            f.write(html_content)
        
        save_content_hash(hash_name, content_hash_value, file_type="html")
        
        add_metadata_entry(
            hash_name=hash_name,
            original_name=urlparse(url).path.split('/')[-1] or "index.html",
            url=url,
            content_hash=content_hash_value,
            duplicate_of=None
        )
        
        soup = BeautifulSoup(html_content, 'lxml')
        
        for link in soup.find_all('a', href=True):
            href = link['href']
            absolute_url = urljoin(url, href)
            
            if base_domain in urlparse(absolute_url).netloc:
                url_lower = absolute_url.lower()
                is_document_link = any(url_lower.endswith(ext) or ext in url_lower for ext in DOCUMENT_EXTENSIONS)
                
                if is_document_link:
                    download_document(absolute_url)
                else:
                    found_links.append(absolute_url)
        
    except requests.exceptions.RequestException:
        pass
    except Exception:
        pass
    
    return found_links

def download_document(url: str) -> bool:
    """Télécharge un document binaire avec gestion intelligente de la déduplication."""
    try:
        hash_name = hash_url(url)
        
        headers = {"User-Agent": SCRAPING_CONFIG["user_agent"]}
        response = requests.get(
            url,
            headers=headers,
            timeout=SCRAPING_CONFIG["timeout"],
            verify=SCRAPING_CONFIG["verify_ssl"],
            stream=True
        )
        response.raise_for_status()
        
        extension = None
        
        for ext in DOCUMENT_EXTENSIONS:
            if ext in url.lower():
                extension = ext
                break
        
        if not extension:
            content_type = response.headers.get('Content-Type', '').lower()
            content_type_map = {
                'application/pdf': '.pdf',
                'application/msword': '.doc',
                'application/vnd.openxmlformats-officedocument.wordprocessingml.document': '.docx',
                'application/vnd.ms-excel': '.xls',
                'application/vnd.openxmlformats-officedocument.spreadsheetml.sheet': '.xlsx',
                'application/vnd.ms-powerpoint': '.ppt',
                'application/vnd.openxmlformats-officedocument.presentationml.presentation': '.pptx',
                'application/vnd.oasis.opendocument.text': '.odt',
                'application/vnd.oasis.opendocument.spreadsheet': '.ods',
                'application/vnd.oasis.opendocument.presentation': '.odp',
                'application/rtf': '.rtf',
                'text/csv': '.csv',
                'application/json': '.json',
            }
            
            for mime_type, ext in content_type_map.items():
                if mime_type in content_type:
                    extension = ext
                    break
        
        if not extension:
            extension = '.bin'
        
        temp_file = OUTPUT_STRUCTURE["documents"] / f"{hash_name}_temp{extension}"
        with open(temp_file, 'wb') as f:
            for chunk in response.iter_content(chunk_size=8192):
                f.write(chunk)
        
        content_hash_value = hash_content(temp_file)
        
        all_existing_hashes = get_all_content_hashes()
        if content_hash_value in all_existing_hashes:
            temp_file.unlink()
            return False
        
        old_content_hash = load_content_hash(hash_name, file_type="document")
        
        if old_content_hash is not None:
            if old_content_hash == content_hash_value:
                temp_file.unlink()
                return False
            else:
                delete_old_file(hash_name, file_type="document")
        
        final_file = OUTPUT_STRUCTURE["documents"] / f"{hash_name}{extension}"
        temp_file.rename(final_file)
        
        save_content_hash(hash_name, content_hash_value, file_type="document")
        
        original_name = urlparse(url).path.split('/')[-1]
        if not original_name or '.' not in original_name:
            original_name = f"document{extension}"
        
        add_metadata_entry(
            hash_name=hash_name,
            original_name=original_name,
            url=url,
            content_hash=content_hash_value,
            duplicate_of=None
        )
        
        return True
        
    except requests.exceptions.RequestException:
        return False
    except Exception:
        return False

def crawl_website(start_urls: List[str], base_domain: str, max_pages: int = None) -> Dict[str, Any]:
    """Crawle un site web de manière concurrente avec gestion intelligente des URLs."""
    if max_pages is None:
        max_pages = SCRAPING_CONFIG["max_pages"]
    
    num_workers = SCRAPING_CONFIG["num_workers"]
    delay = SCRAPING_CONFIG["delay"]
    
    urls_to_visit = deque(start_urls)
    visited_urls = set()
    lock = threading.Lock()
    
    stats = {
        "total_pages": 0,
        "pages_scraped": 0,
        "pages_skipped": 0,
        "documents_downloaded": 0,
        "errors": 0,
        "start_time": datetime.now().astimezone()
    }
    
    def worker_scrape(url: str) -> Tuple[str, List[str], bool]:
        try:
            time.sleep(delay)
            found_links = scrape_page(url, base_domain)
            return (url, found_links, True)
        except Exception:
            return (url, [], False)
    
    with ThreadPoolExecutor(max_workers=num_workers) as executor:
        futures = {}
        
        with tqdm(total=max_pages, desc="Crawling", unit="pages") as pbar:
            
            while urls_to_visit or futures:
                
                while urls_to_visit and len(futures) < num_workers and stats["total_pages"] < max_pages:
                    
                    with lock:
                        if not urls_to_visit:
                            break
                        
                        url = urls_to_visit.popleft()
                        
                        if url in visited_urls:
                            continue
                        
                        visited_urls.add(url)
                        stats["total_pages"] += 1
                    
                    future = executor.submit(worker_scrape, url)
                    futures[future] = url
                
                if futures:
                    done, _ = as_completed(futures, timeout=1), None
                    
                    for future in list(futures.keys()):
                        if future.done():
                            url = futures.pop(future)
                            
                            try:
                                result_url, found_links, success = future.result()
                                
                                if success:
                                    stats["pages_scraped"] += 1
                                    
                                    with lock:
                                        for link in found_links:
                                            if link not in visited_urls and link not in urls_to_visit:
                                                urls_to_visit.append(link)
                                else:
                                    stats["errors"] += 1
                                
                            except Exception:
                                stats["errors"] += 1
                            
                            pbar.update(1)
                            pbar.set_postfix({
                                "scrapées": stats["pages_scraped"],
                                "erreurs": stats["errors"],
                                "file": len(urls_to_visit)
                            })
                
                if stats["total_pages"] >= max_pages:
                    break
    
    stats["end_time"] = datetime.now().astimezone()
    stats["duration"] = (stats["end_time"] - stats["start_time"]).total_seconds()
    stats["pages_skipped"] = stats["total_pages"] - stats["pages_scraped"] - stats["errors"]
    
    return stats

# ============================================
# CONVERSION HTML → MARKDOWN
# ============================================

def convert_html_to_markdown(html_file: Path) -> Optional[Path]:
    """Convertit un fichier HTML en Markdown propre avec nettoyage intelligent."""
    try:
        with open(html_file, 'r', encoding='utf-8') as f:
            html_content = f.read()
        
        soup = BeautifulSoup(html_content, 'lxml')
        
        main_content = None
        
        if not main_content:
            main_content = soup.find('div', class_=re.compile(r'gdlr-core-page-builder-body|kingster-page-wrapper', re.I))
        
        if not main_content:
            main_content = soup.find('main')
        
        if not main_content:
            main_content = soup.find('article')
        
        if not main_content:
            main_content = soup.find('div', class_=re.compile(r'content|main|post|entry|page-content', re.I))
        
        if not main_content:
            main_content = soup.find('div', id=re.compile(r'content|main|primary', re.I))
        
        if not main_content:
            main_content = soup.find('body')
        
        if main_content is None:
            return None
        
        for tag in main_content.find_all(['script', 'style', 'noscript']):
            tag.decompose()
        
        for tag in main_content.find_all(['nav', 'aside']):
            tag.decompose()
        
        for selector in ['.navigation', '.sidebar', '.menu', '.breadcrumb']:
            for element in main_content.select(selector):
                element.decompose()
        
        for img in main_content.find_all('img'):
            alt_text = img.get('alt', '').strip()
            if alt_text:
                img.replace_with(f"[Image: {alt_text}]")
            else:
                img.decompose()
        
        text_content = main_content.get_text(strip=True)
        if len(text_content) < 50:
            return None
        
        markdown = html_to_md(
            str(main_content),
            heading_style="ATX",
            bullets="-",
            strong_em_symbol="**",
            strip=['img']
        )
        
        markdown = re.sub(r'\n{3,}', '\n\n', markdown)
        markdown = re.sub(r' +\n', '\n', markdown)
        markdown = re.sub(r'\[([^\]]+)\]\(\s*\)', r'\1', markdown)
        markdown = markdown.strip()
        
        if len(markdown) < 50:
            return None
        
        md_file = OUTPUT_STRUCTURE["converted_html"] / f"{html_file.stem}.md"
        with open(md_file, 'w', encoding='utf-8') as f:
            f.write(markdown)
        
        return md_file
        
    except Exception:
        return None

def convert_all_html_to_markdown(batch_size: int = 100) -> Dict[str, int]:
    """Convertit tous les fichiers HTML du dossier html_brut/ en Markdown."""
    html_dir = OUTPUT_STRUCTURE["html_brut"]
    md_dir = OUTPUT_STRUCTURE["converted_html"]
    
    html_files = list(html_dir.glob("*.html"))
    
    if not html_files:
        return {"success": 0, "errors": 0, "skipped": 0, "total": 0}
    
    stats = {
        "success": 0,
        "errors": 0,
        "skipped": 0,
        "total": len(html_files)
    }
    
    for html_file in tqdm(html_files, desc="Conversion", unit="fichiers"):
        
        md_file = md_dir / f"{html_file.stem}.md"
        if md_file.exists():
            stats["skipped"] += 1
            continue
        
        result = convert_html_to_markdown(html_file)
        
        if result:
            stats["success"] += 1
        else:
            stats["errors"] += 1
    
    return stats

# ============================================
# NETTOYAGE DES FICHIERS HTML CORROMPUS
# ============================================

def clean_corrupted_html_files(dry_run: bool = True) -> Dict[str, Any]:
    """Identifie et DÉPLACE les fichiers HTML corrompus (binaires) vers un dossier d'isolation."""
    html_dir = OUTPUT_STRUCTURE["html_brut"]
    hash_dir = OUTPUT_STRUCTURE["html_hashes"]
    corrupted_dir = OUTPUT_ROOT / "corrupted_files"
    
    if not dry_run:
        corrupted_dir.mkdir(exist_ok=True)
    
    html_files = list(html_dir.glob("*.html"))
    
    stats = {
        "total_checked": len(html_files),
        "corrupted_pdf": 0,
        "corrupted_zip": 0,
        "corrupted_other": 0,
        "moved": 0,
        "errors": 0,
        "corrupted_files": []
    }
    
    for html_file in tqdm(html_files, desc="Vérification HTML", unit="fichiers"):
        try:
            with open(html_file, 'rb') as f:
                first_bytes = f.read(10)
            
            is_corrupted = False
            corruption_type = None
            
            if first_bytes.startswith(b'%PDF'):
                is_corrupted = True
                corruption_type = "PDF"
                stats["corrupted_pdf"] += 1
            
            elif first_bytes.startswith(b'PK'):
                is_corrupted = True
                corruption_type = "ZIP/Office"
                stats["corrupted_zip"] += 1
            
            elif len([b for b in first_bytes if b < 32 or b > 126]) > 5:
                is_corrupted = True
                corruption_type = "Binaire"
                stats["corrupted_other"] += 1
            
            if is_corrupted:
                stats["corrupted_files"].append({
                    "file": html_file.name,
                    "type": corruption_type,
                    "hash": html_file.stem
                })
                
                if not dry_run:
                    corrupted_html = corrupted_dir / html_file.name
                    html_file.rename(corrupted_html)
                    
                    hash_file = hash_dir / f"{html_file.stem}.hash"
                    if hash_file.exists():
                        corrupted_hash = corrupted_dir / hash_file.name
                        hash_file.rename(corrupted_hash)
                    
                    metadata = load_metadata()
                    if html_file.stem in metadata:
                        metadata[html_file.stem]["status"] = "corrupted"
                        metadata[html_file.stem]["corruption_type"] = corruption_type
                        metadata[html_file.stem]["moved_to"] = str(corrupted_html)
                        metadata[html_file.stem]["moved_at"] = datetime.now().astimezone().isoformat()
                        save_metadata(metadata)
                    
                    stats["moved"] += 1
        
        except Exception:
            stats["errors"] += 1
    
    return stats

# ============================================
# ANALYSE & NETTOYAGE
# ============================================

def analyze_markdown_files() -> Dict[str, Any]:
    """Analyse complète des fichiers Markdown convertis."""
    md_dir = OUTPUT_STRUCTURE["converted_html"]
    md_files = list(md_dir.glob("*.md"))
    
    if not md_files:
        return {}
    
    file_data = []
    content_hashes = {}
    
    for md_file in tqdm(md_files, desc="Lecture des fichiers", unit="fichiers"):
        try:
            with open(md_file, 'r', encoding='utf-8') as f:
                content = f.read()
            
            lines = content.split('\n')
            words = content.split()
            size_bytes = md_file.stat().st_size
            
            content_hash = hashlib.sha256(content.encode('utf-8')).hexdigest()
            
            data = {
                "filename": md_file.name,
                "path": md_file,
                "size_bytes": size_bytes,
                "size_kb": size_bytes / 1024,
                "num_lines": len(lines),
                "num_words": len(words),
                "num_chars": len(content),
                "content_hash": content_hash,
                "is_empty": len(content.strip()) == 0,
                "is_short": len(content.strip()) < 100,
            }
            
            file_data.append(data)
            
            if content_hash not in content_hashes:
                content_hashes[content_hash] = []
            content_hashes[content_hash].append(md_file.name)
            
        except Exception:
            pass
    
    duplicates = {h: files for h, files in content_hashes.items() if len(files) > 1}
    
    stats = {
        "total_files": len(file_data),
        "duplicate_groups": len(duplicates),
        "duplicate_files": sum(len(files) - 1 for files in duplicates.values()),
    }
    
    return {
        "stats": stats,
        "file_data": file_data,
        "duplicates": duplicates,
    }

def deduplicate_markdown_files(dry_run=True):
    """Supprime les fichiers Markdown dupliqués (même contenu)."""
    md_folder = OUTPUT_STRUCTURE["converted_html"]
    metadata_path = OUTPUT_ROOT / "metadata.json"
    
    if metadata_path.exists():
        with open(metadata_path, 'r', encoding='utf-8') as f:
            metadata = json.load(f)
    else:
        metadata = {}
    
    content_hashes = {}
    md_files = list(md_folder.glob("*.md"))
    
    for md_file in md_files:
        content = md_file.read_text(encoding='utf-8', errors='ignore')
        content_hash = hashlib.sha256(content.encode('utf-8', errors='ignore')).hexdigest()
        
        if content_hash not in content_hashes:
            content_hashes[content_hash] = []
        content_hashes[content_hash].append(md_file)
    
    duplicate_groups = {h: files for h, files in content_hashes.items() if len(files) > 1}
    
    if not duplicate_groups:
        return {
            "duplicate_groups": 0,
            "files_kept": 0,
            "files_removed": 0,
            "removed_files": []
        }
    
    files_kept = 0
    files_removed = 0
    removed_files = []
    
    for content_hash, files in duplicate_groups.items():
        sorted_files = sorted(files, key=lambda f: f.name)
        kept_file = sorted_files[0]
        files_to_remove = sorted_files[1:]
        
        files_kept += 1
        
        for remove_file in files_to_remove:
            files_removed += 1
            removed_files.append(remove_file.name)
            
            if not dry_run:
                for url, data in metadata.items():
                    if data.get("markdown_file") == remove_file.name:
                        data["status"] = "duplicate_removed"
                        data["duplicate_of"] = kept_file.name
                        data["removed_at"] = datetime.now().astimezone().isoformat()
                        break
                
                remove_file.unlink()
    
    if not dry_run:
        with open(metadata_path, 'w', encoding='utf-8') as f:
            json.dump(metadata, f, indent=2, ensure_ascii=False)
    
    return {
        "duplicate_groups": len(duplicate_groups),
        "files_kept": files_kept,
        "files_removed": files_removed,
        "removed_files": removed_files
    }

# ============================================
# CONVERSION DOCUMENTS → MARKDOWN (DOCLING)
# ============================================

def convert_document_to_markdown(doc_file: Path) -> Optional[Path]:
    """Convertit un document binaire en Markdown avec Docling."""
    try:
        from docling.document_converter import DocumentConverter
    except ImportError:
        return None
    
    try:
        converter = DocumentConverter()
        result = converter.convert(str(doc_file))
        markdown = result.document.export_to_markdown()
        
        markdown = re.sub(r'\n{3,}', '\n\n', markdown)
        markdown = markdown.strip()
        
        if len(markdown) < 50:
            return None
        
        md_file = OUTPUT_STRUCTURE["converted_documents"] / f"{doc_file.stem}.md"
        with open(md_file, 'w', encoding='utf-8') as f:
            f.write(markdown)
        
        return md_file
        
    except Exception:
        return None

def convert_all_documents_to_markdown() -> Dict[str, int]:
    """Convertit tous les documents binaires du dossier documents/ en Markdown."""
    try:
        from docling.document_converter import DocumentConverter
        DOCLING_AVAILABLE = True
    except ImportError:
        DOCLING_AVAILABLE = False
    
    if not DOCLING_AVAILABLE:
        return {"success": 0, "errors": 0, "skipped": 0, "total": 0, "not_available": True}
    
    doc_dir = OUTPUT_STRUCTURE["documents"]
    md_dir = OUTPUT_STRUCTURE["converted_documents"]
    
    all_files = [f for f in doc_dir.iterdir() if f.is_file()]
    doc_files = [f for f in all_files if not f.name.endswith('_temp') and f.suffix.lower() in [ext.lower() for ext in DOCUMENT_EXTENSIONS]]
    
    if not doc_files:
        return {"success": 0, "errors": 0, "skipped": 0, "total": 0}
    
    stats = {
        "success": 0,
        "errors": 0,
        "skipped": 0,
        "total": len(doc_files)
    }
    
    for doc_file in tqdm(doc_files, desc="Conversion docs", unit="docs"):
        md_file = md_dir / f"{doc_file.stem}.md"
        if md_file.exists():
            stats["skipped"] += 1
            continue
        
        result = convert_document_to_markdown(doc_file)
        
        if result:
            stats["success"] += 1
        else:
            stats["errors"] += 1
    
    return stats

def analyze_converted_documents() -> Dict[str, Any]:
    """Analyse complète des fichiers Markdown convertis depuis les documents."""
    md_dir = OUTPUT_STRUCTURE["converted_documents"]
    md_files = list(md_dir.glob("*.md"))
    
    if not md_files:
        return {}
    
    file_data = []
    content_hashes = {}
    
    for md_file in tqdm(md_files, desc="Analyse docs MD", unit="fichiers"):
        try:
            with open(md_file, 'r', encoding='utf-8') as f:
                content = f.read()
            
            size_bytes = md_file.stat().st_size
            content_hash = hashlib.sha256(content.encode('utf-8')).hexdigest()
            
            data = {
                "filename": md_file.name,
                "path": md_file,
                "size_kb": size_bytes / 1024,
                "content_hash": content_hash,
            }
            
            file_data.append(data)
            
            if content_hash not in content_hashes:
                content_hashes[content_hash] = []
            content_hashes[content_hash].append(md_file.name)
            
        except Exception:
            pass
    
    duplicates = {h: files for h, files in content_hashes.items() if len(files) > 1}
    
    stats = {
        "total_files": len(file_data),
        "duplicate_groups": len(duplicates),
        "duplicate_files": sum(len(files) - 1 for files in duplicates.values()),
    }
    
    return {
        "stats": stats,
        "file_data": file_data,
        "duplicates": duplicates,
    }

def deduplicate_converted_documents(dry_run=True):
    """Supprime les fichiers Markdown de documents dupliqués (même contenu)."""
    md_folder = OUTPUT_STRUCTURE["converted_documents"]
    metadata_path = OUTPUT_ROOT / "metadata.json"
    
    if metadata_path.exists():
        with open(metadata_path, 'r', encoding='utf-8') as f:
            metadata = json.load(f)
    else:
        metadata = {}
    
    content_hashes = {}
    md_files = list(md_folder.glob("*.md"))
    
    for md_file in md_files:
        content = md_file.read_text(encoding='utf-8', errors='ignore')
        content_hash = hashlib.sha256(content.encode('utf-8', errors='ignore')).hexdigest()
        
        if content_hash not in content_hashes:
            content_hashes[content_hash] = []
        content_hashes[content_hash].append(md_file)
    
    duplicate_groups = {h: files for h, files in content_hashes.items() if len(files) > 1}
    
    if not duplicate_groups:
        return {
            "duplicate_groups": 0,
            "files_kept": 0,
            "files_removed": 0,
            "removed_files": []
        }
    
    files_kept = 0
    files_removed = 0
    removed_files = []
    
    for content_hash, files in duplicate_groups.items():
        sorted_files = sorted(files, key=lambda f: f.name)
        kept_file = sorted_files[0]
        files_to_remove = sorted_files[1:]
        
        files_kept += 1
        
        for remove_file in files_to_remove:
            files_removed += 1
            removed_files.append(remove_file.name)
            
            if not dry_run:
                for url, data in metadata.items():
                    if data.get("converted_document") == remove_file.name:
                        data["status"] = "duplicate_removed"
                        data["duplicate_of"] = kept_file.name
                        data["removed_at"] = datetime.now().astimezone().isoformat()
                        break
                
                remove_file.unlink()
    
    if not dry_run:
        with open(metadata_path, 'w', encoding='utf-8') as f:
            json.dump(metadata, f, indent=2, ensure_ascii=False)
    
    return {
        "duplicate_groups": len(duplicate_groups),
        "files_kept": files_kept,
        "files_removed": files_removed,
        "removed_files": removed_files
    }

# ============================================
# ANALYSE GLOBALE DES RÉSULTATS
# ============================================

def analyze_crawl_results():
    """Analyse les fichiers téléchargés et affiche des statistiques globales."""
    html_files = list(OUTPUT_STRUCTURE["html_brut"].glob("*.html"))
    html_hashes = list(OUTPUT_STRUCTURE["html_hashes"].glob("*.hash"))
    
    doc_files = [f for f in OUTPUT_STRUCTURE["documents"].iterdir() if f.is_file() and not f.name.endswith('_temp')]
    doc_hashes = list(OUTPUT_STRUCTURE["document_hashes"].glob("*.hash"))
    
    doc_types = {}
    for doc_file in doc_files:
        ext = doc_file.suffix.lower()
        doc_types[ext] = doc_types.get(ext, 0) + 1
    
    metadata = load_metadata()
    duplicates = sum(1 for entry in metadata.values() if entry.get("duplicate_of") is not None)
    originals = len(metadata) - duplicates
    
    total_size_html = sum(f.stat().st_size for f in html_files)
    total_size_docs = sum(f.stat().st_size for f in doc_files)
    total_size = total_size_html + total_size_docs
    
    return {
        "html_count": len(html_files),
        "html_hashes": len(html_hashes),
        "doc_count": len(doc_files),
        "doc_hashes": len(doc_hashes),
        "doc_types": doc_types,
        "metadata_count": len(metadata),
        "duplicates": duplicates,
        "originals": originals,
        "total_size_mb": total_size / 1024 / 1024,
        "html_size_mb": total_size_html / 1024 / 1024,
        "docs_size_mb": total_size_docs / 1024 / 1024,
    }

# ============================================
# NETTOYAGE
# ============================================

def clean_orphan_hash_files():
    """Nettoie les fichiers .hash orphelins (sans fichier HTML ou document correspondant)."""
    html_orphans = 0
    document_orphans = 0
    
    html_hash_dir = OUTPUT_STRUCTURE["html_hashes"]
    html_brut_dir = OUTPUT_STRUCTURE["html_brut"]
    
    for hash_file in html_hash_dir.glob("*.hash"):
        hash_name = hash_file.stem
        html_file = html_brut_dir / f"{hash_name}.html"
        
        if not html_file.exists():
            hash_file.unlink()
            html_orphans += 1
    
    doc_hash_dir = OUTPUT_STRUCTURE["document_hashes"]
    doc_dir = OUTPUT_STRUCTURE["documents"]
    
    for hash_file in doc_hash_dir.glob("*.hash"):
        hash_name = hash_file.stem
        matching_docs = list(doc_dir.glob(f"{hash_name}.*"))
        
        if not matching_docs:
            hash_file.unlink()
            document_orphans += 1
    
    total_removed = html_orphans + document_orphans
    
    return {
        "html_orphans_removed": html_orphans,
        "document_orphans_removed": document_orphans,
        "total_removed": total_removed
    }

# ============================================
# MAIN
# ============================================

if __name__ == "__main__":
    print("Pipeline de Scraping Web - Miabé IA")
    print("=" * 70)
    
    # Initialisation
    print("\n[1/11] Création de la structure de dossiers...")
    create_output_structure()
    
    # Réparation metadata si nécessaire
    print("\n[2/11] Vérification/Réparation metadata.json...")
    repair_metadata_json()
    
    # Scraping
    print("\n[3/11] Crawling du site web...")
    stats = crawl_website(
        start_urls=[UNIV_LOME_URLS["site_principal"]],
        base_domain="univ-lome.tg",
        max_pages=50
    )
    print(f"Pages scrapées: {stats['pages_scraped']}/{stats['total_pages']}")
    
    # Nettoyage fichiers HTML corrompus
    print("\n[4/11] Nettoyage des fichiers HTML corrompus...")
    corrupt_stats = clean_corrupted_html_files(dry_run=False)
    print(f"Fichiers corrompus déplacés: {corrupt_stats['moved']}")
    
    # Conversion HTML → Markdown
    print("\n[5/11] Conversion HTML → Markdown...")
    conv_stats = convert_all_html_to_markdown()
    print(f"Convertis: {conv_stats['success']}, Erreurs: {conv_stats['errors']}")
    
    # Analyse HTML MD
    print("\n[6/11] Analyse des fichiers Markdown HTML...")
    analysis = analyze_markdown_files()
    if analysis:
        print(f"Total: {analysis['stats']['total_files']} fichiers")
        print(f"Doublons: {analysis['stats']['duplicate_groups']} groupes")
    
    # Déduplication HTML MD
    print("\n[7/11] Déduplication Markdown HTML...")
    dedup_stats = deduplicate_markdown_files(dry_run=False)
    print(f"Fichiers supprimés: {dedup_stats['files_removed']}")
    
    # Conversion Documents → Markdown
    print("\n[8/11] Conversion Documents → Markdown (Docling)...")
    doc_conv_stats = convert_all_documents_to_markdown()
    if doc_conv_stats.get('not_available'):
        print("Docling non installé, conversion documents ignorée")
    else:
        print(f"Convertis: {doc_conv_stats['success']}, Erreurs: {doc_conv_stats['errors']}")
    
    # Analyse Documents MD
    print("\n[9/11] Analyse des fichiers Markdown Documents...")
    doc_analysis = analyze_converted_documents()
    if doc_analysis:
        print(f"Total: {doc_analysis['stats']['total_files']} fichiers")
        print(f"Doublons: {doc_analysis['stats']['duplicate_groups']} groupes")
    
    # Déduplication Documents MD
    print("\n[10/11] Déduplication Markdown Documents...")
    doc_dedup_stats = deduplicate_converted_documents(dry_run=False)
    print(f"Fichiers supprimés: {doc_dedup_stats['files_removed']}")
    
    # Nettoyage
    print("\n[11/11] Nettoyage des fichiers orphelins...")
    clean_stats = clean_orphan_hash_files()
    print(f"Hash orphelins supprimés: {clean_stats['total_removed']}")
    
    # Statistiques finales
    print("\n" + "=" * 70)
    print("STATISTIQUES FINALES")
    print("=" * 70)
    final_stats = analyze_crawl_results()
    print(f"Pages HTML         : {final_stats['html_count']} ({final_stats['html_size_mb']:.2f} MB)")
    print(f"Documents          : {final_stats['doc_count']} ({final_stats['docs_size_mb']:.2f} MB)")
    print(f"Taille totale      : {final_stats['total_size_mb']:.2f} MB")
    print(f"Métadonnées        : {final_stats['metadata_count']} entrées")
    print(f"Doublons évités    : {final_stats['duplicates']}")
    if final_stats['doc_types']:
        print(f"\nTypes de documents :")
        for ext, count in sorted(final_stats['doc_types'].items(), key=lambda x: -x[1]):
            print(f"  {ext}: {count}")
    
    print("\n" + "=" * 70)
    print("Pipeline terminé avec succès!")
