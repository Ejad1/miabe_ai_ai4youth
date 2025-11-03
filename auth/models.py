"""Schémas utilisateur pour MiabéIA.auth

Ce module fournit deux modèles Pydantic légers pour valider et sérialiser
les utilisateurs avant insertion en base MongoDB :
- StudentUser : étudiant(e)
- AffiliateUser : visiteur (personnel, enseignant, externe, etc.)

Chaque modèle propose une méthode `to_db()` qui renvoie un dict prêt à être
inséré dans la collection `users` en respectant quelques conventions du projet
(champs `user_id`, `name`, `email` normalisé, `created_at`, `role`, ...).
"""
from __future__ import annotations

from typing import Optional, Literal, Dict, Any
from datetime import datetime
import uuid

from pydantic import BaseModel, EmailStr, Field, validator


def _now_iso() -> str:
    return datetime.utcnow().isoformat() + 'Z'


class StudentUser(BaseModel):
    """Modèle pour un utilisateur étudiant.

    Champs obligatoires : nom, prenoms, email, contact, filiere, grade
    Champs optionnels : domaine, annee_etudes

    Exemples de grade acceptés : 'licence', 'master', 'doctorat'
    """

    nom: str
    prenoms: str
    # L'utilisateur fournit un mot de passe en clair ('password').
    # Le modèle ne reçoit jamais de hash en entrée : to_db() hachera le mot de passe.
    password: str
    email: EmailStr
    contact: str
    domaine: Optional[str] = None
    filiere: str
    grade: Literal['licence', 'master', 'doctorat']
    annee_etudes: Optional[str] = Field(None, alias='annee_etudes')

    @validator('nom', 'prenoms', 'contact', 'domaine', 'filiere', pre=True, always=True)
    def _strip_strings(cls, v):
        if v is None:
            return v
        return str(v).strip()

    @validator('email', pre=True, always=True)
    def _normalize_email(cls, v):
        return str(v).strip().lower()

    @validator('password', pre=True, always=True)
    def _validate_password(cls, v):
        if v is None:
            raise ValueError("'password' est requis")
        s = str(v).strip()
        if not s:
            raise ValueError("'password' ne peut pas être vide")
        return s

    def to_db(self, user_id: Optional[str] = None) -> Dict[str, Any]:
        """Retourne un dict prêt pour insertion dans la collection users.

        Ne gère pas le champ `password_hash` (la création de compte ou l'enregistrement
        du mot de passe doit se faire via la logique existante dans `logic.py`).
        """
        uid = user_id or str(uuid.uuid4())
        name_combined = f"{self.nom} {self.prenoms}".strip()
        doc = {
            "user_id": uid,
            "role": "student",
            "name": name_combined,
            "email": str(self.email),
            "contact": self.contact,
            "nom": self.nom,
            "prenoms": self.prenoms,
            "domaine": self.domaine,
            "filiere": self.filiere,
            "grade": self.grade,
            "annee_etudes": self.annee_etudes,
        }

        # hacher le mot de passe fourni et l'inclure dans le document retourné
        try:
            from .logic import _hash_password
            final_hash = _hash_password(self.password)
        except Exception:
            import bcrypt
            final_hash = bcrypt.hashpw(self.password.encode('utf-8'), bcrypt.gensalt()).decode('utf-8')

        doc.update({
            "password_hash": final_hash,
            "created_at": _now_iso(),
            "status": "active",
        })
        return doc


class AffiliateUser(BaseModel):
    """Modèle pour un utilisateur visiteur.

    Inclut : personnel UL, enseignants, chercheurs, partenaires externes, etc.
    Champs : nom, prenoms, affiliation_ul (bool), affiliation_details, email, contact
    """

    nom: str
    prenoms: str
    affiliation_ul: bool = True
    affiliation_details: Optional[str] = None
    password: str
    email: EmailStr
    contact: str

    @validator('nom', 'prenoms', 'contact', 'affiliation_details', pre=True, always=True)
    def _strip_strings(cls, v):
        if v is None:
            return v
        return str(v).strip()

    @validator('email', pre=True, always=True)
    def _normalize_email(cls, v):
        return str(v).strip().lower()

    @validator('password', pre=True, always=True)
    def _validate_password_aff(cls, v):
        if v is None:
            raise ValueError("'password' est requis")
        s = str(v).strip()
        if not s:
            raise ValueError("'password' ne peut pas être vide")
        return s

    def to_db(self, user_id: Optional[str] = None) -> Dict[str, Any]:
        uid = user_id or str(uuid.uuid4())
        name_combined = f"{self.nom} {self.prenoms}".strip()
        doc = {
            "user_id": uid,
            "role": "affiliate",
            "name": name_combined,
            "email": str(self.email),
            "contact": self.contact,
            "nom": self.nom,
            "prenoms": self.prenoms,
            "affiliation_ul": bool(self.affiliation_ul),
            "affiliation_details": self.affiliation_details,
        }

        try:
            from .logic import _hash_password
            final_hash = _hash_password(self.password)
        except Exception:
            import bcrypt
            final_hash = bcrypt.hashpw(self.password.encode('utf-8'), bcrypt.gensalt()).decode('utf-8')

        doc.update({
            "password_hash": final_hash,
            "created_at": _now_iso(),
            "status": "active",
        })
        return doc
