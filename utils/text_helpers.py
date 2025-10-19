import re
import string
import unicodedata

def normalize_text(text: str) -> str:
    """Nettoie et normalise une chaîne de caractères."""
    text = text.lower()
    text = unicodedata.normalize('NFKD', text).encode('ascii', 'ignore').decode('utf-8')
    text = re.sub(r'[' + re.escape(string.punctuation) + r'\s]+', ' ', text).strip()
    return text
