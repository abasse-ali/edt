import os
import io
import json
import base64
import requests
import re
import time
import numpy as np
from pdf2image import convert_from_bytes
from PIL import Image
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
    # Masque Orange -> Noir
    img_array = np.array(pil_image)
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
    Analyse l'emploi du temps (multi-semaines).
    OBJECTIF : Lister les cours avec leurs COORDONNÉES et POSITION.
    
    RÈGLES VISUELLES :
    1. Dates à gauche (ex: 12/janv).
    2. Fond NOIR = IGNORE.
    3. Fond JAUNE = EXAMEN.
    4. **POSITION** : Regarde dans la case. Le texte est-il en HAUT (Groupe A), en BAS (Groupe B) ou MILIEU ?

    FORMAT JSON :
    [
      {{ "type": "DATE_LABEL", "text": "12/janv", "box_2d": [ymin, xmin, ymax, xmax] }},
      {{ 
        "type": "COURSE", 
        "day_name": "Lundi...", 
        "summary": "Matière", 
        "start": "HH:MM", "end": "HH:MM", "location": "Salle", 
        "box_2d": [ymin, xmin, ymax, xmax], 
        "position": "HAUT/BAS/MILIEU",
        "is_exam": true 
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

def geometric_filtering_and_dating(raw_items):
    final_events = []
    
    # 1. Structure Semaines
    date_labels = sorted([x for x in raw_items if x['type'] == 'DATE_LABEL'], key=lambda k: k['box_2d'][0])
    courses = [x for x in raw_items if x['type'] == 'COURSE']
    if not date_labels: date_labels = [{'text': '12/janv', 'box_2d': [0, 0, 1000, 0]}]

    courses_by_week = {i: [] for i in range(len(date_labels))}
    for c in courses:
        c_y = c['box_2d'][0]
        week_idx = -1
        for i, lbl in enumerate(date_labels):
            if c_y >= lbl['box_2d'][0] - 50: week_idx = i
            else: break
        if week_idx >= 0: courses_by_week[week_idx].append(c)

    # 2. Traitement Semaine
    for idx, week_courses in courses_by_week.items():
        week_text = date_labels[idx]['text']
        week_start_str = parse_date_string(week_text) or "2026-01-12"
        print(f"      🗓️ Semaine {week_text}...")
        
        # Groupement par JOUR
        days = ["Lundi", "Mardi", "Mercredi", "Jeudi", "Vendredi"]
        courses_by_day = {d: [] for d in days}
        for c in week_courses:
            for d in days:
                if d in c.get('day_name', ''):
                    courses_by_day[d].append(c)
                    break
        
        # 3. ANALYSE "SMART GRID" PAR JOUR
        for day_name, day_items in courses_by_day.items():
            if not day_items: continue
            
            # --- A. Détection du Seuil de Division (Split Threshold) ---
            # On cherche des créneaux horaires avec superpositions pour calibrer la ligne
            slots = {}
            for c in day_items:
                start = c.get('start', '00').split(':')[0]
                if start not in slots: slots[start] = []
                slots[start].append(c)
            
            split_thresholds = []
            for t, items in slots.items():
                if len(items) >= 2:
                    # Duel détecté ! Le seuil est entre le HAUT (ymin petit) et le BAS (ymin grand)
                    items.sort(key=lambda x: x['box_2d'][0])
                    # Seuil = (Bas du cours haut + Haut du cours bas) / 2
                    thresh = (items[0]['box_2d'][2] + items[-1]['box_2d'][0]) / 2
                    split_thresholds.append(thresh)
            
            # Calcul du seuil moyen pour la journée
            if split_thresholds:
                day_split_line = sum(split_thresholds) / len(split_thresholds)
                # print(f"         📏 Ligne de flottaison {day_name} : {day_split_line}")
            else:
                # Pas de duel ? On estime le milieu de la zone occupée
                all_ymin = [x['box_2d'][0] for x in day_items]
                all_ymax = [x['box_2d'][2] for x in day_items]
                day_split_line = (min(all_ymin) + max(all_ymax)) / 2
            
            # --- B. Filtrage ---
            for c in day_items:
                summary = c.get('summary', '').upper()
                c_center = (c['box_2d'][0] + c['box_2d'][2]) / 2
                pos_tag = c.get('position', 'MILIEU').upper()
                
                # 1. Filtres Absolus (Tags/Couleurs)
                if "SPORT" in summary and "EXAMEN" not in summary: continue
                if "/GA" in summary or re.search(r'\bGA\b', summary): continue
                
                # Détection Groupe Cible
                is_target = False
                prefix = ""
                if re.search(r'(\b|/|\()GC\b', summary): 
                    prefix = "[GC] "
                    is_target = True
                elif re.search(r'(\b|/|\()GB\b', summary): 
                    prefix = "[GB] "
                    is_target = True
                
                if prefix and not c.get('summary', '').startswith("["):
                    c['summary'] = prefix + c.get('summary', '')

                # 2. FILTRE GÉOMÉTRIQUE STRICT
                # Si le centre est Au-dessus de la ligne de flottaison (- marge)
                # ET ce n'est pas un groupe cible explicite -> POUBELLE
                margin = 20 # pixels
                if c_center < (day_split_line - margin):
                    if not is_target:
                        print(f"         🗑️ Rejet STRICT HAUT ({summary})")
                        continue
                
                # 3. Sécurité IA (si géométrie ambiguë)
                if pos_tag == "HAUT" and not is_target:
                     # Double check : si on est vraiment proche de la ligne, on fait confiance à l'IA
                     if c_center < day_split_line + margin:
                         print(f"         🗑️ Rejet IA HAUT ({summary})")
                         continue

                # Ajout
                day_offset = days.index(day_name)
                dt = datetime.strptime(week_start_str, "%Y-%m-%d") + timedelta(days=day_offset)
                c['real_date'] = dt.strftime("%Y-%m-%d")
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
    ics = ["BEGIN:VCALENDAR", "VERSION:2.0", "PRODID:-//STRI//Groupe GB-GC//FR", "CALSCALE:GREGORIAN", "METHOD:PUBLISH"]
    for evt in events:
        try:
            d = evt['real_date'].replace('-', '')
            s = evt['start'].replace(':', '') + "00"
            e = evt['end'].replace(':', '') + "00"
            summ = evt['summary']
            prio = "5"
            if evt.get('is_exam') or "EXAMEN" in summ.upper():
                if "🔴" not in summ: summ = "🔴 [EXAMEN] " + summ.replace("[EXAMEN]", "").strip()
                prio = "1"
            ics.append("BEGIN:VEVENT")
            ics.append(f"DTSTART:{d}T{s}")
            ics.append(f"DTEND:{d}T{e}")
            ics.append(f"SUMMARY:{summ}")
            ics.append(f"LOCATION:{evt.get('location', '')}")
            ics.append(f"PRIORITY:{prio}")
            ics.append("DESCRIPTION:Groupe GB et GC")
            ics.append("END:VEVENT")
        except: continue
    ics.append("END:VCALENDAR")
    return "\n".join(ics)

def main():
    if not API_KEY: raise Exception("Clé API manquante")
    avail = get_available_models()
    prio = ["gemini-3-pro-preview", "gemini-3-flash-preview", "gemini-2.5-pro", "gemini-2.5-flash", "gemini-2.0-flash-001", "gemini-1.5-pro-latest"]
    models = [m for m in prio if m in avail] or ["gemini-1.5-flash"]
    
    print(f"📋 Modèles : {models}")
    print("Téléchargement PDF...")
    response = requests.get(PDF_URL)
    images = convert_from_bytes(response.content, dpi=300) 
    all_events = []

    print(f"Traitement de {len(images)} pages...")
    for i, img in enumerate(images):
        print(f"--- Page {i+1} ---")
        clean_img = preprocess_destructive(img)
        raw = extract_schedule_with_geometry(clean_img, models)
        if raw:
            valid = geometric_filtering_and_dating(raw)
            all_events.extend(valid)
            print(f"   ✅ {len(valid)} cours validés.")
        else: print("   ❌ Echec.")
        time.sleep(2)

    print("Génération ICS...")
    with open(OUTPUT_FILE, "w", encoding="utf-8") as f:
        f.write(create_ics(all_events))
    print("Terminé.")

if __name__ == "__main__":
    main()
