"""Streamlit UI helpers for MiabéIA authentication (simplified and up-to-date).

Handles user session persistence using browser cookies.

Functions:
- render_login_form(...)
- render_signup_form(...)
- require_auth_or_stop()
- logout()

Stores the authenticated user in st.session_state['user'].

IMPORTANT: Uses extra-streamlit-components for reliable cookie management.
Install: pip install extra-streamlit-components
"""

from __future__ import annotations
from typing import Optional, Dict, Any
import os
import streamlit as st

# Use extra-streamlit-components for RELIABLE cookie management
try:
    import extra_streamlit_components as stx
    COOKIES_AVAILABLE = True
except ImportError:
    COOKIES_AVAILABLE = False
    st.warning("⚠️ Pour la persistance de session, installez: pip install extra-streamlit-components")

from .db import get_mongo_collections, ensure_user_indexes
# LES FONCTIONS DE LOGIC SONT MAINTENANT IMPLÉMENTÉES ET UTILISENT users_coll
from .logic import register_user, authenticate_user, generate_session_token, validate_session_token, delete_session_token 
from .models import StudentUser, AffiliateUser
from pymongo.errors import DuplicateKeyError


# --- Constantes & Utilitaires ---

SESSION_COOKIE_NAME = "miabeia_auth_token"
COOKIE_EXPIRY_DAYS = 1/12  # 2 heures (ajustez selon besoin: 1=24h, 7=1 semaine)

def _do_rerun() -> None:
    """Recharge proprement l'application Streamlit."""
    try:
        st.rerun()
    except Exception:
        st.stop()


def _get_users_coll():
    """Récupère la collection des utilisateurs MongoDB."""
    cols = get_mongo_collections()
    if not cols:
        st.error("MongoDB non configuré : impossible d'accéder à la collection utilisateurs.")
        return None

    _, _, users_coll, _ = cols
    try:
        ensure_user_indexes(users_coll)
    except Exception:
        pass
    return users_coll


# --- Gestion du Token (PRODUCTION - Session ID dans URL + MongoDB) ---

import uuid
from datetime import datetime, timedelta

def save_auth_token(token: str) -> None:
    """Sauvegarde le token avec persistance via MongoDB + session ID dans URL.
    
    Architecture sécurisée :
    - Génère un ID de session unique
    - Stocke {session_id → token} dans MongoDB
    - Met l'ID (pas le token) dans l'URL
    """
    from .db import get_mongo_collections
    
    # Sauvegarder dans session_state pour accès immédiat
    st.session_state['_miabeia_auth_token'] = token
    
    # Créer un ID de session unique (UUID court)
    session_id = str(uuid.uuid4())[:8]  # 8 caractères suffisent
    st.session_state['_session_id'] = session_id
    
    # Stocker dans MongoDB
    cols = get_mongo_collections()
    if cols:
        db = cols[3].database  # Utilise la DB du dernier élément du tuple
        persistent_coll = db['persistent_sessions']
        
        # Insérer avec expiration
        persistent_coll.insert_one({
            'session_id': session_id,
            'token': token,
            'created_at': datetime.utcnow(),
            'expires_at': datetime.utcnow() + timedelta(hours=2)
        })
        
        # Créer index TTL pour auto-expiration (si pas déjà créé)
        try:
            persistent_coll.create_index('expires_at', expireAfterSeconds=0)
        except:
            pass
        
        # Mettre l'ID de session dans l'URL (PAS le token complet)
        st.query_params['sid'] = session_id


