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

    user_clean = {k: v for k, v in user.items() if k not in ("_id", "password_hash")}
    user_clean['last_login_at'] = datetime.utcnow().isoformat()
    return True, "Connexion réussie.", user_clean
