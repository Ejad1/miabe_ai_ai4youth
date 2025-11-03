"""MiabéIA.auth.logic

Logique métier pour l'authentification locale.
- register_user(users_coll, name, email, password)
- authenticate_user(users_coll, email, password)
- get_user_by_email(users_coll, email)

Contrats:
- Toutes les fonctions acceptent une collection pymongo `users_coll` (injection) afin d'être testables.
- Les fonctions retournent des tuples: (ok: bool, message: str, user: dict|None)

Sécurité:
- bcrypt pour le hachage des mots de passe (rounds=12)
- normalisation de l'email (lowercase/trim)

"""
from __future__ import annotations

import uuid
from datetime import datetime
from typing import Tuple, Optional, Dict, Any

import bcrypt
from pymongo.errors import DuplicateKeyError, PyMongoError
import secrets
import hashlib
from datetime import timedelta
from bson.objectid import ObjectId


def _hash_token(token: str) -> str:
    return hashlib.sha256(token.encode('utf-8')).hexdigest()


def _normalize_email(email: str) -> str:
    return (email or "").strip().lower()


def _hash_password(password: str) -> str:
    """Hash un mot de passe avec bcrypt et renvoie la chaîne décodée."""
    if password is None:
        raise ValueError("Password must be provided")
    salt = bcrypt.gensalt(rounds=12)
    hashed = bcrypt.hashpw(password.encode('utf-8'), salt)

    return hashed.decode('utf-8')


def _check_password(password: str, hashed: str) -> bool:
    if not hashed:
        return False
    try:
        return bcrypt.checkpw(password.encode('utf-8'), hashed.encode('utf-8'))
    except Exception:
        return False


def get_user_by_email(users_coll, email: str) -> Optional[Dict[str, Any]]:
    """Récupère un utilisateur par email (normalisé). Retourne None si introuvable.
    Ne renvoie pas le champ password_hash.
    """
    if users_coll is None:
        return None
    email_n = _normalize_email(email)
    user = users_coll.find_one({"email": email_n})
    if not user:
        return None
    
    
    # Convertir l'objet Mongo en dict simple et supprimer password_hash et _id
    user_clean = {k: v for k, v in user.items() if k not in ("_id", "password_hash")}
    return user_clean


def register_user(users_coll, name: str, email: str, password: str) -> Tuple[bool, str, Optional[Dict[str, Any]]]:
    """Crée un nouvel utilisateur.

    Returns (ok, message, user_dict)
    - ok True: user_dict contient les champs publics (user_id, email, name, created_at,...)
    - ok False: message décrit l'erreur
    """
    if users_coll is None:
        return False, "Base utilisateurs indisponible.", None

    email_n = _normalize_email(email)
    if not email_n:
        return False, "Email requis.", None
    if not password or len(password) < 6:
        return False, "Le mot de passe doit comporter au moins 6 caractères.", None

    user_id = str(uuid.uuid4())
    created_at = datetime.now().isoformat()

    try:
        pwd_hash = _hash_password(password)
    except Exception as e:
        return False, f"Erreur de hachage du mot de passe: {e}", None

    doc = {
        "user_id": user_id,
        "email": email_n,
        "name": (name or "").strip(),
        "password_hash": pwd_hash,
        "created_at": created_at,
        "last_login_at": None,
        "status": "active"
    }

    try:
        users_coll.insert_one(doc)
        user_clean = {k: v for k, v in doc.items() if k != 'password_hash'}
        return True, "Compte créé avec succès.", user_clean
    except DuplicateKeyError:
        return False, "Cet email est déjà enregistré.", None
    except PyMongoError as e:
        return False, f"Erreur base de données: {e}", None
    except Exception as e:
        return False, f"Erreur inconnue: {e}", None


def authenticate_user(users_coll, email: str, password: str) -> Tuple[bool, str, Optional[Dict[str, Any]]]:
    """Authentifie un utilisateur par email et mot de passe.

    Si ok=True, renvoie user sans password_hash et met à jour last_login_at en base.
    """
    if users_coll is None:
        return False, "Base utilisateurs indisponible.", None

    email_n = _normalize_email(email)
    if not email_n or not password:
        return False, "Email et mot de passe requis.", None

    try:
        user = users_coll.find_one({"email": email_n})
    except PyMongoError as e:
        return False, f"Erreur base de données: {e}", None

    if not user:
        return False, "Utilisateur introuvable.", None

    stored_hash = user.get('password_hash', '')
    if not _check_password(password, stored_hash):
        return False, "Mot de passe incorrect.", None

    # update last_login_at
    try:
        now_iso = datetime.utcnow().isoformat()
        users_coll.update_one({"_id": user.get("_id")}, {"$set": {"last_login_at": now_iso}})
    except Exception:
        # non-blocking: on continue même si l'update échoue
        pass

    # Return user document including the Mongo `_id` but without password_hash
    user_clean = {k: v for k, v in user.items() if k != 'password_hash'}
    user_clean['last_login_at'] = datetime.utcnow().isoformat()
    return True, "Connexion réussie.", user_clean


# --- Session management helpers (token generation / validation / deletion)
def generate_session_token(users_coll, user_obj_id, ttl_days: int = 7) -> str:
    """Génère un token de session, stocke son hash dans la collection `sessions` et
    retourne le token brut à envoyer au client (cookie).

    - users_coll: collection `users` (utilisée pour retrouver la database)
    - user_obj_id: ObjectId (ou valeur convertible) de l'utilisateur
    """
    db = users_coll.database
    sessions_coll = db['sessions']

    # créer index TTL si nécessaire
    try:
        sessions_coll.create_index('expires_at', expireAfterSeconds=0)
    except Exception:
        # non bloquant
        pass

    raw = secrets.token_urlsafe(32)
    h = _hash_token(raw)
    now = datetime.utcnow()
    expires = now + timedelta(days=ttl_days)

    # stocker user reference; si user_obj_id est une string qui représente ObjectId, on tente la conversion
    uid = user_obj_id
    try:
        # si c'est un str convertible en ObjectId
        if isinstance(user_obj_id, str):
            uid = ObjectId(user_obj_id)
    except Exception:
        uid = user_obj_id

    sessions_coll.insert_one({
        'token_hash': h,
        'user_id': uid,
        'created_at': now,
        'expires_at': expires,
        'last_used_at': now,
    })
    return raw


def validate_session_token(users_coll, token: str):
    """Valide un token brut reçu du cookie.
    Retourne le document user complet (tel qu'en base) si valide, sinon None.
    """
    if not token:
        return None
    db = users_coll.database
    sessions_coll = db['sessions']
    h = _hash_token(token)
    sess = sessions_coll.find_one({'token_hash': h})
    if not sess:
        return None
    # vérifier expiry
    exp = sess.get('expires_at')
    if exp and exp < datetime.utcnow():
        try:
            sessions_coll.delete_one({'_id': sess.get('_id')})
        except Exception:
            pass
        return None

    # mise à jour last_used_at
    try:
        sessions_coll.update_one({'_id': sess.get('_id')}, {'$set': {'last_used_at': datetime.utcnow()}})
    except Exception:
        pass

    # récupérer l'utilisateur
    user = users_coll.find_one({'_id': sess.get('user_id')})
    return user


def delete_session_token(users_coll, token: str) -> bool:
    """Supprime la session correspondante au token brut. Retourne True si supprimé."""
    if not token:
        return False
    db = users_coll.database
    sessions_coll = db['sessions']
    h = _hash_token(token)
    res = sessions_coll.delete_one({'token_hash': h})
    return (res.deleted_count or 0) > 0
