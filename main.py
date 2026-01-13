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
    """Récupère les modèles dispos."""
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
    Transforme l'ORANGE en NOIR pour effacer le texte 'Sport'.
    """
    img_array = np.array(pil_image)
    
    # ORANGE (#FFB84D) ~ [255, 184, 77]
    # Tolérance élargie pour être sûr de tout attraper
    # Rouge Haut, Bleu Bas, Vert Moyen (c'est la signature de l'orange)
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
    """Demande à l'IA de lire la date écrite en haut à gauche (ex: 12/janv)."""
    # On crop le coin haut gauche pour aider l'IA
    w, h = image.size
    header_crop = image.crop((0, 0, int(w*0.3), int(h*0.3)))
    
    prompt = """
    Regarde cette image. Quelle est la date écrite sous le numéro de semaine (ex: '12/janv' ou '19/janv') ?
    Format attendu JSON : {"date_str": "JJ/Mois"}
    Si tu ne trouves pas, renvoie null.
    """
    
    for model in model_list:
        try:
            resp = call_gemini(header_crop, model, prompt)
            if resp.status_code == 200:
                data = json.loads(clean_json_text(resp.json()['candidates'][0]['content']['parts'][0]['text']))
                if data.get('date_str'):
                    return data['date_str']
        except: continue
    return None

def parse_date_string(date_str):
    """Convertit '12/janv' en '2026-01-12'."""
    try:
        # Nettoyage
        clean = date_str.lower().replace("janv", "01").replace("févr", "02").replace("mars", "03").strip()
        clean = re.sub(r"[^0-9/]", "", clean) # Garde chiffres et slash
        
        day, month = clean.split('/')
        return f"2026-{month.zfill(2)}-{day.zfill(2)}"
    except:
        return None

def extract_schedule(image, model_list, week_start_date):
    """Extrait l'emploi du temps complet de la page."""
    
    prompt = f"""
    Analyse cet emploi du temps pour la semaine du {week_start_date}.
    GROUPE CIBLE : "GB" (Groupe B).

    RÈGLES DE FILTRAGE STRICTES :
    1. **IGNORER** tous les cours marqués "/GA", "/GC" ou "Groupe A/C".
    2. **IGNORER** les cours de "Sport" (souvent sur fond orange, qui apparait noir/bizarre ici).
    3. **POSITION** : Si une case contient deux lignes de texte (Haut/Bas), celle du HAUT est pour GA (Ignore), celle du BAS est pour GB (Garde).
    
    RÈGLES HORAIRES :
    - Col 1: 07h45-09h45
    - Col 2: 10h00-12h00
    - Col 3: 13h30-15h30
    - Col 4: 15h45-17h45

    FORMAT JSON :
    [
      {{
        "day_index": 0, (0=Lundi, 1=Mardi, 2=Mercredi, 3=Jeudi, 4=Vendredi)
        "summary": "Matière (Prof)",
        "start_time": "HH:MM",
        "end_time": "HH:MM",
        "location": "Salle",
        "raw_text": "Texte complet lu pour vérification"
      }}
    ]
    Profs: {PROFS_DICT}
    """
    
    for model in model_list:
        print(f"   👉 Tentative lecture avec {model}...")
        try:
            resp = call_gemini(image, model, prompt)
            if resp.status_code == 200:
                raw = resp.json()
                if 'candidates' in raw:
                    return json.loads(clean_json_text(raw['candidates'][0]['content']['parts'][0]['text']))
            elif resp.status_code in [429, 503]:
                print(f"      ⚠️ Surcharge ({resp.status_code}). Suivant...")
                continue
        except Exception as e:
            print(f"      ❌ Erreur: {e}")
            continue
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
        if "EXAMEN" in summary.upper():
            summary = "🔴 " + summary
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

    # 1. Modèles
    avail = get_available_models()
    # Votre liste de priorité
    prio = [
        # --- GÉNÉRATION 3 ---
        "gemini-3-pro-preview",
        "gemini-3-flash-preview",
        # --- GÉNÉRATION 2.5 ---
        "gemini-2.5-pro",
        "gemini-2.5-flash",
        # --- GÉNÉRATION 2.0 ---
        "gemini-2.0-flash-001",
        # --- LITE ---
        "gemini-2.5-flash-lite",
        "gemini-2.0-flash-lite-preview-02-05",
        # --- GÉNÉRATION 1.5 ---
        "gemini-1.5-pro-latest",
        "gemini-1.5-pro",
        "gemini-1.5-flash-latest",
        "gemini-1.5-flash",
        "gemini-1.5-flash-8b"
    ]
    models = [m for m in prio if m in avail]
    if not models: models = ["gemini-1.5-flash"]
    
    print(f"📋 Modèles actifs : {models}")

    print("Téléchargement PDF...")
    response = requests.get(PDF_URL)
    images = convert_from_bytes(response.content, dpi=300) 

    final_events = []

    print(f"Traitement de {len(images)} pages...")
    for i, img in enumerate(images):
        print(f"--- Page {i+1} ---")
        
        # A. Nettoyage Visuel (Destructif pour Sport)
        clean_img = preprocess_destructive(img)
        
        # B. Détection de la date réelle (Anti-Mélange)
        date_str = detect_week_date_from_image(img, models)
        week_start = parse_date_string(date_str)
        
        # Fallback si date non lue (ex: page 1 = 12 janv par défaut)
        if not week_start:
            print("      ⚠️ Date non détectée, estimation...")
            # Estimation: 12 Janvier + 7 jours * page index
            start_dt = datetime(2026, 1, 12) + timedelta(days=i*7)
            week_start = start_dt.strftime("%Y-%m-%d")
            
        print(f"   📅 Semaine détectée : {week_start}")
        
        # C. Extraction des cours
        raw_events = extract_schedule(clean_img, models, week_start)
        
        # D. Filtrage Python (Anti-Sport / Anti-GA)
        week_dt = datetime.strptime(week_start, "%Y-%m-%d")
        
        for evt in raw_events:
            summary = evt.get('summary', '').upper()
            raw_txt = evt.get('raw_text', '').upper()
            
            # FILTRES DURS
            if "SPORT" in summary and "EXAMEN" not in summary: continue
            if "/GA" in raw_txt or "/GC" in raw_txt or "(GA)" in summary or "(GC)" in summary: continue
            if "GROUPE A" in raw_txt: continue

            # Calcul date exacte
            day_idx = evt.get('day_index', 0)
            real_date = (week_dt + timedelta(days=day_idx)).strftime("%Y-%m-%d")
            
            evt['date'] = real_date
            final_events.append(evt)
            
        print(f"   ✅ {len(raw_events)} cours extraits -> {len(final_events)} après filtrage.")
        time.sleep(2)

    print("Génération ICS...")
    with open(OUTPUT_FILE, "w", encoding="utf-8") as f:
        f.write(create_ics(final_events))
    print("Terminé.")

if __name__ == "__main__":
    main()