def load_auth_token() -> Optional[str]:
    """Charge le token depuis session_state ou MongoDB via session ID dans URL."""
    from .db import get_mongo_collections
    
    # 1. Vérifier session_state (le plus rapide)
    token = st.session_state.get('_miabeia_auth_token')
    if token:
        return token
    
    # 2. Lire l'ID de session depuis l'URL
    session_id = st.query_params.get('sid')
    
    if session_id:
        # 3. Récupérer le token depuis MongoDB
        cols = get_mongo_collections()
        if cols:
            db = cols[3].database
            persistent_coll = db['persistent_sessions']
            
            session_doc = persistent_coll.find_one({
                'session_id': session_id,
                'expires_at': {'$gt': datetime.utcnow()}  # Non expiré
            })
            
            if session_doc:
                token = session_doc['token']
                st.session_state['_miabeia_auth_token'] = token
                st.session_state['_session_id'] = session_id
                return token
            else:
                # Session expirée ou invalide - nettoyer l'URL
                try:
                    del st.query_params['sid']
                except:
                    pass
    
    return None


def delete_auth_token() -> None:
    """Supprime le token (session_state + URL + MongoDB)."""
    from .db import get_mongo_collections
    
    # Récupérer l'ID de session avant de le supprimer
    session_id = st.session_state.get('_session_id')
    
    # Supprimer de session_state
    st.session_state.pop('_miabeia_auth_token', None)
    st.session_state.pop('_session_id', None)
    
    # Supprimer de MongoDB
    if session_id:
        cols = get_mongo_collections()
        if cols:
            db = cols[3].database
            persistent_coll = db['persistent_sessions']
            persistent_coll.delete_one({'session_id': session_id})
    
    # Supprimer de l'URL
    try:
        del st.query_params['sid']
    except:
        pass


# --- Logique d'Inscription (Mise à Jour pour le Token) ---
def _handle_signup_submission(users_coll, model_instance: StudentUser | AffiliateUser) -> Optional[Dict[str, Any]]:
    """Logique commune de soumission d'inscription et de création de token."""
    try:
        # 1. Préparation du document
        doc = model_instance.to_db()

        # 2. Insertion du document initial (pour avoir un _id)
        result = users_coll.insert_one(doc)
        inserted_id = result.inserted_id

        # 3. Création du jeton de session et mise à jour de la BD (PASSE users_coll)
        session_token = generate_session_token(users_coll, inserted_id)

        # 4. Préparation de l'utilisateur public
        # Mettre à jour le document public avec le _id retourné par la base
        doc['_id'] = inserted_id
        user_public = {k: v for k, v in doc.items() if k != 'password_hash'}
        st.session_state["user"] = user_public

        # 5. SAUVEGARDE DU TOKEN (session_state + tentative cookie)
        save_auth_token(session_token)

        st.success("Compte créé avec succès et connecté.")
        _do_rerun()
        return user_public

    except DuplicateKeyError:
        st.error("Cet email est déjà enregistré.")
    except Exception as e:
        st.error(f"Erreur lors de la création du compte: {e}")

    return None


# --- Logique de Connexion (Mise à Jour pour le Token) ---

def _handle_login_success(users_coll, user_doc: Dict[str, Any]) -> None:
    """Logique après authentification réussie: création de token et sauvegarde."""
    
    # 1. Génération et mise à jour du jeton de session (PASSE users_coll)
    session_token = generate_session_token(users_coll, user_doc['_id'])
    
    # 2. Préparation de l'utilisateur public (sans hash de mot de passe ni token)
    user_public = {k: v for k, v in user_doc.items() if k not in ['password_hash', 'session_token', 'token_expiry']}
    
    # 3. Mise à jour de la session Streamlit
    st.session_state["user"] = user_public
    
    # 4. SAUVEGARDE DU TOKEN (session_state + tentative cookie)
    save_auth_token(session_token)
    
    st.success("Connexion réussie.")
    _do_rerun()


