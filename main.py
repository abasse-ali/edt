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
import statistics

# --- CONFIGURATION ---
PDF_URL = "https://stri.fr/Gestion_STRI/TAV/L3/EDT_STRI1A_L3IRT_TAV.pdf"
OUTPUT_FILE = "emploi_du_temps.ics"
API_KEY = os.environ.get("GEMINI_API_KEY")
CONSENSUS_RETRIES = 5

PROFS_DICT = """
AnAn=Andréi ANDRÉI; AA=André AOUN; AB=Abdelmalek BENZEKRI; AL=Abir LARABA; BC=Bilal CHEBARO; 
BTJ=Boris TIOMELA JOU; CC=Cédric CHAMBAULT; CG=Christine GALY; CT=Cédric TEYSSIE; EG=Eric GONNEAU; 
EL=Emmanuel LAVINAL; FM=Frédéric MOUTIER; GR=Gérard ROUZIES; JGT=Jean-Guy TARTARIN; JS=Jérôme SOKOLOFF; 
KB=Ketty BRAVO; LC=Louisa COT; MCL=Marie-Christine LAGASQUIÉ; MM=MUSTAPHA MOJAHID; OC=Olivier CRIVELLARO; 
OM=Olfa MECHI; PA=Patrick AUSTIN; PhA=Philippe ARGUEL; PIL=Pierre LOTTE; PL=Philippe LATU; PT=Patrice TORGUET; 
RK=Rahim KACIMI; RL=Romain LABORDE; SB=Sonia BADENE; SL=Séverine LALANDE; TD=Thierry DESPRATS; TG=Thierry GAYRAUD.
"""

