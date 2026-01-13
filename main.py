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
    # Noircissement de l'Orange (Sport) pour suppression
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
        "safetySettings": [{"category": "HARM_CATEGORY_HARASSMENT", "threshold": "BLOCK_NONE"}, {"category": "HARM_CATEGORY_HATE_SPEECH", "threshold": "BLOCK_NONE"}, {"category": "HARM_CATEGORY_SEXUALLY_EXPLICIT", "threshold": "BLOCK_NONE"}, {"category": "HARM_CATEGORY_DANGEROUS_CONTENT", "threshold": "BLOCK_NONE"}]
    }
    return requests.post(url, headers={'Content-Type': 'application/json'}, data=json.dumps(payload))

def extract_schedule_with_geometry(image, model_list):
    prompt = f"""
    Analyse l'emploi du temps (multi-semaines). Année 2026.
    OBJECTIF : Lister TOUT avec COORDONNÉES.
    
    RÈGLES VISUELLES :
    1. Dates à gauche (ex: 12/janv).
    2. Couleurs : NOIR = IGNORE, JAUNE = EXAMEN.
    
    FORMAT JSON :
    [
      {{ "type": "DATE_LABEL", "text": "12/janv", "box_2d": [ymin, xmin, ymax, xmax] }},
      {{ "type": "COURSE", "day_name": "Lundi...", "summary": "Matière", "start": "HH:MM", "end": "HH:MM", "location": "Salle", "box_2d": [ymin, xmin, ymax, xmax], "is_exam": true }}
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

def filter_by_slot_duel(raw_items):
    """
    FILTRAGE PAR DUEL ET GÉOMÉTRIE STRICTE.
    """
    final_events = []
    
    date_labels = sorted([x for x in raw_items if x['type'] == 'DATE_LABEL'], key=lambda k: k['box_2d'][0])
    courses = [x for x in raw_items if x['type'] == 'COURSE']
    
    if not date_labels: date_labels = [{'text': '12/janv', 'box_2d': [0, 0, 1000, 0]}]

    # 1. Associer cours aux semaines
    courses_by_week = {i: [] for i in range(len(date_labels))}
    for c in courses:
        c_y = c['box_2d'][0]
        week_idx = -1
        for i, lbl in enumerate(date_labels):
            if c_y >= lbl['box_2d'][0] - 50: week_idx = i
            else: break
        if week_idx >= 0: courses_by_week[week_idx].append(c)

    # 2. Traitement par semaine
    for idx, week_courses in courses_by_week.items():
        week_text = date_labels[idx]['text']
        week_start_str = parse_date_string(week_text) or "2026-01-12"
        print(f"      🗓️ Semaine {week_text}...")

        # --- A. CALCUL GÉOMÉTRIQUE GLOBAL DE LA SEMAINE PAR JOUR ---
        # On détermine les limites (Haut/Bas) de chaque ligne "Lundi", "Mardi", etc.
        day_geoms = {}
        for day in ["Lundi", "Mardi", "Mercredi", "Jeudi", "Vendredi"]:
            d_items = [c for c in week_courses if day in c.get('day_name', '')]
            if d_items:
                y_mins = [x['box_2d'][0] for x in d_items]
                y_maxs = [x['box_2d'][2] for x in d_items]
                row_top = min(y_mins)
                row_bottom = max(y_maxs)
                row_height = row_bottom - row_top
                day_geoms[day] = {
                    'center': (row_top + row_bottom) / 2,
                    'buffer': row_height * 0.1 # 10% de marge
                }
        # -----------------------------------------------------------

        # Grouper par JOUR + HEURE DE DÉBUT
        slots = {} 
        for c in week_courses:
            day = c.get('day_name', 'Lundi')
            start_hour = c.get('start', '00:00').split(':')[0]
            try:
                h = int(start_hour)
                if h < 10: slot_key = f"{day}_1"   # Matin 1
                elif h < 13: slot_key = f"{day}_2" # Matin 2
                elif h < 15: slot_key = f"{day}_3" # Aprèm 1
                else: slot_key = f"{day}_4"        # Aprèm 2
            except: slot_key = f"{day}_unknown"
            
            if slot_key not in slots: slots[slot_key] = []
            slots[slot_key].append(c)
            
        for key, slot_items in slots.items():
            # Filtre préliminaire : Sport & GA explicite
            clean_items = []
            for item in slot_items:
                summary = item.get('summary', '').upper()
                if "SPORT" in summary and "EXAMEN" not in summary: continue
                if "/GA" in summary or re.search(r'\bGA\b', summary): 
                    # print(f"         🗑️ Rejet (Tag GA): {summary}")
                    continue
                clean_items.append(item)
            
            if not clean_items: continue

            winner = None

            # --- SÉLECTION DU GAGNANT ---
            if len(clean_items) == 1:
                # CAS UNIQUE : On vérifie quand même la géométrie
                # Si le cours est unique mais "collé au plafond", c'est un cours GA -> POUBELLE
                candidate = clean_items[0]
                day_name = key.split('_')[0]
                
                if day_name in day_geoms:
                    geom = day_geoms[day_name]
                    c_center = (candidate['box_2d'][0] + candidate['box_2d'][2]) / 2
                    
                    # Si le centre du cours est significativement au-dessus du centre de la ligne
                    if c_center < (geom['center'] - geom['buffer']):
                        # Exception : Si c'est marqué explicitement GB ou GC, on garde quand même
                        if "GB" not in candidate.get('summary', '').upper() and "GC" not in candidate.get('summary', '').upper():
                            print(f"         🗑️ Rejet STRICT (Unique mais HAUT): {candidate.get('summary')}")
                            continue 
                
                winner = candidate
            else:
                # CAS DUEL : On trie par Y (Ymin = Haut, Ymax = Bas)
                clean_items.sort(key=lambda x: x['box_2d'][0]) # Tri croissant Y
                
                winner = clean_items[-1] # Le dernier est le plus BAS
                loser = clean_items[0]   # Le premier est le plus HAUT
                print(f"         ⚔️ DUEL {key}: Rejet HAUT ({loser['summary']}) / Garde BAS ({winner['summary']})")

            if winner:
                # Traitement final du gagnant
                summary = winner.get('summary', '')
                
                # Tagging GB/GC
                if re.search(r'(\b|/|\()GC\b', summary.upper()):
                    if not summary.startswith("["): summary = "[GC] " + summary
                elif re.search(r'(\b|/|\()GB\b', summary.upper()):
                    if not summary.startswith("["): summary = "[GB] " + summary
                    
                winner['summary'] = summary
                
                # Calcul Date Réelle
                days = ["Lundi", "Mardi", "Mercredi", "Jeudi", "Vendredi"]
                day_offset = 0
                for i, d in enumerate(days):
                    if d in winner.get('day_name', ''): 
                        day_offset = i
                        break
                dt = datetime.strptime(week_start_str, "%Y-%m-%d") + timedelta(days=day_offset)
                winner['real_date'] = dt.strftime("%Y-%m-%d")
                
                final_events.append(winner)

    return final_events

def parse_date_string(date_str):
    try:
        clean = date_str.lower().replace("janv", "01").replace("févr", "02").replace("mars", "03").strip()
        clean = re.sub(r"[^0-9/]", "", clean)
        day, month = clean.split('/')
        return f"2026-{month.zfill(2)}-{day.zfill(2)}"
    except: return None

def create_ics(events):
    ics = ["BEGIN:VCALENDAR", "VERSION:2.0", "PRODID:-//STRI//Groupe GB//FR", "CALSCALE:GREGORIAN", "METHOD:PUBLISH"]
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
            ics.append("DESCRIPTION:Groupe GB")
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
            valid = filter_by_slot_duel(raw)
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
