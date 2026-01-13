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
    prompt = f"""
    Analyse cette image d'emploi du temps (multi-semaines).
    ANNÉE : 2026.

    OBJECTIF : Lister TOUT ce que tu vois avec les coordonnées.
    
    RÈGLES VISUELLES :
    1. **Dates** : Repère les dates à gauche (ex: "12/janv").
    2. **Cours** : Lis le contenu des cases.
    3. **Couleurs** : Fond NOIR = IGNORE. Fond JAUNE = EXAMEN ("is_exam": true).

    FORMAT DE SORTIE JSON :
    [
      {{
        "type": "DATE_LABEL",
        "text": "12/janv",
        "box_2d": [ymin, xmin, ymax, xmax]
      }},
      {{
        "type": "COURSE",
        "day_name": "Lundi" (ou Mardi...),
        "summary": "Matière (Prof)",
        "start": "HH:MM",
        "end": "HH:MM",
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

def classify_and_filter_event(course, row_center, row_buffer):
    """
    Détermine la catégorie du cours (GA, GB, GC, COMMUN, EXAMEN) et rejette GA.
    """
    summary = course.get('summary', '').upper()
    c_center = (course['box_2d'][0] + course['box_2d'][2]) / 2
    
    # 1. DÉTECTION EXAMEN
    if course.get('is_exam') or "EXAMEN" in summary:
        return "EXAMEN"

    # 2. DÉTECTION EXPLICITE PAR TEXTE
    if "/GA" in summary or "(GA)" in summary:
        return "GA" # À rejeter
    if "/GC" in summary or "(GC)" in summary:
        return "GC"
    if "/GB" in summary or "(GB)" in summary:
        return "GB"

    # 3. DÉTECTION GÉOMÉTRIQUE (Si pas de tag explicite)
    # Si le cours est clairement en HAUT -> On assume GA
    if c_center < (row_center - row_buffer):
        return "GA" # À rejeter
    
    # Si le cours est clairement en BAS -> On assume GB
    if c_center > (row_center + row_buffer):
        return "GB"

    # Sinon (Centré) -> COMMUN
    return "COMMUN"

def geometric_filtering_and_dating(raw_items):
    final_events = []
    
    # Séparation Dates / Cours
    date_labels = sorted([x for x in raw_items if x['type'] == 'DATE_LABEL'], key=lambda k: k['box_2d'][0])
    courses = [x for x in raw_items if x['type'] == 'COURSE']
    
    if not date_labels: date_labels = [{'text': '12/janv', 'box_2d': [0, 0, 1000, 0]}]

    # Association Cours -> Semaine
    courses_by_week = {i: [] for i in range(len(date_labels))}
    for c in courses:
        c_y = c['box_2d'][0]
        week_idx = -1
        for i, lbl in enumerate(date_labels):
            if c_y >= lbl['box_2d'][0] - 50: week_idx = i
            else: break
        if week_idx >= 0: courses_by_week[week_idx].append(c)

    # Traitement par semaine
    for idx, week_courses in courses_by_week.items():
        week_text = date_labels[idx]['text']
        week_start_str = parse_date_string(week_text) or "2026-01-12"
        
        days = ["Lundi", "Mardi", "Mercredi", "Jeudi", "Vendredi"]
        courses_by_day = {d: [] for d in days}
        
        for c in week_courses:
            for d in days:
                if d in c.get('day_name', ''):
                    courses_by_day[d].append(c)
                    break
        
        for day_name, day_items in courses_by_day.items():
            if not day_items: continue
            
            # Calcul géométrie ligne
            y_mins = [x['box_2d'][0] for x in day_items]
            y_maxs = [x['box_2d'][2] for x in day_items]
            row_height = max(y_maxs) - min(y_mins)
            row_center = (min(y_mins) + max(y_maxs)) / 2
            buffer = row_height * 0.1
            
            for c in day_items:
                # Classification
                category = classify_and_filter_event(c, row_center, buffer)
                
                # FILTRAGE : On jette GA et Sport
                if category == "GA":
                    # print(f"         🗑️ Rejet (GA/Haut): {c.get('summary')}")
                    continue
                
                summary = c.get('summary', '').upper()
                if "SPORT" in summary and category != "EXAMEN":
                    continue

                # Calcul date
                day_offset = days.index(day_name)
                dt = datetime.strptime(week_start_str, "%Y-%m-%d") + timedelta(days=day_offset)
                
                c['real_date'] = dt.strftime("%Y-%m-%d")
                c['category'] = category # Pour la couleur
                
                final_events.append(c)

    return final_events

def parse_date_string(date_str):
    try:
        clean = date_str.lower().replace("janv", "01").replace("févr", "02").replace("mars", "03").replace("avr", "04").strip()
        clean = re.sub(r"[^0-9/]", "", clean)
        day, month = clean.split('/')
        return f"2026-{month.zfill(2)}-{day.zfill(2)}"
    except: return None

def create_ics(events):
    # Mapping Couleurs Google Calendar (ID)
    # 11: Tomate (Rouge) -> EXAMEN
    # 2: Sauge (Vert Menthe) -> GB
    # 9: Myrtille (Bleu/Violet foncé) -> GC
    # 7: Paon (Bleu clair/Turquoise) -> COMMUN
    color_map = {
        "EXAMEN": "11",
        "GB": "2",
        "GC": "9",
        "COMMUN": "7"
    }

    ics = [
        "BEGIN:VCALENDAR",
        "VERSION:2.0",
        "PRODID:-//STRI//Groupe GB-GC//FR",
        "CALSCALE:GREGORIAN",
        "METHOD:PUBLISH"
    ]
    
    for evt in events:
        try:
            d = evt['real_date'].replace('-', '')
            s = evt['start'].replace(':', '') + "00"
            e = evt['end'].replace(':', '') + "00"
            
            cat = evt['category']
            color_id = color_map.get(cat, "7")
            summary = evt['summary']
            
            prio = "5"
            if cat == "EXAMEN":
                if "🔴" not in summary: summary = "🔴 [EXAMEN] " + summary.replace("[EXAMEN]", "").strip()
                prio = "1"
            
            # On ajoute le groupe dans le titre pour clarté
            if cat in ["GB", "GC"]:
                summary = f"[{cat}] {summary}"

            ics.append("BEGIN:VEVENT")
            ics.append(f"DTSTART:{d}T{s}")
            ics.append(f"DTEND:{d}T{e}")
            ics.append(f"SUMMARY:{summary}")
            ics.append(f"LOCATION:{evt.get('location', '')}")
            ics.append(f"PRIORITY:{prio}")
            ics.append(f"X-GOOGLE-CALENDAR-COLOR:{color_id}") # Couleur Google
            ics.append("DESCRIPTION:Généré par IA - Couleurs: Vert=GB, Violet=GC, Bleu=Commun")
            ics.append("END:VEVENT")
        except: continue
        
    ics.append("END:VCALENDAR")
    return "\n".join(ics)

def main():
    if not API_KEY: raise Exception("Clé API manquante")

    avail = get_available_models()
    prio = [
        "gemini-3-pro-preview", "gemini-3-flash-preview",
        "gemini-2.5-pro", "gemini-2.5-flash",
        "gemini-2.0-flash-001", "gemini-2.5-flash-lite", "gemini-2.0-flash-lite-preview-02-05",
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
        
        # Extraction
        raw_items = extract_schedule_with_geometry(clean_img, models)
        
        # Filtrage et Classification
        if raw_items:
            valid_events = geometric_filtering_and_dating(raw_items)
            all_events.extend(valid_events)
            print(f"   ✅ {len(valid_events)} cours validés (GB/GC/Commun).")
        else:
            print("   ❌ Echec extraction.")
        
        time.sleep(2)

    print("Génération ICS...")
    with open(OUTPUT_FILE, "w", encoding="utf-8") as f:
        f.write(create_ics(all_events))
    print("Terminé.")

if __name__ == "__main__":
    main()
