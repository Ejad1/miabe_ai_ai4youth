import sys
import os
import streamlit as st
import requests
from typing import List, Dict

# Add the project root to the Python path to allow absolute imports from the root.
sys.path.insert(0, os.path.abspath(os.path.dirname(__file__)))

from auth.db import (
    get_mongo_collections,
    create_new_session,
    load_session_messages,
    save_message,
)

# Import auth UI helper
from auth.ui import require_auth_or_stop, logout

# Configuration de l'API Miabé IA
API_URL = os.getenv("API_URL", "http://127.0.0.1:8000/chat")

# Page title
st.title("Miabé  IA")

# If a user object exists in session_state we use it; otherwise ask for auth.
# Submitting Streamlit forms triggers an automatic rerun, so after a successful
# login/signup the next run will skip the auth block and show the chat.
if 'user' in st.session_state and st.session_state['user']:
    user = st.session_state['user']
else:
    user = require_auth_or_stop()

# If we just logged in (session now has user but we haven't refreshed layout),
# force one rerun so auth forms are removed from the page.
if user and st.session_state.get('_post_login_done') is not True:
    st.session_state['_post_login_done'] = True
    try:
        import time

        st.set_query_params(_auth_refresh=int(time.time()))
    except Exception:
        try:
            rerun = getattr(st, 'experimental_rerun', None)
            if callable(rerun):
                rerun()
        except Exception:
            pass

# app_streamlit.py (Lignes 58-61)
    # Resolve Mongo collections once and reuse
    cols = get_mongo_collections()
    sessions_coll = None
    if cols is not None:
        _client, _db, _users_coll, sessions_coll = cols


def _do_rerun() -> None:
    try:
        rerun = getattr(st, "experimental_rerun", None)
        if callable(rerun):
            rerun()
            return
    except Exception:
        pass

    try:
        import time

        st.set_query_params(_auth=int(time.time()))
        st.stop()
    except Exception:
        return

# Petite info sur l'utilisateur connecté
if user:
    st.sidebar.markdown(f"**{user.get('name') or user.get('email')}**")
    if st.sidebar.button("Se déconnecter"):
        logout()

    # Resolve Mongo collections once and reuse
    cols = get_mongo_collections()
    sessions_coll = None
    if cols is not None:
        _client, _db, _users_coll, sessions_coll = cols
    # Also attempt to load and show resolved conf for debugging (mask password)
    try:
        from MiabéIA.auth import db as _auth_db
        conf_local = None
        # try private loader functions if available
        try:
            conf_local = _auth_db._load_mongo_config_from_local()
        except Exception:
            conf_local = None
        conf_env = _auth_db._load_mongo_config_from_env()
        conf_st = _auth_db._load_mongo_config_from_st_secrets()
        resolved = conf_local or conf_st or conf_env
        if resolved and resolved.get('uri'):
            uri = resolved.get('uri')
            # mask credentials for display
            masked = uri
            try:
                import re
                masked = re.sub(r"://(.*?):(.*?)@", "://***:***@", uri)
            except Exception:
                pass
            # st.sidebar.caption(f"Mongo config: {masked}")
    except Exception:
        pass
    # Show DB connection status in sidebar for debugging
    if sessions_coll is not None:
        try:
            # cheap check: list collections (may raise if not connected)
            _ = _db.list_collection_names()
            # st.sidebar.success("MongoDB: connecté")
        except Exception:
            st.sidebar.error("Connexion impossible")
    #else:
        # st.sidebar.warning("MongoDB: non configuré (utilisation locale)")

    # Load user's sessions from MongoDB and display them
    sessions = []
    if sessions_coll is not None:
        try:
            # find sessions for this user (support either user_id or user_email stored in docs)
            user_id = user.get('user_id') if isinstance(user, dict) else None
            user_email = user.get('email') if isinstance(user, dict) else None
            sessions = list(
                sessions_coll.find({"$or": [{"user_id": user_id}, {"user_email": user_email}]})
                .sort([("updated_at", -1)])
                .limit(50)
            )
        except Exception as e:
            st.sidebar.error(f"Impossible de charger vos discussions: {e}")
            sessions = []

    # Sidebar controls (always shown)
    st.sidebar.markdown("---")
    # New discussion button
    if st.sidebar.button("Nouvelle discussion"):
        try:
            # create via helper so session_id and initial messages are set when DB is available
            user_identifier = (user.get('email') if isinstance(user, dict) else None) or (user.get('user_id') if isinstance(user, dict) else None)
            if sessions_coll is not None:
                doc = create_new_session(sessions_coll, user_identifier, "Nouvelle discussion")
                # debug: record created session id
                st.session_state['last_db_session_created'] = doc.get('session_id')
                # st.sidebar.info(f"Session créée en base: {st.session_state.get('last_db_session_created')}")
                # load the new session messages and id
                st.session_state['messages'] = doc.get('messages', [])
                st.session_state['active_session_id'] = doc.get('session_id')
            else:
                # fallback: create a transient local session
                import time
                local_sid = f"local_{int(time.time())}"
                st.session_state['messages'] = []
                st.session_state['active_session_id'] = local_sid
            _do_rerun()
        except Exception as e:
            st.sidebar.error(f"Impossible de créer la discussion: {e}")

    st.sidebar.markdown("### Vos discussions")
    for s in sessions:
            sid = s.get('session_id') or str(s.get('_id'))
            title = s.get('title') or s.get('created_at') or 'Discussion'
            cols = st.sidebar.columns([8, 2])
            with cols[0]:
                if st.sidebar.button(title, key=f"load_{sid}"):
                    # load messages into session_state
                    try:
                        msgs = load_session_messages(sessions_coll, sid)
                    except Exception:
                        msgs = s.get('messages', [])
                    st.session_state['messages'] = msgs
                    # remember active session id
                    st.session_state['active_session_id'] = sid
                    _do_rerun()
            with cols[1]:
                # rename inline
                new_title = st.sidebar.text_input("", value=title, key=f"title_{sid}")
                if st.sidebar.button("Renommer", key=f"rename_{sid}"):
                    try:
                        # update by _id if present, otherwise by session_id
                        if sessions_coll is not None:
                            if s.get('_id'):
                                filter_q = {"_id": s.get('_id')}
                            else:
                                filter_q = {"session_id": s.get('session_id') or sid}
                            sessions_coll.update_one(filter_q, {"$set": {"title": new_title, "updated_at": __import__('datetime').datetime.utcnow().isoformat()}})
                        else:
                            st.sidebar.warning("Impossible de renommer: base de données non configurée")
                        _do_rerun()
                    except Exception as e:
                        st.sidebar.error(f"Erreur renommage: {e}")

