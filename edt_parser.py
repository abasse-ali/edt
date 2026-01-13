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

# --- PROMPT "CHIRURGICAL" ---
SYSTEM_PROMPT = f"""
Tu es un expert en extraction de données calendaires.
Aujourd'hui nous sommes le {date_str}.

MISSION :
Extrais l'emploi du temps de l'image pour l'étudiant du groupe "GB".

RÈGLES D'OR DE NETTOYAGE (A RESPECTER SINON ÉCHEC) :
1. **IGNORE** totalement les numéros de page (ex: "Page 1").
2. **IGNORE** les textes qui ne sont QUE des salles (ex: "U3-Amphi", "U3-204", "Salle TP"). Un cours doit avoir un NOM (ex: "Télécoms", "Anglais").
3. **CASE DIVISÉE** : Si une case horaire contient deux lignes séparées par un trait, garde UNIQUEMENT celle du BAS (C'est celle du groupe GB).
4. **FORMATTITRE** : Le titre (summary) doit être PROPRE. 
   - MAUVAIS : "U3-Amphi Télécoms JGT"
   - BON : "Télécoms (Jean-Guy TARTARIN)"
   - Ne mets PAS la salle dans le titre, mets-la dans le champ "location".

FORMAT DE SORTIE JSON STRICT :
[
  {{
    "summary": "Nom du cours nettoyé",
    "location": "Salle",
    "start": "YYYY-MM-DD HH:MM", 
    "end": "YYYY-MM-DD HH:MM"
  }}
]

DATES :
- Trouve la date du Lundi en haut (ex: 12/janv) et déduis l'année.
- Renvoie les dates précises pour chaque cours.
"""

def download_pdf():
    print("Téléchargement du PDF...")
    headers = {
        "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) Chrome/120.0.0.0 Safari/537.36"
    }
    try:
        r = requests.get(PDF_URL, headers=headers, verify=False, timeout=30)
        content_type = r.headers.get('Content-Type', '').lower()
        if 'html' in content_type:
            print("ERREUR: Page HTML reçue. Le site bloque le téléchargement.")
            exit(1)
        with open("edt.pdf", "wb") as f:
            f.write(r.content)
        print("PDF OK.")
    except Exception as e:
        print(f"Erreur DL: {e}")
        exit(1)

def clean_text(text):
    """Nettoie les résidus de texte"""
    if not text: return ""
    # Enlève les espaces multiples
    text = re.sub(r'\s+', ' ', text).strip()
    return text

def is_garbage(event):
    """Détecte si un événement est un déchet"""
    s = event.get('summary', '').strip()
    
    # 1. C'est "Page 1" ?
    if re.search(r'Page\s*\d+', s, re.IGNORECASE):
        return True
    
    # 2. C'est juste une salle ? (ex: "U3-Amphi")
    # Regex : Commence par U chiffre ou Amphi, et fait moins de 15 caractères
    if re.match(r'^(U\d[-\w/]+|Amphi|Salle.*)$', s, re.IGNORECASE) and len(s) < 15:
        return True
        
    # 3. C'est vide ou trop court ?
    if len(s) < 3:
        return True
        
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
            response = model.generate_content([SYSTEM_PROMPT, img])
            text = response.text.strip()
            
            if "```" in text:
                text = text.replace("```json", "").replace("```", "")
            
            try:
                data = json.loads(text)
            except json.JSONDecodeError:
                print("Erreur : L'IA n'a pas renvoyé du JSON valide.")
                continue

            # --- FILTRAGE IMPITOYABLE ---
            valid_page_events = []
            for item in data:
                # Nettoyage basique
                item['summary'] = clean_text(item.get('summary', ''))
                item['location'] = clean_text(item.get('location', ''))
                
                # Test si c'est un déchet
                if is_garbage(item):
                    print(f"   [SUPPRIMÉ] Déchet détecté : {item['summary']}")
                    continue
                
                valid_page_events.append(item)
            
            print(f"   -> {len(valid_page_events)} cours valides conservés.")
            all_events.extend(valid_page_events)
            
        except Exception as e:
            print(f"Erreur IA Page {i+1}: {e}")

    return all_events

def create_ics(events_data):
    cal = Calendar()
    
    # Dédoublonnage (Même heure de début = doublon)
    # On garde celui qui a le titre le plus long (souvent le plus complet)
    unique_events = {}
    for item in events_data:
        key = item['start']
        if key in unique_events:
            current_len = len(unique_events[key].get('summary', ''))
            new_len = len(item.get('summary', ''))
            if new_len > current_len:
                unique_events[key] = item
        else:
            unique_events[key] = item

    print(f"Génération ICS avec {len(unique_events)} événements uniques...")

    for item in unique_events.values():
        try:
            e = Event()
            e.name = item['summary']
            e.location = item['location']
            
            dt_start = datetime.strptime(item['start'], "%Y-%m-%d %H:%M")
            dt_end = datetime.strptime(item['end'], "%Y-%m-%d %H:%M")
            
            e.begin = TZ.localize(dt_start)
            e.end = TZ.localize(dt_end)
            
            cal.events.add(e)
        except Exception as e:
            print(f"Erreur création event: {e}")

    with open("edt.ics", "w") as f:
        f.write(cal.serialize())
    print("Fichier ICS généré.")

if __name__ == "__main__":
    if not API_KEY:
        print("ERREUR: Clé API manquante.")
        exit(1)
    
    download_pdf()
    data = extract_schedule_ai()
    if data:
        create_ics(data)
    else:
        print("Aucun cours trouvé.")