def render_login_form(users_coll=None, key: str = "login") -> Optional[Dict[str, Any]]:
    if users_coll is None:
        users_coll = _get_users_coll()
    if users_coll is None:
        return None
    # Improved layout without st.form to avoid the "Press Enter to submit form" hint
    st.markdown("""<div style='display:flex;align-items:center;gap:12px'>
                    <h2 style='margin:0'>Connexion</h2>
                  </div>""", unsafe_allow_html=True)

    """
    left, right = st.columns([1, 2])
    with left:
        # optional illustration (commented out by default)
        st.image('https://placehold.co/220x140?text=Miab%C3%A9+IA', use_column_width=True)
        st.write("Bienvenue — connectez-vous pour continuer.")

    with right:
    """
    email = st.text_input("Email", placeholder="votre@exemple.tld", key=f"{key}_email")
    password = st.text_input("Mot de passe", type="password", key=f"{key}_password")
    
    if st.button("Se connecter", key=f"{key}_submit"):
        # validate required fields at submit time
        if (not email) or (not password):
            st.error("Veuillez renseigner l'email et le mot de passe.")
        else:
            with st.spinner("Vérification..."):
                ok, msg, user_doc = authenticate_user(users_coll, email, password)
                if ok:
                    _handle_login_success(users_coll, user_doc)
                else:
                    st.error(msg)

    return None


def render_signup_form(users_coll=None, key: str = "signup") -> Optional[Dict[str, Any]]:
    # ... (Le code render_signup_form reste le même, il appelle _handle_signup_submission)
    if users_coll is None:
        users_coll = _get_users_coll()
    if users_coll is None:
        return None
    st.markdown("""<div style='display:flex;align-items:center;gap:12px'>
                    <h2 style='margin:0'>Créer un compte</h2>
                  </div>""", unsafe_allow_html=True)

    tabs = st.tabs(["Étudiant", "Affilié"])
    user_data = None

    # --- Étudiant ---
    with tabs[0]:
        st.markdown("##### Informations générales")
        nom = st.text_input("Nom", key=f"{key}_et_nom", placeholder="Nom de famille")
        prenoms = st.text_input("Prénoms", key=f"{key}_et_prenoms")
        email = st.text_input("Email", key=f"{key}_et_email")
        contact = st.text_input("Contact (téléphone)", key=f"{key}_et_contact")
        password = st.text_input("Mot de passe", type="password", key=f"{key}_et_password")

        st.markdown("##### Détails Étudiant")
        domaine = st.text_input("Domaine (optionnel)", key=f"{key}_et_domaine")
        filiere = st.text_input("Filière", key=f"{key}_et_filiere")
        grade = st.selectbox("Grade", ["licence", "master", "doctorat"], key=f"{key}_et_grade")
        annee_etudes = st.text_input("Année d'études (optionnel)", key=f"{key}_et_annee")

        submit_disabled = not (nom and prenoms and email and password and filiere and grade)
        if st.button("S'inscrire comme Étudiant", key=f"{key}_et_submit"):
            if not (nom and prenoms and email and password and filiere and grade):
                st.error("Veuillez remplir tous les champs obligatoires pour l'inscription de l'étudiant.")
            elif not filiere or not grade:
                st.error("Les champs 'Filière' et 'Grade' sont obligatoires pour un étudiant.")
            else:
                model = StudentUser(
                    nom=nom,
                    prenoms=prenoms,
                    password=password,
                    email=email,
                    contact=contact,
                    domaine=domaine or None,
                    filiere=filiere,
                    grade=grade,
                    annee_etudes=annee_etudes or None,
                )
                user_data = _handle_signup_submission(users_coll, model)

    # --- Affilié ---
    with tabs[1]:
        st.markdown("##### Informations générales")
        nom = st.text_input("Nom", key=f"{key}_af_nom")
        prenoms = st.text_input("Prénoms", key=f"{key}_af_prenoms")
        email = st.text_input("Email", key=f"{key}_af_email")
        contact = st.text_input("Contact (téléphone)", key=f"{key}_af_contact")
        password = st.text_input("Mot de passe", type="password", key=f"{key}_af_password")

        st.markdown("##### Détails Affiliation")
        affiliation_ul = st.checkbox("Affilié à l'Université de Lomé", value=True, key=f"{key}_af_ul")
        affiliation_details = st.text_input("Détails d'affiliation (ex: département)", key=f"{key}_af_details")

        if st.button("S'inscrire comme Affilié", key=f"{key}_af_submit"):
            if not (nom and prenoms and email and password):
                st.error("Veuillez remplir tous les champs obligatoires pour l'inscription affilié.")
            else:
                model = AffiliateUser(
                    nom=nom,
                    prenoms=prenoms,
                    password=password,
                    email=email,
                    contact=contact,
                    affiliation_ul=affiliation_ul,
                    affiliation_details=affiliation_details or None,
                )
                user_data = _handle_signup_submission(users_coll, model)

    return user_data

