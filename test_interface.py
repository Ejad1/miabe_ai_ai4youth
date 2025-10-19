import streamlit as st

# Titre principal
st.title(" Exemple d'interface avec formulaire et dashboard")

# --- Initialisation de l'état de la page ---
if "form_submitted" not in st.session_state:
    st.session_state.form_submitted = False

# --- Étape 1 : Formulaire ---
if not st.session_state.form_submitted:
    st.subheader(" Veuillez remplir le formulaire")

    with st.form("user_form"):
        name = st.text_input("Nom complet")
        age = st.number_input("Âge", min_value=1, max_value=120, step=1)
        country = st.selectbox("Pays", ["Togo", "Bénin", "Ghana", "Autre"])

        submitted = st.form_submit_button("Valider")

        if submitted:
            if name.strip() == "":
                st.warning(" Merci d'entrer votre nom.")
            else:
                # Sauvegarde des données dans la session
                st.session_state.name = name
                st.session_state.age = age
                st.session_state.country = country
                st.session_state.form_submitted = True
                print(f"Le st est : { st }")
                st.success(" Formulaire soumis avec succès !")
                st.rerun()  # Recharge la page pour afficher le dashboard

# --- Étape 2 : Dashboard ---
else:
    st.subheader(" Tableau de bord utilisateur")
    st.write(f"**Nom :** {st.session_state.name}")
    st.write(f"**Âge :** {st.session_state.age}")
    st.write(f"**Pays :** {st.session_state.country}")

    st.markdown("---")
    st.metric(label="Année de naissance estimée", value=2025 - st.session_state.age)
    st.bar_chart({"Âge": [st.session_state.age]})

    # Bouton pour réinitialiser
    if st.button(" Refaire le formulaire"):
        for key in ["form_submitted", "name", "age", "country"]:
            st.session_state.pop(key, None)
        st.experimental_rerun()