# Initialisation de l'historique de la conversation
if "messages" not in st.session_state:
    st.session_state.messages = []

# Affichage des messages de l'historique
for message in st.session_state.messages:
    with st.chat_message(message["role"]):
        st.markdown(message["content"])

def stream_api_response(question: str, history: List[Dict]):
    """Appelle l'API Miabé IA en mode streaming et yield les morceaux de la réponse."""
    payload = {
        "question": question,
        "history": history,
        # optionnel: transmettre l'user_id pour trier/limiter les ressources côté API
        "user_id": user.get("user_id") if isinstance(user, dict) else None,
    }
    
    try:
        with requests.post(API_URL, json=payload, stream=True) as response:
            response.raise_for_status()
            for chunk in response.iter_content(chunk_size=None, decode_unicode=True):
                yield chunk
    except requests.exceptions.RequestException as e:
        yield f"\n[ERREUR] Impossible de contacter l'API : {e}"

# Champ de saisie de l'utilisateur
if prompt := st.chat_input("Posez votre question à Miabé  IA..."):
    # Ajoute et affiche le message de l'utilisateur
    st.session_state.messages.append({"role": "user", "content": prompt})
    # Persist user message to DB if possible. Ensure an active DB session exists first.
    try:
        active_sid = st.session_state.get('active_session_id')
        if sessions_coll is not None:
            # If no active session or it's a local transient, create a DB session first
            if not active_sid or str(active_sid).startswith('local_'):
                user_identifier = (user.get('email') if isinstance(user, dict) else None) or (user.get('user_id') if isinstance(user, dict) else None)
                doc = create_new_session(sessions_coll, user_identifier, "Discussion")
                st.session_state['last_db_session_created'] = doc.get('session_id')
                st.session_state['active_session_id'] = doc.get('session_id')
                active_sid = doc.get('session_id')
                st.sidebar.info(f"Session créée en base: {active_sid}")

            # Save the just-submitted user message into the active DB session
            save_message(sessions_coll, active_sid, 'user', prompt)
            st.session_state['last_db_save'] = (active_sid, 'user')
            st.sidebar.write(f"Dernière sauvegarde DB: message utilisateur -> {active_sid}")
    except Exception as e:
        # non-critical: continue even if persistence fails, but surface a small note
        st.sidebar.warning(f"Echec sauvegarde message utilisateur: {e}")
    with st.chat_message("user"):
        st.markdown(prompt)

    # Prépare et affiche la réponse de l'assistant en streaming
    with st.chat_message("assistant"):
        # Utilisez st.write_stream pour afficher les morceaux (chunks) au fur et à mesure
        # et obtenir la réponse complète à la fin.
        full_response = st.write_stream(
            stream_api_response(prompt, st.session_state.messages[:-1])
        )

        # Append to session state and persist
        st.session_state.messages.append({"role": "assistant", "content": full_response})
        try:
            active_sid = st.session_state.get('active_session_id')
            # If sessions_coll is configured but we somehow don't have an active session,
            # create one so the assistant's reply is persisted as well.
            if sessions_coll is not None and (not active_sid or str(active_sid).startswith('local_')):
                user_identifier = (user.get('email') if isinstance(user, dict) else None) or (user.get('user_id') if isinstance(user, dict) else None)
                doc = create_new_session(sessions_coll, user_identifier, "Discussion")
                st.session_state['last_db_session_created'] = doc.get('session_id')
                st.session_state['active_session_id'] = doc.get('session_id')
                active_sid = doc.get('session_id')
                st.sidebar.info(f"Session créée en base: {active_sid}")

            if sessions_coll is not None and active_sid:
                save_message(sessions_coll, active_sid, 'assistant', full_response)
                st.session_state['last_db_save'] = (active_sid, 'assistant')
                st.sidebar.write(f"Dernière sauvegarde DB: réponse assistant -> {active_sid}")
        except Exception as e:
            st.sidebar.warning(f"Echec sauvegarde réponse assistant: {e}")