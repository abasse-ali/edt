import os
import requests
import json
import re
import google.generativeai as genai
from google.generativeai.types import HarmCategory, HarmBlockThreshold
from pdf2image import convert_from_path
from ics import Calendar, Event
from datetime import datetime
from pytz import timezone
import urllib3

# --- CONFIGURATION ---
PDF_URL = "https://stri.fr/Gestion_STRI/TAV/L3/EDT_STRI1A_L3IRT_TAV.pdf"
API_KEY = os.environ.get("GEMINI_API_KEY")
TZ = timezone('Europe/Paris')

# Désactiver alertes SSL
urllib3.disable_warnings(urllib3.exceptions.InsecureRequestWarning)

# Config IA avec SECURITÉ DÉSACTIVÉE (Pour éviter les blocages silencieux)
genai.configure(api_key=API_KEY)
safety_settings = {
    HarmCategory.HARM_CATEGORY_HATE_SPEECH: HarmBlockThreshold.BLOCK_NONE,
    HarmCategory.HARM_CATEGORY_HARASSMENT: HarmBlockThreshold.BLOCK_NONE,
    HarmCategory.HARM_CATEGORY_SEXUALLY_EXPLICIT: HarmBlockThreshold.BLOCK_NONE,
    HarmCategory.HARM_CATEGORY_DANGEROUS_CONTENT: HarmBlockThreshold.BLOCK_NONE,
}

SYSTEM_PROMPT = """
Analyse cette image d'emploi du temps universitaire.
Extrais TOUS les cours visibles pour le groupe "GB".

RÈGLES SIMPLIFIÉES :
1. Ignore les lignes du haut dans les cases divisées (Garde le bas).
2. Ignore les cours marqués "GC".
3. Les cases jaunes sont des EXAMENS.
4. Si tu vois une date (ex: 12/janv), c'est l'année 2026.

FORMAT JSON OBLIGATOIRE :
[
  {
    "summary": "Matière (Prof)",
    "location": "Salle",
    "start": "YYYY-MM-DD HH:MM", 
    "end": "YYYY-MM-DD HH:MM"
  }
]
Ne renvoie RIEN D'AUTRE que le JSON. Si tu ne trouves rien, renvoie [].
"""

def download_pdf():
    print("--- 1. TÉLÉCHARGEMENT ---")
    headers = {
        "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) Chrome/120.0.0.0 Safari/537.36",
        "Referer": "https://stri.fr/"
    }
    try:
        r = requests.get(PDF_URL, headers=headers, verify=False, timeout=30)
        print(f"Code HTTP: {r.status_code}")
        print(f"Type MIME: {r.headers.get('Content-Type')}")
        
        with open("edt.pdf", "wb") as f:
            f.write(r.content)
            
        size = os.path.getsize("edt.pdf")
        print(f"Taille du fichier: {size} octets")
        
        if size < 1000:
            print("ERREUR: Fichier trop petit (probablement une page d'erreur).")
            print("Contenu:", r.text[:200])
            exit(1)
            
    except Exception as e:
        print(f"Erreur DL: {e}")
        exit(1)

def extract_schedule_ai():
    print("--- 2. ANALYSE IA ---")
    try:
        images = convert_from_path("edt.pdf")
        print(f"Nombre de pages converties: {len(images)}")
    except Exception as e:
        print(f"Erreur Poppler: {e}")
        return []

    model = genai.GenerativeModel('gemini-1.5-flash')
    all_events = []

    for i, img in enumerate(images):
        print(f"Analyse Page {i+1}...")
        try:
            # On passe les safety_settings pour éviter le blocage
            response = model.generate_content(
                [SYSTEM_PROMPT, img],
                safety_settings=safety_settings
            )
            
            text = response.text.strip()
            # Debug: Afficher les 100 premiers caractères de la réponse
            print(f"Réponse IA (début): {text[:100]}...")

            if "```" in text:
                text = text.replace("```json", "").replace("```", "")
            
            data = json.loads(text)
            
            # Filtrage basique des déchets
            clean_data = []
            for item in data:
                s = item.get('summary', '')
                # Si le titre est trop court ou ressemble à une page, on jette
                if len(s) > 2 and "Page" not in s:
                    clean_data.append(item)
            
            print(f"   -> {len(clean_data)} cours trouvés.")
            all_events.extend(clean_data)
            
        except Exception as e:
            print(f"Erreur IA Page {i+1}: {e}")
            # Si l'erreur est liée au JSON, on affiche la réponse brute pour comprendre
            if 'response' in locals():
                print("RAW RESPONSE:", response.text)

    return all_events

def create_ics(events_data):
    print("--- 3. GÉNÉRATION ICS ---")
    cal = Calendar()
    count = 0
    
    for item in events_data:
        try:
            e = Event()
            e.name = item.get('summary', 'Cours')
            e.location = item.get('location', '')
            
            dt_start = datetime.strptime(item['start'], "%Y-%m-%d %H:%M")
            dt_end = datetime.strptime(item['end'], "%Y-%m-%d %H:%M")
            
            e.begin = TZ.localize(dt_start)
            e.end = TZ.localize(dt_end)
            
            cal.events.add(e)
            count += 1
        except Exception as e:
            print(f"Erreur event {item.get('summary')}: {e}")

    with open("edt.ics", "w") as f:
        f.write(cal.serialize())
    print(f"Fichier edt.ics créé avec {count} événements.")

if __name__ == "__main__":
    if not API_KEY:
        print("ERREUR CRITIQUE: Clé API manquante.")
        # Fichier vide pour pas casser git
        with open("edt.ics", "w") as f: f.write("BEGIN:VCALENDAR\nVERSION:2.0\nEND:VCALENDAR")
        exit(1)
    
    download_pdf()
    data = extract_schedule_ai()
    create_ics(data)
