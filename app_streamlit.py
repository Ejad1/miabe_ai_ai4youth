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
    delete_session,
)

# Import auth UI helper
from auth.ui import require_auth_or_stop, logout

# Configuration de l'API Miabé IA
API_URL = os.getenv("API_URL", "http://127.0.0.1:8000/chat")

# Page title
st.title("Miabé  IA")

# CRITICAL: Always attempt to restore session from cookie on page load/refresh
# This ensures that even when st.session_state is reset (on page refresh),
# the user is automatically logged back in if a valid cookie exists.
# We do this BEFORE checking st.session_state['user'] to handle refresh correctly.
if 'user' not in st.session_state or not st.session_state.get('user'):
    # Try to restore from cookie first
    from auth.ui import _restore_session_from_cookie, _get_users_coll
    users_coll = _get_users_coll()
    if users_coll is not None:
        restored = _restore_session_from_cookie(users_coll)
        if restored:
            st.session_state['user'] = restored

# If a user object exists in session_state (either from previous state or just restored),
# use it; otherwise require authentication.
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
    
    # CRITICAL: Restore active session after page refresh
    # If active_session_id is missing (after refresh), load the most recent discussion
    if sessions_coll is not None and 'active_session_id' not in st.session_state:
        try:
            user_id = user.get('user_id') if isinstance(user, dict) else None
            user_email = user.get('email') if isinstance(user, dict) else None
            
            # Find the most recent session for this user
            most_recent = sessions_coll.find_one(
                {"$or": [{"user_id": user_id}, {"user_email": user_email}]},
                sort=[("updated_at", -1)]
            )
            
            if most_recent:
                # Restore the active session
                st.session_state['active_session_id'] = most_recent.get('session_id')
                st.session_state['messages'] = most_recent.get('messages', [])
        except Exception as e:
            # Non-critical: if restore fails, user can manually select a discussion
            pass
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
    
    # Inject CSS to remove button boxes for icon buttons and align them horizontally
    st.sidebar.markdown("""
    <style>
    /* Remove borders and background for icon buttons (edit/delete) */
    div[data-testid="column"] > div > div > div > button[kind="secondary"] {
        background: transparent !important;
        border: none !important;
        box-shadow: none !important;
        padding: 0.2rem 0.3rem !important;
        font-size: 1.2rem !important;
        color: #aaa !important;
    }
    div[data-testid="column"] > div > div > div > button[kind="secondary"]:hover {
        color: #fff !important;
        background: transparent !important;
    }
    /* Reduce column padding for tight horizontal layout */
    div[data-testid="column"] {
        padding: 0 !important;
    }
    </style>
    """, unsafe_allow_html=True)
    
    for s in sessions:
        sid = s.get('session_id') or str(s.get('_id'))
        title = s.get('title') or s.get('created_at') or 'Discussion'

        # Prepare per-session state keys
        editing_key = f"editing_{sid}"
        confirm_key = f"confirm_delete_{sid}"

        # Columns: title (clickable) | edit | delete
        # Use narrow columns for the icon buttons so they appear on the same line
        # and visually smaller. DO NOT prefix buttons with st.sidebar inside columns.
        cols = st.sidebar.columns([6, 0.7, 0.7])

        with cols[0]:
            if st.button(title, key=f"load_{sid}"):
                # load messages into session_state
                try:
                    msgs = load_session_messages(sessions_coll, sid)
                except Exception:
                    msgs = s.get('messages', [])
                st.session_state['messages'] = msgs
                # remember active session id
                st.session_state['active_session_id'] = sid
                _do_rerun()

        # Edit (pencil) - small icon button on same row
        with cols[1]:
            # Use a compact label for a smaller visual footprint
            if st.button("✎", key=f"edit_btn_{sid}"):
                st.session_state[editing_key] = True

        # Delete (trash)
        with cols[2]:
            # Compact trash icon
            if st.button("🗑", key=f"del_btn_{sid}"):
                st.session_state[confirm_key] = True

        # If editing, show inline rename controls below the row
        if st.session_state.get(editing_key):
            new_title = st.sidebar.text_input("Nom de la discussion", value=title, key=f"title_input_{sid}")
            rename_cols = st.sidebar.columns([1, 1])
            with rename_cols[0]:
                if st.sidebar.button("Enregistrer", key=f"save_title_{sid}"):
                    try:
                        if sessions_coll is not None:
                            if s.get('_id'):
                                filter_q = {"_id": s.get('_id')}
                            else:
                                filter_q = {"session_id": s.get('session_id') or sid}
                            sessions_coll.update_one(filter_q, {"$set": {"title": new_title, "updated_at": __import__('datetime').datetime.utcnow().isoformat()}})
                        else:
                            st.sidebar.warning("Impossible de renommer: base de données non configurée")
                        # exit edit mode and refresh
                        st.session_state[editing_key] = False
                        _do_rerun()
                    except Exception as e:
                        st.sidebar.error(f"Erreur renommage: {e}")
            with rename_cols[1]:
                if st.sidebar.button("Annuler", key=f"cancel_title_{sid}"):
                    st.session_state[editing_key] = False

        # If delete was requested, show confirm UI
        if st.session_state.get(confirm_key):
            confirm_cols = st.sidebar.columns([1, 1])
            with confirm_cols[0]:
                if st.sidebar.button("Confirmer suppression", key=f"confirm_del_{sid}"):
                    try:
                        if sessions_coll is not None:
                            # Attempt to delete by session_id first
                            deleted = delete_session(sessions_coll, s.get('session_id') or sid)
                            if deleted:
                                # If the deleted session was active, clear messages/active id
                                if st.session_state.get('active_session_id') == sid:
                                    st.session_state['messages'] = []
                                    st.session_state['active_session_id'] = None
                                st.sidebar.success("Discussion supprimée")
                            else:
                                st.sidebar.error("Impossible de supprimer la discussion (non trouvée)")
                        else:
                            st.sidebar.warning("Impossible de supprimer: base de données non configurée")
                        # clear confirm flag and rerun to refresh list
                        st.session_state[confirm_key] = False
                        _do_rerun()
                    except Exception as e:
                        st.sidebar.error(f"Erreur suppression: {e}")
            with confirm_cols[1]:
                if st.sidebar.button("Annuler", key=f"cancel_del_{sid}"):
                    st.session_state[confirm_key] = False

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

            # Save the just-submitted user message into the active DB session
            save_message(sessions_coll, active_sid, 'user', prompt)
            st.session_state['last_db_save'] = (active_sid, 'user')
    except Exception as e:
        # Non-critical: continue even if persistence fails
        pass
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

            if sessions_coll is not None and active_sid:
                save_message(sessions_coll, active_sid, 'assistant', full_response)
                st.session_state['last_db_save'] = (active_sid, 'assistant')
        except Exception as e:
            # Non-critical: continue even if persistence fails
            pass