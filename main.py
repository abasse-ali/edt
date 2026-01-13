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
    Transforme l'ORANGE en NOIR pour masquer le texte 'Sport' ou les cours annulés.
    """
    img_array = np.array(pil_image)
    
    # ORANGE (#FFB84D) ~ [255, 184, 77]
    # R > 180, G entre 100 et 210, B < 160
    red_cond = img_array[:, :, 0] > 180
    green_cond = (img_array[:, :, 1] > 100) & (img_array[:, :, 1] < 210)
    blue_cond = img_array[:, :, 2] < 160
    
    mask_orange = red_cond & green_cond & blue_cond
    
    # Remplacement par NOIR (0,0,0) -> Le texte noir dessus devient illisible
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
    Demande à l'IA d'extraire les cours AVEC leurs coordonnées (Bounding Boxes).
    Cela permet de savoir mathématiquement qui est en haut et qui est en bas.
    """
    
    prompt = f"""
    Analyse cette image d'emploi du temps (qui contient plusieurs semaines).
    ANNÉE : 2026.

    OBJECTIF : Lister TOUT ce que tu vois (Matières, Dates) avec leurs COORDONNÉES.
    
    RÈGLES VISUELLES :
    1. **Dates** : Repère les dates de début de semaine à gauche (ex: "12/janv", "19/janv").
    2. **Cours** : Lis le contenu des cases.
    3. **Couleurs** :
       - Fond NOIR (anciennement orange) -> IGNORE.
       - Fond JAUNE -> Marque "is_exam": true.

    FORMAT DE SORTIE JSON (Liste d'objets) :
    [
      {{
        "type": "DATE_LABEL",
        "text": "12/janv",
        "box_2d": [ymin, xmin, ymax, xmax]  (Échelle 0-1000)
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

def geometric_filtering_and_dating(raw_items):
    """
    Cerveau du script : 
    1. Associe chaque cours à la bonne semaine (basé sur la position Y).
    2. Supprime les cours du HAUT (basé sur la position Y relative dans la journée).
    """
    final_events = []
    
    # 1. Séparer les étiquettes de dates et les cours
    date_labels = sorted([x for x in raw_items if x['type'] == 'DATE_LABEL'], key=lambda k: k['box_2d'][0])
    courses = [x for x in raw_items if x['type'] == 'COURSE']
    
    print(f"   📊 Structure : {len(date_labels)} semaines détectées, {len(courses)} cours bruts.")

    # Si aucune date détectée (cas rare), on simule pour la page 1
    if not date_labels:
        date_labels = [{'text': '12/janv', 'box_2d': [0, 0, 1000, 0]}]

    # 2. Associer chaque cours à sa semaine
    # Un cours appartient à la semaine dont le label est juste au-dessus de lui.
    courses_by_week = {i: [] for i in range(len(date_labels))}
    
    for c in courses:
        c_y = c['box_2d'][0] # Ymin du cours
        
        # Trouver la semaine correspondante
        week_idx = -1
        for i, lbl in enumerate(date_labels):
            if c_y >= lbl['box_2d'][0] - 50: # Marge de tolérance
                week_idx = i
            else:
                break # On a dépassé
        
        if week_idx >= 0:
            courses_by_week[week_idx].append(c)

    # 3. Traiter chaque semaine
    for idx, week_courses in courses_by_week.items():
        week_text = date_labels[idx]['text']
        week_start_str = parse_date_string(week_text)
        if not week_start_str: 
            # Fallback manuel si lecture ratée
            week_start_str = "2026-01-12" 
            
        print(f"      🗓️ Traitement semaine {week_text} ({week_start_str})...")
        
        # Regrouper par JOUR (Lundi, Mardi...) pour analyser les hauteurs de ligne
        days = ["Lundi", "Mardi", "Mercredi", "Jeudi", "Vendredi"]
        courses_by_day = {d: [] for d in days}
        
        for c in week_courses:
            for d in days:
                if d in c.get('day_name', ''):
                    courses_by_day[d].append(c)
                    break
        
        # 4. FILTRAGE HAUT/BAS PAR JOUR
        for day_name, day_items in courses_by_day.items():
            if not day_items: continue
            
            # Calcul des limites verticales de la journée (Ligne entière)
            # On regarde le min_y et max_y de TOUS les cours de ce jour
            y_mins = [x['box_2d'][0] for x in day_items]
            y_maxs = [x['box_2d'][2] for x in day_items]
            
            row_top = min(y_mins)
            row_bottom = max(y_maxs)
            row_height = row_bottom - row_top
            row_center = (row_top + row_bottom) / 2
            
            # Seuil de décision : Si le centre du cours est au-dessus du centre de la ligne -> HAUT
            # On ajoute une petite marge (buffer) pour ne pas virer les centrés
            buffer = row_height * 0.1 
            
            for c in day_items:
                c_center = (c['box_2d'][0] + c['box_2d'][2]) / 2
                summary = c.get('summary', '').upper()
                
                # FILTRE 1 : "GA/GC" explicite
                if "/GA" in summary or "/GC" in summary or "(GA)" in summary:
                    # print(f"         🗑️ Rejet (Tag GA): {summary}")
                    continue

                # FILTRE 2 : GÉOMÉTRIE (Le plus important)
                # Si le cours est clairement dans la moitié haute
                if c_center < (row_center - buffer):
                    # C'est un cours du HAUT. On rejette SAUF si c'est marqué "GB"
                    if "GB" not in summary:
                        print(f"         🗑️ Rejet (Position HAUT): {summary}")
                        continue
                
                # FILTRE 3 : Sport (Sécurité si nettoyage couleur raté)
                if "SPORT" in summary and "EXAMEN" not in summary:
                    continue

                # C'est validé ! On calcule la date
                day_offset = days.index(day_name)
                dt = datetime.strptime(week_start_str, "%Y-%m-%d") + timedelta(days=day_offset)
                c['real_date'] = dt.strftime("%Y-%m-%d")
                
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
        try:
            d = evt['real_date'].replace('-', '')
            s = evt['start'].replace(':', '') + "00"
            e = evt['end'].replace(':', '') + "00"
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
        except: continue
    ics.append("END:VCALENDAR")
    return "\n".join(ics)

def main():
    if not API_KEY: raise Exception("Clé API manquante")

    avail = get_available_models()
    # LISTE DE PRIORITÉ UTILISATEUR
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
        
        # 1. Masquage Orange -> Noir (Textes Sport/Annulé deviennent invisibles)
        clean_img = preprocess_destructive(img)
        
        # 2. Extraction avec Géométrie
        raw_items = extract_schedule_with_geometry(clean_img, models)
        
        # 3. Filtrage Python (Haut vs Bas) et Datation
        if raw_items:
            valid_events = geometric_filtering_and_dating(raw_items)
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
