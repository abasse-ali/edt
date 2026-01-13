import os
import requests
import json
import re
import google.generativeai as genai
from pdf2image import convert_from_path
from ics import Calendar, Event
from datetime import datetime
from pytz import timezone
import urllib3

# --- CONFIGURATION ---
PDF_URL = "https://stri.fr/Gestion_STRI/TAV/L3/EDT_STRI1A_L3IRT_TAV.pdf"
API_KEY = os.environ.get("GEMINI_API_KEY")
TZ = timezone('Europe/Paris')

# Désactiver les avertissements SSL
urllib3.disable_warnings(urllib3.exceptions.InsecureRequestWarning)

# Configuration IA
genai.configure(api_key=API_KEY)

# Date pour aider l'IA
now = datetime.now(TZ)
date_str = now.strftime("%A %d %B %Y")

SYSTEM_PROMPT = f"""
Tu es un expert en extraction de données. Nous sommes le {date_str}.

MISSION :
Analyse cette image d'emploi du temps pour l'étudiant groupe "GB".

RÈGLES DE NETTOYAGE :
1. **IGNORE** les numéros de page ("Page 1", "1/2").
2. **IGNORE** les textes qui ne sont QUE des salles ("U3-Amphi", "U3-204").
3. **CASE DIVISÉE** : Si une case contient deux lignes séparées par un trait, garde UNIQUEMENT celle du BAS (C'est celle du groupe GB).
4. **FORMATTITRE** : "Nom du cours (Prof)". Ne mets PAS la salle dans le titre.

FORMAT DE SORTIE JSON STRICT :
[
  {{
    "summary": "Nom du cours",
    "location": "Salle",
    "start": "YYYY-MM-DD HH:MM", 
    "end": "YYYY-MM-DD HH:MM"
  }}
]

DATES :
- Repère la date du Lundi en haut (ex: 12/janv).
- Déduis l'année (probablement 2026).
- Renvoie les dates précises (start/end) pour chaque cours.
"""

def download_pdf():
    print("Téléchargement du PDF...")
    headers = {
        "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) Chrome/120.0.0.0 Safari/537.36"
    }
    try:
        r = requests.get(PDF_URL, headers=headers, verify=False, timeout=30)
        if 'html' in r.headers.get('Content-Type', '').lower():
            print("ERREUR: Page HTML reçue au lieu du PDF.")
            exit(1)
        with open("edt.pdf", "wb") as f:
            f.write(r.content)
        print("PDF OK.")
    except Exception as e:
        print(f"Erreur DL: {e}")
        exit(1)

def is_garbage(event):
    s = event.get('summary', '').strip()
    if re.search(r'Page\s*\d+', s, re.IGNORECASE): return True
    if re.match(r'^(U\d[-\w/]+|Amphi|Salle.*)$', s, re.IGNORECASE) and len(s) < 15: return True
    if len(s) < 3: return True
    return False

def extract_schedule_ai():
    print("Conversion PDF -> Images...")
    try:
        images = convert_from_path("edt.pdf")
    except Exception as e:
        print(f"Erreur Poppler: {e}")
        return [] # Retourne liste vide au lieu de planter

    model = genai.GenerativeModel('gemini-1.5-flash')
    all_events = []

    for i, img in enumerate(images):
        print(f"--- Analyse IA Page {i+1} ---")
        try:
            response = model.generate_content([SYSTEM_PROMPT, img])
            text = response.text.strip()
            
            if "```" in text:
                text = text.replace("```json", "").replace("```", "")
            
            try:
                data = json.loads(text)
            except:
                print("   L'IA n'a pas renvoyé de JSON valide.")
                continue

            for item in data:
                if not is_garbage(item):
                    all_events.append(item)
            
        except Exception as e:
            print(f"Erreur IA Page {i+1}: {e}")

    return all_events

def create_ics(events_data):
    cal = Calendar()
    
    # Dédoublonnage
    unique_events = {}
    for item in events_data:
        key = f"{item['start']}_{item['summary'][:5]}" # Clé unique : Heure + début du titre
        if key not in unique_events:
            unique_events[key] = item

    print(f"Génération ICS avec {len(unique_events)} événements...")

    for item in unique_events.values():
        try:
            e = Event()
            e.name = item.get('summary', 'Cours')
            e.location = item.get('location', '')
            dt_start = datetime.strptime(item['start'], "%Y-%m-%d %H:%M")
            dt_end = datetime.strptime(item['end'], "%Y-%m-%d %H:%M")
            e.begin = TZ.localize(dt_start)
            e.end = TZ.localize(dt_end)
            cal.events.add(e)
        except Exception as e:
            print(f"Erreur event: {e}")

    # ON ÉCRIT LE FICHIER MÊME S'IL EST VIDE (Pour éviter l'erreur git add)
    with open("edt.ics", "w") as f:
        f.write(cal.serialize())
    print("Fichier edt.ics généré (Même si vide).")

if __name__ == "__main__":
    if not API_KEY:
        print("ERREUR: Clé API manquante.")
        # On crée un fichier vide pour ne pas casser le workflow
        with open("edt.ics", "w") as f: f.write("BEGIN:VCALENDAR\nVERSION:2.0\nEND:VCALENDAR")
        exit(1)
    
    download_pdf()
    data = extract_schedule_ai()
    
    # ON APPELLE TOUJOURS CREATE_ICS
    create_ics(data)
