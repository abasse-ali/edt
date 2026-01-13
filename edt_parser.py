import os
import requests
import json
import re
import google.generativeai as genai
from pdf2image import convert_from_path
from ics import Calendar, Event
from datetime import datetime
from pytz import timezone

# --- CONFIGURATION ---
PDF_URL = "https://stri.fr/Gestion_STRI/TAV/L3/EDT_STRI1A_L3IRT_TAV.pdf"
API_KEY = os.environ.get("GEMINI_API_KEY")
MY_GROUP = "GB"

# Configuration IA
genai.configure(api_key=API_KEY)

# --- LE CERVEAU (Prompt amélioré) ---
SYSTEM_PROMPT = """
Tu es un assistant expert en extraction de données. Ta mission est de convertir une image d'emploi du temps universitaire en JSON structuré propre.

CONTEXTE :
- C'est un emploi du temps "grille" (Colonnes=Jours, Lignes=Heures).
- L'étudiant appartient au groupe "GB".
- Il y a beaucoup de "bruit" visuel (superposition de textes).

RÈGLES DE TRI (CRITIQUE) :
1. **GROUPE** : Si une case est divisée en deux (haut/bas), le groupe "GB" est souvent en BAS. Si un cours est marqué "GC" explicitement, IGNORE-LE. Si c'est "Commun" ou "Amphi", GARDE-LE.
2. **NETTOYAGE** : 
   - Ne crée JAMAIS d'événement si le texte n'est qu'un nom de salle (ex: "U3-Amphi", "U3-110", "Salle TP"). C'est du bruit.
   - Ne crée JAMAIS d'événement pour le texte "Page 1" ou des numéros de bas de page.
   - Si le texte est éclaté (ex: "Télécoms" sur une ligne, "JGT" sur l'autre), FUSIONNE-LE en un seul titre cohérent : "Télécoms (JGT)".
3. **EXAMENS** : Les cases jaunes sont des examens. Ajoute "EXAM: " au début du titre.

FORMAT JSON ATTENDU :
Renvoie une LISTE d'objets JSON. Rien d'autre.
[
  {
    "summary": "Nom du cours nettoyé (ex: Télécoms (JGT))",
    "location": "Salle (ex: U3-Amphi)",
    "start": "YYYY-MM-DD HH:MM",
    "end": "YYYY-MM-DD HH:MM"
  }
]

DATES :
- Repère la date du Lundi (ex: 12/janv) en haut de l'image.
- Déduis l'année intelligemment (si on est en janvier/février, c'est l'année civile en cours, ex: 2026).
- Calcule la date précise pour chaque cours.
"""

def download_pdf():
    print("Téléchargement du PDF...")
    try:
        r = requests.get(PDF_URL, verify=False, timeout=30)
        with open("edt.pdf", "wb") as f:
            f.write(r.content)
    except Exception as e:
        print(f"Erreur téléchargement: {e}")
        exit(1)

def is_garbage(summary):
    """Filtre de sécurité pour supprimer les déchets que l'IA aurait laissé passer"""
    s = summary.strip().lower()
    # Si c'est vide ou très court
    if len(s) < 3: return True
    # Si c'est littéralement "Page 1"
    if "page" in s and len(s) < 10: return True
    # Si c'est juste un nom de salle (ex: U3-Amphi, U3-203)
    if re.match(r'^(u\d[-\s\w/]+|amphi|salle \w+)$', s): return True
    return False

def extract_schedule_ai():
    print("Conversion PDF -> Images...")
    try:
        images = convert_from_path("edt.pdf")
    except Exception as e:
        print(f"Erreur Poppler: {e}")
        exit(1)

    model = genai.GenerativeModel('gemini-1.5-flash')
    all_events = []

    for i, img in enumerate(images):
        print(f"--- Analyse IA Page {i+1} ---")
        try:
            # Appel API Gemini
            response = model.generate_content([SYSTEM_PROMPT, img])
            text = response.text.strip()
            
            # Nettoyage Markdown
            if text.startswith("```"):
                text = text.split("\n", 1)[1]
                if text.endswith("```"):
                    text = text.rsplit("\n", 1)[0]
            
            data = json.loads(text)
            
            # Filtrage post-IA
            valid_count = 0
            for item in data:
                if not is_garbage(item.get('summary', '')):
                    all_events.append(item)
                    valid_count += 1
            
            print(f"   -> {valid_count} cours valides extraits.")
            
        except Exception as e:
            print(f"Erreur sur la page {i+1}: {e}")

    return all_events

def create_ics(events_data):
    cal = Calendar()
    tz = timezone('Europe/Paris')
    
    # Dictionnaire pour Mapping Profs (Nettoyage final)
    PROFS = {
        "JGT": "Jean-Guy TARTARIN", "AA": "André AOUN", "PT": "Patrice TORGUET",
        "MM": "Mustapha MOJAHID", "AnAn": "Andréi ANDRÉI", "OM": "Olfa MECHI",
        "BA": "Ben A.", "JS": "Jérôme SOKOLOFF"
    }

    for item in events_data:
        try:
            summary = item.get('summary', 'Cours')
            
            # Dernier nettoyage des sigles profs
            for sigle, nom in PROFS.items():
                if f"({sigle})" in summary or f" {sigle} " in summary:
                    summary = summary.replace(sigle, nom).replace("((", "(").replace("))", ")")

            e = Event()
            e.name = summary
            e.location = item.get('location', '')
            
            # Dates
            dt_start = datetime.strptime(item['start'], "%Y-%m-%d %H:%M")
            dt_end = datetime.strptime(item['end'], "%Y-%m-%d %H:%M")
            
            e.begin = tz.localize(dt_start)
            e.end = tz.localize(dt_end)
            
            cal.events.add(e)
        except Exception as e:
            print(f"Event ignoré (données invalides): {item} - {e}")

    with open("edt.ics", "w") as f:
        f.write(cal.serialize())
    print("Fichier ICS généré avec succès.")

if __name__ == "__main__":
    if not API_KEY:
        print("ERREUR: Pas de clé API !")
        exit(1)
    
    download_pdf()
    data = extract_schedule_ai()
    if data:
        create_ics(data)
    else:
        print("Aucun cours trouvé.")
