# Structure de Sortie du Scrapping

Ce document décrit l'organisation des données scrapées pour le projet CampusGPT / Miabé IA.

## Structure des Dossiers

```
scrapped_documents/
├── html_brut/              # Pages HTML brutes téléchargées
├── html_hashes/            # Hash SHA-256 des contenus HTML (fichiers .hash)
├── html_texts/             # Textes extraits des pages HTML
├── converted_html/         # Pages HTML converties en Markdown
├── documents/              # Documents binaires (PDF, DOCX, etc.)
├── document_hashes/        # Hash SHA-256 des documents (fichiers .hash)
├── converted_documents/    # Documents convertis en Markdown
└── metadata.json           # Métadonnées et mapping des fichiers
```

## 🔑 Système de Nommage

### Fichiers avec Hash d'URL

Tous les fichiers sont nommés avec le **hash SHA-256 de leur URL** :

- `abc123def456...` → Hash SHA-256 de `https://univ-lome.tg/page.html`
- `xyz789abc123...` → Hash SHA-256 de `https://univ-lome.tg/document.pdf`

### Fichiers de Hash de Contenu

Les hash de contenu sont également SHA-256 et stockés dans des fichiers séparés :

- `html_hashes/abc123def456....hash` → Contient le SHA-256 du contenu HTML
- `document_hashes/xyz789abc123....hash` → Contient le SHA-256 du document PDF

## Déduplication Intelligente

### Logique en 2 Étapes

#### Étape 1 : Vérification du Contenu
- Calcule le hash SHA-256 du contenu téléchargé
- Cherche si ce hash existe dans `*_hashes/`
- **Si trouvé** → SKIP (contenu déjà scrapé depuis une autre URL)
- **Si non trouvé** → Continue à l'étape 2

#### Étape 2 : Vérification de l'URL

- Calcule le hash SHA-256 de l'URL
- Cherche si un fichier avec ce nom existe
- **Si non trouvé** → Nouveau fichier, sauvegarde
- **Si trouvé** → Compare les hash de contenu
  - **Identique** → SKIP (page inchangée)
  - **Différent** → Supprime l'ancien, sauvegarde le nouveau (mise à jour)

### Avantages

1. Évite les doublons (même contenu, URLs différentes)
2. Détecte les mises à jour (même URL, contenu modifié)
3. Économise l'espace disque
4. Permet le scrapping incrémental
5. Garde toujours la version la plus récente

## metadata.json

Le fichier `metadata.json` contient le mapping entre les noms hashés et les informations originales :

```json
{
  "abc123def456": {
    "original_name": "syllabus_math.pdf",
    "url": "https://univ-lome.tg/docs/syllabus_math.pdf",
    "content_hash": "a1b2c3d4e5f6789...xyz",
    "duplicate_of": null,
    "timestamp": "2025-11-05T10:00:00Z"
  }
}
```

### Champs
- `original_name` : Nom original du fichier
- `url` : URL source complète
- `content_hash` : Hash SHA-256 du contenu
- `duplicate_of` : `null` si original, sinon hash_name du fichier original
- `timestamp` : Date/heure de scrapping (UTC)

## 🎯 Cas d'Usage

### Premier Scrapping
```
URL1 → télécharge → hash contenu → nouveau → sauvegarde tout
```

### Scrapping Incrémental (même URL)
```
URL1 → télécharge → hash contenu → compare avec ancien
  → Si identique → SKIP
  → Si différent → Supprime ancien + Sauvegarde nouveau
```

### Scrapping de Doublon (URL différente, même contenu)
```
URL2 → télécharge → hash contenu → existe déjà → SKIP (ne sauvegarde pas)
```
