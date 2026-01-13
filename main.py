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
    
    # ORANGE (#FFB84D) : R>180, 100<G<210, B<160
    red_cond = img_array[:, :, 0] > 180
    green_cond = (img_array[:, :, 1] > 100) & (img_array[:, :, 1] < 210)
    blue_cond = img_array[:, :, 2] < 160
    
    mask_orange = red_cond & green_cond & blue_cond
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

def extract_schedule_with_geometry(image, model_list):
    """
    Extrait cours + coordonnées pour savoir où ils sont (Haut/Bas/Gauche/Droite).
    """
    prompt = f"""
    Analyse cette image d'emploi du temps. ANNÉE : 2026.

    OBJECTIF : Lister TOUS les éléments (Dates, Cours) avec leurs COORDONNÉES.
    
    RÈGLES VISUELLES :
    1. **Dates** : Repère les dates de début de semaine à gauche (ex: "12/janv").
    2. **Cours** : Lis le contenu des cases.
    3. **Couleurs** :
       - Fond NOIR -> IGNORE (Cours annulé/Sport).
       - Fond JAUNE -> Marque "is_exam": true.

    FORMAT DE SORTIE JSON :
    [
      {{
        "type": "DATE_LABEL",
        "text": "12/janv",
        "box_2d": [ymin, xmin, ymax, xmax] (0-1000)
      }},
      {{
        "type": "COURSE",
        "day_name": "Lundi" (ou Mardi...),
        "summary": "Matière (Prof)",
        "location": "Salle",
        "box_2d": [ymin, xmin, ymax, xmax],
        "is_exam": true/false
      }}
    ]
    Profs: {PROFS_DICT}
    """
    
    for model in model_list:
        print(f"   👉 Analyse géométrique avec {model}...")
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

def geometric_processing(raw_items):
    """
    1. Associe cours -> Semaine (Y)
    2. Associe cours -> Jour (Texte)
    3. Calcule l'heure exacte via la colonne (X)
    4. Filtre HAUT vs BAS
    5. Assigne les catégories (GB, GC, Commun, Examen)
    """
    final_events = []
    
    # Séparation
    date_labels = sorted([x for x in raw_items if x['type'] == 'DATE_LABEL'], key=lambda k: k['box_2d'][0])
    courses = [x for x in raw_items if x['type'] == 'COURSE']
    
    if not date_labels: date_labels = [{'text': '12/janv', 'box_2d': [0, 0, 1000, 0]}]

    # --- 1. CLASSEMENT PAR SEMAINE ---
    courses_by_week = {i: [] for i in range(len(date_labels))}
    for c in courses:
        c_y = c['box_2d'][0]
        week_idx = -1
        for i, lbl in enumerate(date_labels):
            if c_y >= lbl['box_2d'][0] - 50:
                week_idx = i
            else: break
        if week_idx >= 0: courses_by_week[week_idx].append(c)

    # --- 2. TRAITEMENT PAR SEMAINE ---
    for idx, week_courses in courses_by_week.items():
        week_text = date_labels[idx]['text']
        week_start_str = parse_date_string(week_text) or "2026-01-12"
        
        print(f"      🗓️ Semaine {week_text} ({week_start_str})...")
        
        days = ["Lundi", "Mardi", "Mercredi", "Jeudi", "Vendredi"]
        courses_by_day = {d: [] for d in days}
        
        for c in week_courses:
            for d in days:
                if d in c.get('day_name', ''):
                    courses_by_day[d].append(c)
                    break
        
        # --- 3. TRAITEMENT PAR JOUR ---
        for day_name, day_items in courses_by_day.items():
            if not day_items: continue
            
            # Limites verticales de la ligne (Journée)
            y_mins = [x['box_2d'][0] for x in day_items]
            y_maxs = [x['box_2d'][2] for x in day_items]
            row_top = min(y_mins)
            row_bottom = max(y_maxs)
            row_center = (row_top + row_bottom) / 2
            buffer = (row_bottom - row_top) * 0.1 # 10% de marge
            
            for c in day_items:
                summary = c.get('summary', '').strip()
                summary_upper = summary.upper()
                
                # --- A. RECALAGE HORAIRE (SNAP X) ---
                # On détermine l'heure selon la colonne (position X)
                # 0-1000 de large. 4 colonnes environ égales.
                x_center = (c['box_2d'][1] + c['box_2d'][3]) / 2
                
                if x_center < 250:
                    start, end = "074500", "094500"
                elif x_center < 500:
                    start, end = "100000", "120000"
                elif x_center < 750:
                    start, end = "133000", "153000" # 13h30
                else:
                    start, end = "154500", "174500"
                
                c['s_time'] = start
                c['e_time'] = end

                # --- B. FILTRAGE ET TYPAGE ---
                category = None # GB, GC, COMMUN, EXAMEN, REJET
                
                # 1. Tags explicites
                is_ga = "/GA" in summary_upper or "(GA)" in summary_upper
                is_gc = "/GC" in summary_upper or "(GC)" in summary_upper
                is_gb = "/GB" in summary_upper or "(GB)" in summary_upper
                is_exam = c.get('is_exam') or "EXAMEN" in summary_upper or "EVAL" in summary_upper
                
                # 2. Position Géométrique
                c_y_center = (c['box_2d'][0] + c['box_2d'][2]) / 2
                is_top_half = c_y_center < (row_center - buffer)
                is_bottom_half = c_y_center > (row_center + buffer)
                is_unique = not is_top_half and not is_bottom_half # Centré

                # --- LOGIQUE DE DECISION ---
                
                if is_ga:
                    category = "REJET"
                elif "SPORT" in summary_upper and not is_exam:
                    category = "REJET"
                elif is_exam:
                    category = "EXAMEN"
                elif is_gc:
                    category = "GC"
                elif is_gb:
                    category = "GB"
                else:
                    # Pas de tag explicite, on juge par la position
                    if is_top_half:
                        # Haut sans tag = GA (souvent) -> Rejet
                        # Sauf si on veut absolument garder GC qui serait en haut ?
                        # Dans le doute, Haut = GA.
                        category = "REJET"
                    elif is_bottom_half:
                        # Bas sans tag = GB
                        category = "GB"
                    else:
                        # Unique/Centré = Commun
                        category = "COMMUN"
                
                if category == "REJET":
                    continue

                # Calcul Date
                day_offset = days.index(day_name)
                dt = datetime.strptime(week_start_str, "%Y-%m-%d") + timedelta(days=day_offset)
                c['real_date'] = dt.strftime("%Y%m%d")
                c['category_type'] = category
                
                final_events.append(c)

    return final_events