# --- Déconnexion (Mise à Jour pour le Token) ---

def logout() -> None:
    """Déconnecte l'utilisateur, supprime la session Streamlit et le cookie."""
    users_coll = _get_users_coll()
    
    if users_coll is not None and "user" in st.session_state and st.session_state["user"]:
        # 1. SUPPRESSION DU TOKEN EN BASE (on utilise l'email pour le retrouver si nécessaire)
        # Note: Le token n'est pas stocké dans st.session_state["user"] pour des raisons de sécurité, 
        # on doit le récupérer du cookie pour le supprimer en base.
        token = load_auth_token()
        if token:
            delete_session_token(users_coll, token)
            
        del st.session_state["user"]
    
    # 2. SUPPRESSION DU COOKIE
    delete_auth_token() 
    
    _do_rerun()


# --- Auth obligatoire (Mise à Jour Critique) ---

def _restore_session_from_cookie(users_coll) -> Optional[Dict[str, Any]]:
    """Tente de restaurer la session si un token valide existe (cookie ou session_state)."""
    
    token = load_auth_token()  # Will check session_state first, then cookie
    
    if token:
        # 1. VALIDE LE TOKEN EN BASE (PASSE users_coll)
        user = validate_session_token(users_coll, token)
        if user:
            # 2. Restaure l'état de session Streamlit
            # On retire les champs sensibles du document récupéré de la BD
            user_public = {k: v for k, v in user.items() if k not in ['password_hash', 'session_token', 'token_expiry']}
            st.session_state["user"] = user_public
            
            return user_public
        else:
            # Token invalide ou expiré: on nettoie tout
            delete_auth_token()
            st.session_state.pop('_miabeia_auth_token_backup', None)
            
    return None

def require_auth_or_stop() -> Dict[str, Any]:
    """Force la connexion ou restaure la session via cookie."""
    if "user" in st.session_state and st.session_state["user"]:
        return st.session_state["user"]

    users_coll = _get_users_coll()
    if users_coll is None:
        st.stop()
        return {}
    # --- ÉTAPE 1: TENTER LA RESTAURATION VIA COOKIE ---
    restored_user = _restore_session_from_cookie(users_coll)
    if restored_user:
        return restored_user
    
    # --- ÉTAPE 2: AFFICHER LES FORMULAIRES DE CONNEXION/INSCRIPTION ---
    st.warning("Veuillez vous connecter ou créer un compte pour continuer.")
    mode = _render_mode_toggle(current="login")

    if mode == "login":
        render_login_form(users_coll, key="login_form")
    else:
        render_signup_form(users_coll, key="signup_form")

    st.stop()
    return {}

# --- Basculement entre les modes login / signup ---

def _render_mode_toggle(current: str = "login") -> str:
    # ... (Le code _render_mode_toggle reste inchangé)
    if "auth_mode" not in st.session_state:
        st.session_state["auth_mode"] = current

    col1, col2 = st.columns(2)
    with col1:
        if st.button("Se connecter"):
            st.session_state["auth_mode"] = "login"
            _do_rerun()
    with col2:
        if st.button("Créer un compte"):
            st.session_state["auth_mode"] = "signup"
            _do_rerun()

    return st.session_state["auth_mode"]
