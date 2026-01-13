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
    
    # ORANGE (#FFB84D) ~ [255, 184, 77]
    # Filtre large pour ne rien rater
    red_cond = img_array[:, :, 0] > 180
    green_cond = (img_array[:, :, 1] > 100) & (img_array[:, :, 1] < 210)
    blue_cond = img_array[:, :, 2] < 160
    
    mask_orange = red_cond & green_cond & blue_cond
    
    # Remplacement par NOIR (0,0,0) -> Le texte noir devient invisible
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

def detect_week_date_from_image(image, model_list):
    """Lit la date de la semaine (ex: 12/janv)."""
    w, h = image.size
    header_crop = image.crop((0, 0, int(w*0.25), int(h*0.25)))
    
    prompt = """
    Quelle est la date écrite sous le numéro de semaine en haut à gauche (ex: '12/janv') ?
    JSON: {"date_str": "JJ/Mois"}
    """
    
    for model in model_list:
        try:
            resp = call_gemini(header_crop, model, prompt)
            if resp.status_code == 200:
                data = json.loads(clean_json_text(resp.json()['candidates'][0]['content']['parts'][0]['text']))
                if data.get('date_str'): return data['date_str']
        except: continue
    return None

def parse_date_string(date_str):
    try:
        clean = date_str.lower().replace("janv", "01").replace("févr", "02").replace("mars", "03").replace("avr", "04").strip()
        clean = re.sub(r"[^0-9/]", "", clean)
        day, month = clean.split('/')
        return f"2026-{month.zfill(2)}-{day.zfill(2)}"
    except: return None

def extract_schedule(image, model_list, week_start_date):
    prompt = f"""
    Analyse cette page d'emploi du temps (Semaine du {week_start_date}).
    GROUPE CIBLE : "GB".

    RÈGLES D'OR (TRI SÉLECTIF) :
    1. **IGNORER LE HAUT** : Dans une case horaire, s'il y a une séparation horizontale :
       - Le texte du HAUT est pour le Groupe A (GA/G1) -> **NE LE LIS PAS**.
       - Le texte du BAS est pour le Groupe B (GB) -> **GARDE-LE**.
       - Si texte unique centré -> GARDE-LE.
    
    2. **GROUPES INTERDITS** : Si tu lis "/GA", "/GC", "/G1", "Gr A" -> C'EST UNE ERREUR, JETTE-LE.
    
    3. **COULEURS** :
       - Fond NOIR = Cours annulé/Sport -> IGNORE.
       - Fond JAUNE = EXAMEN -> Mets "is_exam": true.

    RÈGLES HORAIRES :
    - Col 1: 07h45-09h45
    - Col 2: 10h00-12h00
    - Col 3: 13h30-15h30
    - Col 4: 15h45-17h45

    FORMAT JSON :
    [
      {{
        "day_index": 0 (Lundi=0...Vendredi=4),
        "summary": "Matière (Prof)",
        "start_time": "HH:MM",
        "end_time": "HH:MM",
        "location": "Salle",
        "group_tag": "GB/GA/AUCUN",
        "position": "BAS/HAUT/UNIQUE",
        "is_exam": true/false
      }}
    ]
    Profs: {PROFS_DICT}
    """
    
    for model in model_list:
        print(f"   👉 Extraction avec {model}...")
        try:
            resp = call_gemini(image, model, prompt)
            if resp.status_code == 200:
                raw = resp.json()
                if 'candidates' in raw:
                    return json.loads(clean_json_text(raw['candidates'][0]['content']['parts'][0]['text']))
            elif resp.status_code in [429, 503]:
                print(f"      ⚠️ Surcharge ({resp.status_code}). Suivant...")
                continue
        except: continue
    return []

def create_ics(events):
    ics = [
        "BEGIN:VCALENDAR",
        "VERSION:2.0",
        "PRODID:-//STRI//Groupe GB//FR",
        "CALSCALE:GREGORIAN",
        "METHOD:PUBLISH"
    ]
    
    for evt in events:
        d = evt['date'].replace('-', '')
        s = evt['start_time'].replace(':', '') + "00"
        e = evt['end_time'].replace(':', '') + "00"
        
        summary = evt['summary']
        prio = "5"
        
        if evt.get('is_exam') or "EXAMEN" in summary.upper():
            if "🔴" not in summary: summary = "🔴 [EXAMEN] " + summary.replace("[EXAMEN]", "").strip()
            prio = "1"
            
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
    prio = [
        "gemini-3-pro-preview", "gemini-3-flash-preview",
        "gemini-2.5-pro", "gemini-2.5-flash",
        "gemini-2.0-flash-001", "gemini-2.0-flash-lite-preview-02-05",
        "gemini-1.5-pro-latest", "gemini-1.5-pro", "gemini-1.5-flash-latest"
    ]
    models = [m for m in prio if m in avail]
    if not models: models = ["gemini-1.5-flash"]
    
    print(f"📋 Modèles : {models}")
    print("Téléchargement PDF...")
    response = requests.get(PDF_URL)
    images = convert_from_bytes(response.content, dpi=300) 

    final_events = []

    print(f"Traitement de {len(images)} pages...")
    for i, img in enumerate(images):
        print(f"--- Page {i+1} ---")
        
        # 1. Noircissement Orange (Sport/Annulé)
        clean_img = preprocess_destructive(img)
        
        # 2. Date réelle
        date_str = detect_week_date_from_image(img, models)
        week_start = parse_date_string(date_str)
        if not week_start:
            start_dt = datetime(2026, 1, 12) + timedelta(days=i*7)
            week_start = start_dt.strftime("%Y-%m-%d")
            
        print(f"   📅 Semaine : {week_start}")
        
        # 3. Extraction
        raw_events = extract_schedule(clean_img, models, week_start)
        week_dt = datetime.strptime(week_start, "%Y-%m-%d")
        
        # 4. Filtrage "Anti-Haut" & "Anti-GA"
        for evt in raw_events:
            summary = evt.get('summary', '').upper()
            pos = evt.get('position', 'UNIQUE').upper()
            grp = evt.get('group_tag', 'AUCUN').upper()
            
            # REJET SI POSITION HAUT (Sauf si GB mentionné)
            if pos == "HAUT" and "GB" not in summary and "GB" not in grp:
                print(f"      🗑️ Rejet (Position HAUT): {summary}")
                continue
                
            # REJET SI GROUPE A EXPLICITE
            if "/GA" in summary or "/GC" in summary or "GA" in grp or "GC" in grp:
                print(f"      🗑️ Rejet (Tag GA/GC): {summary}")
                continue
                
            # REJET SPORT (Sécurité)
            if "SPORT" in summary and "EXAMEN" not in summary: continue

            # Date et Ajout
            day_idx = evt.get('day_index', 0)
            real_date = (week_dt + timedelta(days=day_idx)).strftime("%Y-%m-%d")
            evt['date'] = real_date
            final_events.append(evt)
            
        print(f"   ✅ {len(final_events)} cours valides cumulés.")
        time.sleep(2)

    print("Génération ICS...")
    with open(OUTPUT_FILE, "w", encoding="utf-8") as f:
        f.write(create_ics(final_events))
    print("Terminé.")

if __name__ == "__main__":
    main()