def parse_date_string(date_str):
    try:
        clean = date_str.lower().replace("janv", "01").replace("févr", "02").replace("mars", "03").strip()
        clean = re.sub(r"[^0-9/]", "", clean)
        day, month = clean.split('/')
        return f"2026-{month.zfill(2)}-{day.zfill(2)}"
    except: return None

def create_ics(events):
    ics = [
        "BEGIN:VCALENDAR",
        "VERSION:2.0",
        "PRODID:-//STRI//Groupe GB//FR",
        "CALSCALE:GREGORIAN",
        "METHOD:PUBLISH"
    ]
    
    for evt in events:
        cat = evt['category_type']
        summary = evt['summary']
        
        # Décoration du titre selon la catégorie
        if cat == "EXAMEN":
            prefix = "🔴 [EXAMEN]"
            prio = "1"
        elif cat == "GC":
            prefix = "🫐 [GC]"
            prio = "5"
        elif cat == "GB":
            prefix = "🌿 [GB]"
            prio = "5"
        elif cat == "COMMUN":
            prefix = "🔵"
            prio = "5"
        else:
            prefix = ""
            prio = "5"
            
        # On évite les doubles tags
        if "EXAMEN" in summary.upper(): summary = summary.replace("EXAMEN", "").replace("🔴", "").strip()
        final_summary = f"{prefix} {summary}".strip()

        ics.append("BEGIN:VEVENT")
        ics.append(f"DTSTART:{evt['real_date']}T{evt['s_time']}")
        ics.append(f"DTEND:{evt['real_date']}T{evt['e_time']}")
        ics.append(f"SUMMARY:{final_summary}")
        ics.append(f"LOCATION:{evt.get('location', '')}")
        ics.append(f"PRIORITY:{prio}")
        ics.append(f"CATEGORIES:{cat}") # Metadonnée utile
        ics.append("DESCRIPTION:Groupe GB/GC")
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

    all_events = []

    print(f"Traitement de {len(images)} pages...")
    for i, img in enumerate(images):
        print(f"--- Page {i+1} ---")
        clean_img = preprocess_destructive(img)
        raw_items = extract_schedule_with_geometry(clean_img, models)
        
        if raw_items:
            valid_events = geometric_processing(raw_items)
            all_events.extend(valid_events)
            print(f"   ✅ {len(valid_events)} cours validés.")
        else:
            print("   ❌ Echec extraction.")
        time.sleep(2)

    print("Génération ICS...")
    with open(OUTPUT_FILE, "w", encoding="utf-8") as f:
        f.write(create_ics(all_events))
    print("Terminé.")

if __name__ == "__main__":
    main()
