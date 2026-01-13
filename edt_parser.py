import os
import requests
import json
import google.generativeai as genai
from pdf2image import convert_from_path
from ics import Calendar, Event
from datetime import datetime
from pytz import timezone

# --- CONFIGURATION ---
PDF_URL = "https://stri.fr/Gestion_STRI/TAV/L3/EDT_STRI1A_L3IRT_TAV.pdf"
API_KEY = os.environ.get("GEMINI_API_KEY")
MY_GROUP = "GB" # Ton groupe cible

# Configuration de l'IA
genai.configure(api_key=API_KEY)

# Le "Cerveau" de l'extraction : Les instructions données à l'IA
SYSTEM_PROMPT = """
Tu es un assistant qui convertit des images d'emploi du temps universitaire en données structurées JSON.

RÈGLES STRICTES D'EXTRACTION :
1. ANALYSE VISUELLE : L'image contient une grille. Les colonnes sont les jours, les lignes sont les heures.
2. GROUPE CIBLE : L'étudiant est dans le groupe "GB".
   - Si une case horaire est divisée en deux (une ligne en haut, une ligne en bas), le groupe GB est souvent la ligne du BAS.
   - Si un cours mentionne explicitement "GC" et pas "GB", IGNORE-LE.
   - Si un cours mentionne "GB" ou est un cours magistral (Amphi) sans groupe, GARDE-LE.
3. DATES : Identifie la date du Lundi affichée en haut (ex: "12/janv") et déduis l'année (probablement 2026 vu le contexte scolaire). Calcule la date exacte pour chaque jour (Lundi, Mardi, etc.).
4. HEURES : Regarde attentivement la position verticale pour déterminer l'heure de début et de fin.
   - Les cours commencent souvent à h00, h15, h30 ou h45.
5. EXAMENS : Les cases sur fond JAUNE sont des examens. Ajoute "EXAM: " au début du titre.

FORMAT DE SORTIE JSON (Liste d'objets) :
[
  {
    "summary": "Nom du cours (ex: Télécoms JGT)",
    "location": "Salle (ex: U3-Amphi)",
    "start": "YYYY-MM-DD HH:MM",
    "end": "YYYY-MM-DD HH:MM"
  }
]

Ne renvoie QUE le JSON brut. Pas de markdown, pas d'explication.
"""

def download_pdf():
    print("Téléchargement du PDF...")
    try:
        r = requests.get(PDF_URL, verify=False, timeout=30)
        with open("edt.pdf", "wb") as f:
            f.write(r.content)
        print("PDF téléchargé.")
    except Exception as e:
        print(f"Erreur téléchargement: {e}")
        exit(1)

def extract_with_ai():
    print("Conversion PDF -> Images...")
    try:
        # Convertit chaque page du PDF en image
        images = convert_from_path("edt.pdf")
    except Exception as e:
        print(f"Erreur critique: Impossible de convertir le PDF. Poppler est-il installé ? {e}")
        exit(1)

    # Modèle Google Gemini 1.5 Flash (Rapide et Gratuit)
    model = genai.GenerativeModel('gemini-1.5-flash')
    
    all_events = []

    for i, img in enumerate(images):
        print(f"--- Analyse IA de la page {i+1} ---")
        try:
            # On envoie le prompt + l'image à l'IA
            response = model.generate_content([SYSTEM_PROMPT, img])
            text = response.text.strip()
            
            # Nettoyage du Markdown json (```json ... ```)
            if text.startswith("```"):
                text = text.split("\n", 1)[1]
                if text.endswith("```"):
                    text = text.rsplit("\n", 1)[0]
            
            # Parsing JSON
            data = json.loads(text)
            print(f"   -> {len(data)} cours trouvés.")
            all_events.extend(data)
            
        except Exception as e:
            print(f"Erreur IA sur la page {i+1}: {e}")
            # print(response.text) # Décommenter pour debug

    return all_events

def generate_ics(events_data):
    cal = Calendar()
    tz = timezone('Europe/Paris')
    
    for item in events_data:
        try:
            e = Event()
            e.name = item.get('summary', 'Cours Inconnu')
            e.location = item.get('location', '')
            
            # Conversion string -> datetime avec fuseau horaire
            start_dt = datetime.strptime(item['start'], "%Y-%m-%d %H:%M")
            end_dt = datetime.strptime(item['end'], "%Y-%m-%d %H:%M")
            
            e.begin = tz.localize(start_dt)
            e.end = tz.localize(end_dt)
            
            cal.events.add(e)
        except Exception as e:
            print(f"Erreur création événement {item}: {e}")

    with open("edt.ics", "w") as f:
        f.write(cal.serialize())
    print("Calendrier edt.ics généré avec succès !")

if __name__ == "__main__":
    if not API_KEY:
        print("ERREUR: La clé API GEMINI_API_KEY est manquante.")
        exit(1)
        
    download_pdf()
    events = extract_with_ai()
    if events:
        generate_ics(events)
    else:
        print("Aucun cours extrait.")
