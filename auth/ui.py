"""Streamlit UI helpers for MiabéIA authentication (simplified and up-to-date).

Functions:
- render_login_form(users_coll=None, key='login')
- render_signup_form(users_coll=None, key='signup')
- require_auth_or_stop()
- logout()

Stores the authenticated user in st.session_state['user'].
"""

from __future__ import annotations
from typing import Optional, Dict, Any
import streamlit as st

from .db import get_mongo_collections, ensure_user_indexes
from .logic import register_user, authenticate_user


# --- Utilitaires ---

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


# --- Formulaire d'inscription ---

def render_signup_form(users_coll=None, key: str = "signup") -> Optional[Dict[str, Any]]:
    if users_coll is None:
        users_coll = _get_users_coll()
    # if users_coll is still None, we cannot render the signup form
    if users_coll is None:
        return None

    st.write("### Créer un compte")
    with st.form(key):
        name = st.text_input("Nom complet")
        email = st.text_input("Email")
        password = st.text_input("Mot de passe", type="password")
        submit = st.form_submit_button("S'inscrire")

    if submit:
        ok, msg, user = register_user(users_coll, name, email, password)
        if ok:
            st.success(msg)
            st.session_state["user"] = user
            _do_rerun()
            return user
        else:
            st.error(msg)

    return None


# --- Formulaire de connexion ---

def render_login_form(users_coll=None, key: str = "login") -> Optional[Dict[str, Any]]:
    if users_coll is None:
        users_coll = _get_users_coll()
    if users_coll is None:
        return None

    st.write("### Connexion")
    with st.form(key):
        email = st.text_input("Email")
        password = st.text_input("Mot de passe", type="password")
        submit = st.form_submit_button("Se connecter")

    if submit:
        ok, msg, user = authenticate_user(users_coll, email, password)
        if ok:
            st.success(msg)
            st.session_state["user"] = user
            _do_rerun()
        else:
            st.error(msg)

    return None


# --- Basculement entre les modes login / signup ---

def _render_mode_toggle(current: str = "login") -> str:
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


# --- Déconnexion ---

def logout() -> None:
    """Déconnecte l'utilisateur et recharge la page."""
    if "user" in st.session_state:
        del st.session_state["user"]
    _do_rerun()


# --- Auth obligatoire ---

def require_auth_or_stop() -> Dict[str, Any]:
    """Force la connexion avant de poursuivre l'exécution."""
    if "user" in st.session_state and st.session_state["user"]:
        return st.session_state["user"]

    st.warning("Veuillez vous connecter ou créer un compte pour continuer.")
    mode = _render_mode_toggle(current="login")
    users_coll = _get_users_coll()

    if mode == "login":
        render_login_form(users_coll, key="login_form")
    else:
        render_signup_form(users_coll, key="signup_form")

    st.stop()
    return {}
