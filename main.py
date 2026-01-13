import os
import io
import json
import base64
import requests
import re
import time
import numpy as np
from pdf2image import convert_from_bytes
from PIL import Image, ImageOps
from datetime import datetime, timedelta

# --- CONFIGURATION ---
PDF_URL = "https://stri.fr/Gestion_STRI/TAV/L3/EDT_STRI1A_L3IRT_TAV.pdf"
OUTPUT_FILE = "emploi_du_temps.ics"
API_KEY = os.environ.get("GEMINI_API_KEY")

PROFS_DICT = """
AnAn=Andréi ANDRÉI; AA=André AOUN; AB=Abdelmalek BENZEKRI; AL=Abir LARABA; BC=Bilal CHEBARO; 
BTJ=Boris TIOMELA JOU; CC=Cédric CHAMBAULT; CG=Christine GALY; CT=Cédric TEYSSIE; EG=Eric GONNEAU; 
EL=Emmanuel LAVINAL; FM=Frédéric MOUTIER; GR=Gérard ROUZIES; JGT=Jean-Guy TARTARIN; JS=Jérôme SOKOLOFF; 
KB=Ketty BRAVO; LC=Louisa COT; MCL=Marie-Christine LAGASQUIÉ; MM=MUSTAPHA MOJAHID; OC=Olivier CRIVELLARO; 
OM=Olfa MECHI; PA=Patrick AUSTIN; PhA=Philippe ARGUEL; PIL=Pierre LOTTE; PL=Philippe LATU; PT=Patrice TORGUET; 
RK=Rahim KACIMI; RL=Romain LABORDE; SB=Sonia BADENE; SL=Séverine LALANDE; TD=Thierry DESPRATS; TG=Thierry GAYRAUD.
"""

def get_available_models():
    url = f"https://generativelanguage.googleapis.com/v1beta/models?key={API_KEY}"
    try:
        response = requests.get(url)
        if response.status_code != 200: return []
        return [m['name'].replace('models/', '') for m in response.json().get('models', [])]
    except: return []

def clean_json_text(text):
    text = re.sub(r"```json|```", "", text).strip()
    start = text.find('[')
    end = text.rfind(']')
    if start != -1 and end != -1:
        return text[start:end+1]
    return text

def preprocess_destructive(pil_image):
    """
    Transforme l'ORANGE en NOIR pour masquer le texte 'Sport' ou les cours annulés.
    """
    img_array = np.array(pil_image)
    
    # Détection ORANGE (#FFB84D)
    # R > 180, G entre 100 et 210, B < 160
    red_cond = img_array[:, :, 0] > 180
    green_cond = (img_array[:, :, 1] > 100) & (img_array[:, :, 1] < 210)
    blue_cond = img_array[:, :, 2] < 160
    
    mask_orange = red_cond & green_cond & blue_cond
    
    # Remplacement par NOIR (0,0,0)
    img_array[mask_orange] = [0, 0, 0]
    
    return Image.fromarray(img_array)

def call_gemini(image, model_name, prompt):
    url = f"https://generativelanguage.googleapis.com/v1beta/models/{model_name}:generateContent?key={API_KEY}"
    
    img_byte_arr = io.BytesIO()
    image.save(img_byte_arr, format='JPEG')
    b64_data = base64.b64encode(img_byte_arr.getvalue()).decode('utf-8')

    payload = {
        "contents": [{"parts": [{"text": prompt}, {"inline_data": {"mime_type": "image/jpeg", "data": b64_data}}]}],
        "generationConfig": {"response_mime_type": "application/json"},
        "safetySettings": [
            {"category": "HARM_CATEGORY_HARASSMENT", "threshold": "BLOCK_NONE"},
            {"category": "HARM_CATEGORY_HATE_SPEECH", "threshold": "BLOCK_NONE"},
            {"category": "HARM_CATEGORY_SEXUALLY_EXPLICIT", "threshold": "BLOCK_NONE"},
            {"category": "HARM_CATEGORY_DANGEROUS_CONTENT", "threshold": "BLOCK_NONE"}
        ]
    }
    return requests.post(url, headers={'Content-Type': 'application/json'}, data=json.dumps(payload))

def extract_all_weeks(image, model_list):
    """Extrait TOUTES les semaines présentes sur la page."""
    
    prompt = f"""
    Analyse cette image qui contient PLUSIEURS semaines d'emploi du temps empilées verticalement.
    GROUPE CIBLE : "GB" (Groupe B).
    ANNÉE : 2026.

    TACHE : Repère chaque bloc semaine (identifié par une date à gauche, ex: "12/janv", "19/janv", "26/janv", "02/févr").
    Pour CHAQUE semaine trouvée, extrais les cours du groupe GB.

    RÈGLES DE FILTRAGE :
    1. **POSITION (HAUT/BAS)** : Dans une case avec deux matières superposées :
       - Celle du HAUT est pour le Groupe A (GA/GC) -> **IGNORE-LA**.
       - Celle du BAS est pour le Groupe B (GB) -> **GARDE-LA**.
       - Texte centré -> GARDE.
    2. **COULEUR** :
       - Zones NOIRES (anciennement orange) -> **IGNORE** (Cours annulés/Sport).
       - Zones JAUNES -> **EXAMEN** (mets "is_exam": true).
    
    RÈGLES HORAIRES :
    - Col 1: 07h45-09h45
    - Col 2: 10h00-12h00
    - Col 3: 13h30-15h30
    - Col 4: 15h45-17h45

    FORMAT JSON :
    [
      {{
        "week_label": "12/janv", (La date écrite à gauche de la ligne correspondante)
        "day": "Lundi", (ou Mardi, Mercredi...)
        "summary": "Matière (Prof)",
        "start_time": "HH:MM",
        "end_time": "HH:MM",
        "location": "Salle",
        "is_exam": true/false
      }}
    ]
    Profs: {PROFS_DICT}
    """
    
    for model in model_list:
        print(f"   👉 Tentative lecture globale avec {model}...")
        try:
            resp = call_gemini(image, model, prompt)
            if resp.status_code == 200:
                raw = resp.json()
                if 'candidates' in raw and raw['candidates']:
                    txt = clean_json_text(raw['candidates'][0]['content']['parts'][0]['text'])
                    return json.loads(txt)
            elif resp.status_code in [429, 503]:
                print(f"      ⚠️ Surcharge ({resp.status_code}). Suivant...")
                continue
        except Exception as e:
            print(f"      ❌ Erreur: {e}")
            continue
    return []

