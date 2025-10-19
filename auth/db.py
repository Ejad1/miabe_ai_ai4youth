"""MiabéIA.auth.db

Couche d'accès à MongoDB pour le module d'authentification.
- Résolution des secrets (priorité: Code/secrets.toml local, puis st.secrets pour Cloud, puis env vars)
- Fournit un client Mongo réutilisable via st.cache_resource
- Expose collections: users_coll, sessions_coll
- Fournit ensure_indexes() pour créer un index unique sur email

Conventions:
- Les chemins locaux utilisent Code/secrets.toml (vous avez standardisé cela)
- Le client Mongo utilise TLS (certifi) et des timeouts raisonnables

Usage:
from MiabéIA.auth.db import get_mongo_collections, ensure_user_indexes
client, db, users_coll, sessions_coll = get_mongo_collections()
ensure_user_indexes(users_coll)
"""
from __future__ import annotations

import pathlib
import os
from typing import Optional, Tuple, Any

import streamlit as st
from pymongo import MongoClient
from pymongo.errors import PyMongoError
import certifi

try:
    import tomllib  # Python 3.11+
except Exception:
    # Python < 3.11: try the backport 'tomli' if installed and alias it to tomllib
    try:
        import tomli as tomllib  # type: ignore
    except Exception:
        tomllib = None


@st.cache_resource
def _create_mongo_client(uri: str, database: str, collection_name: str) -> Tuple[MongoClient, Any]:
    """Crée un MongoClient robuste avec TLS et renvoie (client, collection).
    Cache la ressource pour réutilisation par Streamlit.
    """
    client = MongoClient(
        uri,
        tls=True,
        tlsCAFile=certifi.where(),
        serverSelectionTimeoutMS=45000,
        connectTimeoutMS=20000,
        socketTimeoutMS=20000,
    )
    db = client[database]
    coll = db[collection_name]
    return client, coll


def _read_local_secrets_candidates() -> list[pathlib.Path]:
    candidates = []
    try:
        file_dir = pathlib.Path(__file__).parents[1]
        candidates.append(file_dir / 'secrets.toml')  # MiabéIA/secrets.toml
    except Exception:
        pass
    cwd = pathlib.Path.cwd()
    candidates.extend([
        cwd / 'Code' / 'secrets.toml',
        cwd / 'secrets.toml',
        pathlib.Path('d:/CampusGPT/Code/secrets.toml')
    ])
    return candidates


def _load_mongo_config_from_local() -> Optional[dict]:
    """Tente de charger la section [mongo] depuis un secrets.toml local.
    Retourne dict ou None.
    """
    if tomllib is None:
        return None
    for path in _read_local_secrets_candidates():
        try:
            if path.is_file():
                with open(path, 'rb') as f:
                    data = tomllib.load(f)
                mongo_conf = data.get('mongo')
                if mongo_conf and mongo_conf.get('uri'):
                    return {
                        'uri': mongo_conf.get('uri'),
                        'database': mongo_conf.get('database', 'ia_chat_db'),
                        'collection': mongo_conf.get('collection', 'user_sessions')
                    }
        except Exception:
            continue
    return None


def _load_mongo_config_from_st_secrets() -> Optional[dict]:
    try:
        if 'mongo' in st.secrets:
            mc = st.secrets['mongo']
            return {
                'uri': mc.get('uri'),
                'database': mc.get('database', 'ia_chat_db'),
                'collection': mc.get('collection', 'user_sessions')
            }
    except Exception:
        pass
    return None


def _load_mongo_config_from_env() -> Optional[dict]:
    uri = os.environ.get('MONGO_URI') or os.environ.get('MONGODB_URI')
    if uri:
        return {
            'uri': uri,
            'database': os.environ.get('MONGO_DB', 'ia_chat_db'),
            'collection': os.environ.get('MONGO_COLLECTION', 'user_sessions')
        }
    return None


@st.cache_resource
def get_mongo_collections() -> Optional[Tuple[MongoClient, Any, Any, Any]]:
    """Résout la configuration Mongo et retourne (client, db, users_coll, sessions_coll).

    Priorité:
    1) Code/secrets.toml local
    2) st.secrets['mongo'] (Streamlit Cloud)
    3) Variables d'environnement MONGO_URI/MONGO_DB/MONGO_COLLECTION
    """
    # 1) local
    conf = _load_mongo_config_from_local()
    if conf is None:
        # 2) st.secrets
        conf = _load_mongo_config_from_st_secrets()
    if conf is None:
        # 3) env
        conf = _load_mongo_config_from_env()
    if conf is None:
        st.error('MongoDB configuration introuvable. Ajoutez Code/secrets.toml ou st.secrets["mongo"] ou la variable MONGO_URI.')
        return None

    uri = conf['uri']
    database = conf.get('database', 'ia_chat_db')
    sessions_collection_name = conf.get('collection', 'user_sessions')

    try:
        client, sessions_coll = _create_mongo_client(uri, database, sessions_collection_name)
        db = client[database]
        users_coll = db['users']
        return client, db, users_coll, sessions_coll
    except PyMongoError as e:
        st.error(f"Erreur de connexion MongoDB: {e}")
        return None


def ensure_user_indexes(users_coll: Any) -> None:
    """Crée l'index unique sur email pour garantir l'unicité des comptes.

    N'affecte pas les documents existants; si l'index existe déjà, la fonction passe silencieusement.
    """
    try:
        users_coll.create_index('email', unique=True)
    except PyMongoError as e:
        # On log mais on nève throw pour ne pas casser l'application
        st.warning(f"Impossible de créer l'index unique sur email: {e}")


# MiabéIA/chat/db.py
from datetime import datetime
import uuid
from pymongo.collection import Collection
from typing import Dict, Any, List

def _now_iso() -> str:
    return datetime.now().isoformat()

def _generate_session_id() -> str:
    """Génère un identifiant unique pour chaque session."""
    return f"sess_{datetime.now().strftime('%Y%m%d_%H%M%S')}_{uuid.uuid4().hex[:6]}"

def create_new_session(coll: Collection, user_id_or_email: str, title: str) -> Dict[str, Any]:
    """Crée un nouveau document de session pour un utilisateur."""
    session_id = _generate_session_id()
    doc = {
        "session_id": session_id,
        "user_email": user_id_or_email,
        "title": title,
        "messages": [{
            "role": "assistant",
            "content": "Bonjour ! Je suis Miabé IA. Comment puis-je vous aider aujourd'hui ?",
            "timestamp": _now_iso()
        }],
        "created_at": _now_iso(),
        "updated_at": _now_iso()
    }
    coll.insert_one(doc)
    return doc


def load_session_messages(coll: Collection, session_id: str) -> List[Dict[str, Any]]:
    """Charge la liste des messages depuis une session existante."""
    session = coll.find_one({"session_id": session_id}, {"_id": 0, "messages": 1})
    if session:
        return session.get("messages", [])
    return []


def save_message(coll: Collection, session_id: str, role: str, content: str) -> None:
    """Ajoute un message à la session existante et met à jour la date."""
    coll.update_one(
        {"session_id": session_id},
        {
            "$push": {"messages": {
                "role": role,
                "content": content,
                "timestamp": _now_iso()
            }},
            "$set": {"updated_at": _now_iso()}
        },
        upsert=True  # crée la session si elle n’existe pas
    )
