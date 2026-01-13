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
DEBUG_FILE = "debug.txt"

# Désactiver alertes SSL
urllib3.disable_warnings(urllib3.exceptions.InsecureRequestWarning)

# Config IA
genai.configure(api_key=API_KEY)
safety_settings = {
    HarmCategory.HARM_CATEGORY_HATE_SPEECH: HarmBlockThreshold.BLOCK_NONE,
    HarmCategory.HARM_CATEGORY_HARASSMENT: HarmBlockThreshold.BLOCK_NONE,
    HarmCategory.HARM_CATEGORY_SEXUALLY_EXPLICIT: HarmBlockThreshold.BLOCK_NONE,
    HarmCategory.HARM_CATEGORY_DANGEROUS_CONTENT: HarmBlockThreshold.BLOCK_NONE,
}

# --- FONCTION DE LOG ---
def log(msg):
    print(msg)
    with open(DEBUG_FILE, "a", encoding="utf-8") as f:
        f.write(f"[{datetime.now().strftime('%H:%M:%S')}] {msg}\n")

# --- PROMPT ---
now_str = datetime.now(TZ).strftime("%A %d %B %Y")
SYSTEM_PROMPT = f"""
Nous sommes le {now_str}.
Analyse cette image d'emploi du temps.
Extrais TOUS les cours de la semaine visible pour le groupe "GB".

RÈGLES :
1. Ignore les cours marqués "/GC".
2. Si une case est divisée en deux (haut/bas), GARDE LE BAS.
3. Cases jaunes = EXAMEN.

FORMAT JSON STRICT :
[
  {{
    "summary": "Matière",
    "location": "Salle",
    "start": "YYYY-MM-DD HH:MM", 
    "end": "YYYY-MM-DD HH:MM"
  }}
]
Si aucun cours n'est trouvé, renvoie [].
"""

def download_pdf():
    log("--- 1. TÉLÉCHARGEMENT ---")
    headers = {"User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) Chrome/120.0.0.0 Safari/537.36"}
    try:
        # On essaie d'abord de voir si un fichier local existe (pour bypasser le téléchargement si besoin)
        if os.path.exists("edt_manual.pdf"):
            log("Fichier 'edt_manual.pdf' trouvé localement. Utilisation de ce fichier.")
            os.rename("edt_manual.pdf", "edt.pdf")
            return

        r = requests.get(PDF_URL, headers=headers, verify=False, timeout=30)
        log(f"Code HTTP: {r.status_code}")
        
        # Vérification Magic Number PDF (%PDF)
        if r.content.startswith(b'%PDF'):
            with open("edt.pdf", "wb") as f:
                f.write(r.content)
            log("Fichier PDF valide sauvegardé.")
        else:
            log(f"ERREUR: Le fichier reçu n'est pas un PDF ! Début du contenu : {r.text[:100]}")
            # On force la création d'un faux PDF pour que le script ne plante pas tout de suite
            with open("edt.pdf", "wb") as f: f.write(b"INVALID")
            
    except Exception as e:
        log(f"Exception Download: {e}")

def extract_with_ai():
    log("--- 2. ANALYSE IA ---")
    
    # Vérification du fichier
    if not os.path.exists("edt.pdf"):
        log("Fichier edt.pdf absent.")
        return []
    
    with open("edt.pdf", "rb") as f:
        header = f.read(4)
        if header != b'%PDF':
            log("Le fichier edt.pdf local n'est pas un PDF valide (Protection anti-bot probable).")
            return []

    try:
        images = convert_from_path("edt.pdf")
        log(f"{len(images)} pages converties en images.")
    except Exception as e:
        log(f"Erreur Poppler (Conversion Image): {e}")
        return []

    model = genai.GenerativeModel('gemini-1.5-flash')
    all_events = []

    for i, img in enumerate(images):
        log(f"Traitement Page {i+1}...")
        try:
            response = model.generate_content([SYSTEM_PROMPT, img], safety_settings=safety_settings)
            raw_text = response.text
            log(f"Réponse IA brute (50 premiers cars): {raw_text[:50]}...")
            
            # Nettoyage JSON
            clean_text = raw_text.replace("```json", "").replace("```", "").strip()
            
            try:
                data = json.loads(clean_text)
                log(f"JSON valide trouvé : {len(data)} éléments.")
                all_events.extend(data)
            except json.JSONDecodeError:
                log("ERREUR: L'IA n'a pas renvoyé de JSON valide.")
                log(f"Contenu reçu: {clean_text}")

        except Exception as e:
            log(f"Erreur API Gemini: {e}")

    return all_events

def create_ics(events):
    log(f"--- 3. GÉNÉRATION ICS ({len(events)} events) ---")
    cal = Calendar()
    
    for item in events:
        try:
            e = Event()
            e.name = item.get('summary', 'Cours')
            e.location = item.get('location', '')
            e.begin = TZ.localize(datetime.strptime(item['start'], "%Y-%m-%d %H:%M"))
            e.end = TZ.localize(datetime.strptime(item['end'], "%Y-%m-%d %H:%M"))
            cal.events.add(e)
        except Exception as e:
            log(f"Erreur Event: {e} sur {item}")

    with open("edt.ics", "w") as f:
        f.write(cal.serialize())
    log("Fichier edt.ics écrit.")

if __name__ == "__main__":
    # Reset debug file
    with open(DEBUG_FILE, "w") as f: f.write("--- LOGS DÉMARRAGE ---\n")
    
    if not API_KEY:
        log("ERREUR: API KEY MANQUANTE")
    else:
        download_pdf()
        events = extract_with_ai()
        create_ics(events)