def calculate_date(week_label, day_name):
    """Calcule la date précise : 2026 + mois/jour du label + jour de la semaine."""
    try:
        # Nettoyage label semaine (ex: "12/janv" -> "12/01")
        lbl = week_label.lower().replace("janv", "01").replace("févr", "02").replace("mars", "03").replace("avr", "04")
        lbl = re.sub(r"[^0-9/]", "", lbl)
        
        # On suppose année 2026
        day_str, month_str = lbl.split('/')
        week_start = datetime(2026, int(month_str), int(day_str))
        
        # Offset jour
        days = ["Lundi", "Mardi", "Mercredi", "Jeudi", "Vendredi"]
        day_offset = 0
        for i, d in enumerate(days):
            if d.lower() in day_name.lower():
                day_offset = i
                break
                
        final_date = week_start + timedelta(days=day_offset)
        return final_date.strftime("%Y%m%d")
    except:
        return None

def create_ics(events):
    ics = [
        "BEGIN:VCALENDAR",
        "VERSION:2.0",
        "PRODID:-//STRI//Groupe GB//FR",
        "CALSCALE:GREGORIAN",
        "METHOD:PUBLISH"
    ]
    
    for evt in events:
        # Calcul date
        d = calculate_date(evt.get('week_label', '01/01'), evt.get('day', 'Lundi'))
        if not d: continue
        
        s = evt['start_time'].replace(':', '') + "00"
        e = evt['end_time'].replace(':', '') + "00"
        
        summary = evt.get('summary', 'Cours')
        prio = "5"
        
        # Gestion Examen
        if evt.get('is_exam') or "EXAMEN" in summary.upper():
            if "🔴" not in summary:
                summary = "🔴 [EXAMEN] " + summary.replace("[EXAMEN]", "").strip()
            prio = "1"
            
        # Filtrage ultime (sécurité)
        if "SPORT" in summary.upper() and prio != "1": continue
        if "/GA" in summary.upper() or "/GC" in summary.upper(): continue

        ics.append("BEGIN:VEVENT")
        ics.append(f"DTSTART:{d}T{s}")
        ics.append(f"DTEND:{d}T{e}")
        ics.append(f"SUMMARY:{summary}")
        ics.append(f"LOCATION:{evt.get('location', '')}")
        ics.append(f"PRIORITY:{prio}")
        ics.append("DESCRIPTION:Groupe GB")
        ics.append("END:VEVENT")
        
    ics.append("END:VCALENDAR")
    return "\n".join(ics)

def main():
    if not API_KEY: raise Exception("Clé API manquante")

    avail = get_available_models()
    # Liste fournie par l'utilisateur
    prio = [
        "gemini-3-pro-preview", "gemini-3-flash-preview",
        "gemini-2.5-pro", "gemini-2.5-flash",
        "gemini-2.0-flash-001", "gemini-2.5-flash-lite", "gemini-2.0-flash-lite-preview-02-05",
        "gemini-1.5-pro-latest", "gemini-1.5-pro", "gemini-1.5-flash-latest", "gemini-1.5-flash", "gemini-1.5-flash-8b"
    ]
    models = [m for m in prio if m in avail]
    if not models: models = ["gemini-1.5-flash"]
    
    print(f"📋 Modèles actifs : {models}")

    print("Téléchargement PDF...")
    response = requests.get(PDF_URL)
    images = convert_from_bytes(response.content, dpi=300) 

    all_events = []

    print(f"Traitement de {len(images)} pages...")
    for i, img in enumerate(images):
        print(f"--- Page {i+1} ---")
        
        # 1. Nettoyage Visuel (Masquage Orange en Noir)
        clean_img = preprocess_destructive(img)
        
        # 2. Extraction Multi-Semaines
        page_events = extract_all_weeks(clean_img, models)
        
        if page_events:
            print(f"   ✅ {len(page_events)} cours trouvés sur cette page.")
            all_events.extend(page_events)
        else:
            print("   ❌ Aucun cours extrait.")
            
        time.sleep(2)

    print("Génération ICS...")
    with open(OUTPUT_FILE, "w", encoding="utf-8") as f:
        f.write(create_ics(all_events))
    print("Terminé.")

if __name__ == "__main__":
    main()