# Horaires officiels
OFFICIAL_TIMES = {
    "1": ("07:45", "09:45"),
    "2": ("10:00", "12:00"),
    "3": ("13:30", "15:30"),
    "4": ("15:45", "17:45")
}

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
    img_array = np.array(pil_image)
    # ORANGE (#FFB84D)
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
    
    RÈGLES CRITIQUES :
    1. **NOMS PROFS** : Si tu vois des initiales (ex: AA, JGT), REMPLACE-LES par le nom complet (ex: André AOUN).
    2. **GROUPES** : Si tu vois "Gr A", "GA", "Gr B", "GB", "Gr C", "GC", note-le DANS LE SUMMARY.
    3. **VISUEL** : NOIR = IGNORE. JAUNE = EXAMEN.

    FORMAT JSON :
    [
      {{ "type": "DATE_LABEL", "text": "12/janv", "box_2d": [ymin, xmin, ymax, xmax] }},
      {{ "type": "COURSE", "day_name": "Lundi...", "summary": "Matière (Nom Prof)", "start": "HH:MM", "end": "HH:MM", "location": "Salle", "box_2d": [ymin, xmin, ymax, xmax], "is_exam": true }}
    ]
    LISTE DES PROFS : {PROFS_DICT}
    """
    for model in model_list:
        print(f"         👉 Tentative avec {model}...")
        try:
            resp = call_gemini(image, model, prompt)
            if resp.status_code == 200:
                raw = resp.json()
                if 'candidates' in raw:
                    return json.loads(clean_json_text(raw['candidates'][0]['content']['parts'][0]['text']))
            elif resp.status_code in [429, 503]:
                print(f"         ⚠️ Surcharge ({resp.status_code})...")
                continue
        except: continue
    return []

def filter_by_slot_duel(raw_items):
    final_events = []
    
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

    for idx, week_courses in courses_by_week.items():
        week_text = date_labels[idx]['text']
        week_start_str = parse_date_string(week_text) or "2026-01-12"
        # print(f"      🗓️ Semaine {week_text}...")

        # --- GÉOMÉTRIE PAR MÉDIANE (Plus robuste) ---
        day_geoms = {}
        for day in ["Lundi", "Mardi", "Mercredi", "Jeudi", "Vendredi"]:
            d_items = [c for c in week_courses if day in c.get('day_name', '')]
            if d_items:
                y_mins = [x['box_2d'][0] for x in d_items]
                y_maxs = [x['box_2d'][2] for x in d_items]
                
                # Utilisation de la médiane pour ignorer les outliers
                row_top = statistics.median(y_mins)
                row_bottom = statistics.median(y_maxs)
                row_height = row_bottom - row_top
                
                # "Ligne de flottaison" : Tout ce qui est au-dessus de 45% de la hauteur est "HAUT"
                limit_line = row_top + (row_height * 0.45)
                
                day_geoms[day] = {'limit': limit_line}

        slots = {} 
        for c in week_courses:
            day = c.get('day_name', 'Lundi')
            start_hour = c.get('start', '00:00').split(':')[0]
            try:
                h = int(start_hour)
                if h < 10: slot_id = "1"
                elif h < 13: slot_id = "2"
                elif h < 15: slot_id = "3"
                else: slot_id = "4"
            except: slot_id = "unknown"
            
            c['slot_id'] = slot_id
            slot_key = f"{day}_{slot_id}"
            
            if slot_key not in slots: slots[slot_key] = []
            slots[slot_key].append(c)
            
        for key, slot_items in slots.items():
            # Pré-traitement : Nettoyage Sport et GA explicite
            clean_items = []
            for item in slot_items:
                summary = item.get('summary', '').upper()
                
                if "SPORT" in summary and "EXAMEN" not in summary: 
                    continue
                
                # REJET DIRECT GA / Gr A (Texte)
                if re.search(r'(\b|/|\()GA\b', summary) or "GR A" in summary:
                    print(f"         🗑️ [{key}] SUPPRESSION DIRECTE (Tag GA): {summary}")
                    continue
                    
                clean_items.append(item)
            
            if not clean_items: continue
            
            # FILTRAGE GÉOMÉTRIQUE STRICT (S'applique à tout le monde)
            survivors = []
            day_name = key.split('_')[0]
            
            if day_name in day_geoms:
                limit = day_geoms[day_name]['limit']
                for item in clean_items:
                    c_center = (item['box_2d'][0] + item['box_2d'][2]) / 2
                    
                    # SI C'EST EN HAUT (0-45%) -> POUBELLE
                    if c_center < limit:
                        # On log pour vérification, mais on supprime sans pitié
                        print(f"         ❌ [{key}] GUILLOTINE GÉOMÉTRIQUE (Pos HAUT): {item.get('summary')}")
                    else:
                        survivors.append(item)
            else:
                # Si pas de géométrie (bizarre), on garde tout par défaut
                survivors = clean_items

            if not survivors: continue

            # SÉLECTION FINALE (S'il reste plusieurs cours en bas)
            # On prend le plus bas (le dernier après tri)
            survivors.sort(key=lambda x: x['box_2d'][0]) 
            winner = survivors[-1]
            
            # S'il y a eu un "duel" (plusieurs survivants en bas ? Rare mais possible)
            if len(survivors) > 1:
                 print(f"         ⚔️ [{key}] CONFLIT BAS: Vainqueur ({winner['summary']})")

            # VALIDATION DU GAGNANT
            if winner:
                if winner['slot_id'] in OFFICIAL_TIMES:
                    winner['start'], winner['end'] = OFFICIAL_TIMES[winner['slot_id']]

                summary = winner.get('summary', '')
                
                # Standardisation des tags
                if re.search(r'(\b|/|\()GC\b', summary.upper()) or "GR C" in summary.upper():
                    if not summary.startswith("["): summary = "[GC] " + summary
                elif re.search(r'(\b|/|\()GB\b', summary.upper()) or "GR B" in summary.upper():
                    if not summary.startswith("["): summary = "[GB] " + summary
                
                winner['summary'] = summary
                
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
        clean = date_str.lower().replace("janv", "01").replace("févr", "02").replace("mars", "03").replace("avr", "04").strip()
        clean = re.sub(r"[^0-9/]", "", clean)
        day, month = clean.split('/')
        return f"2026-{month.zfill(2)}-{day.zfill(2)}"
    except: return None

def analyze_page_consensus(image, models):
    all_runs = []
    print(f"   🔄 Lancement du consensus ({CONSENSUS_RETRIES} runs)...")
    for i in range(CONSENSUS_RETRIES):
        print(f"      👉 Run {i+1}/{CONSENSUS_RETRIES}...")
        clean_img = preprocess_destructive(image)
        raw = extract_schedule_with_geometry(clean_img, models)
        if raw:
            filtered = filter_by_slot_duel(raw)
            all_runs.append(filtered)
    
    vote_counts = {}
    event_objects = {}
    for run in all_runs:
        seen_in_run = set()
        for evt in run:
            key = (evt['real_date'], evt['start'], evt['end'], evt['summary'].strip())
            if key in seen_in_run: continue
            seen_in_run.add(key)
            vote_counts[key] = vote_counts.get(key, 0) + 1
            if key not in event_objects: event_objects[key] = evt
            
    final_list = []
    threshold = max(2, int(CONSENSUS_RETRIES * 0.4)) 
    
    for key, count in vote_counts.items():
        if count >= threshold:
            final_list.append(event_objects[key])
        else:
            print(f"      🗑️ Rejet Consensus (Vu {count} fois seulement): {key[3]}")
    return final_list

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
    prio = ["gemini-3-flash-preview", "gemini-2.5-flash", "gemini-2.0-flash", "gemini-2.0-flash-001", "gemini-1.5-flash"]
    models = [m for m in prio if m in avail] or ["gemini-1.5-flash"]
    
    print(f"📋 Modèles : {models}")
    print("Téléchargement PDF...")
    response = requests.get(PDF_URL)
    images = convert_from_bytes(response.content, dpi=300) 
    all_events = []

    print(f"Traitement de {len(images)} pages avec consensus...")
    for i, img in enumerate(images):
        print(f"--- Page {i+1} ---")
        page_events = analyze_page_consensus(img, models)
        all_events.extend(page_events)
        print(f"   ✅ {len(page_events)} cours validés par consensus.")
        time.sleep(2)

    print("Génération ICS...")
    with open(OUTPUT_FILE, "w", encoding="utf-8") as f:
        f.write(create_ics(all_events))
    print("Terminé.")

if __name__ == "__main__":
    main()
