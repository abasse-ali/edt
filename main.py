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

def preprocess_image_destructive(pil_image):
    """
    1. Détecte l'ORANGE (Sport/Annulé).
    2. Le remplace par du NOIR (0,0,0). 
       => Le texte noir sur fond noir devient INVISIBLE.
    """
    img_array = np.array(pil_image)
    
    # ORANGE (#FFB84D) : R>200, 130<G<200, B<150
    # JAUNE (#FFD966)  : R>200, G>210, B<150
    
    red_cond = img_array[:, :, 0] > 180
    green_cond = (img_array[:, :, 1] > 100) & (img_array[:, :, 1] < 205)
    blue_cond = img_array[:, :, 2] < 160
    
    mask_orange = red_cond & green_cond & blue_cond
    
    # Remplacement destructif : NOIR
    img_array[mask_orange] = [0, 0, 0] 
    
    return Image.fromarray(img_array)

def smart_slice_image(pil_image):
    """
    Découpe l'image en détectant les lignes horizontales noires du tableau.
    """
    # Conversion niveau de gris
    gray = pil_image.convert('L')
    # Binarisation (Noir et Blanc strict)
    threshold = 200
    bw = gray.point(lambda x: 0 if x < threshold else 255, '1')
    
    # Inversion (Lignes deviennent blanches sur fond noir)
    bw_inv = ImageOps.invert(bw.convert('L'))
    pixels = np.array(bw_inv)
    
    # Somme des pixels blancs par ligne (Projection horizontale)
    row_sums = np.sum(pixels, axis=1)
    
    # On cherche les pics (lignes horizontales)
    # Un seuil empirique : si la ligne est > 30% noire
    width = pil_image.width
    line_threshold = width * 255 * 0.3
    
    lines = np.where(row_sums > line_threshold)[0]
    
    # Filtrage des lignes trop proches (doublons)
    cleaned_lines = []
    if len(lines) > 0:
        cleaned_lines.append(lines[0])
        for l in lines:
            if l - cleaned_lines[-1] > 50: # Minimum 50px de hauteur par jour
                cleaned_lines.append(l)
    
    # Si la détection échoue, on fallback sur le découpage mathématique
    if len(cleaned_lines) < 6:
        print("   ⚠️ Détection de lignes échouée, fallback découpage simple.")
        h = pil_image.height
        header = int(h * 0.1)
        step = (h - header) / 5
        cleaned_lines = [header + i*step for i in range(6)]
        
    slices = []
    days = ["Lundi", "Mardi", "Mercredi", "Jeudi", "Vendredi"]
    
    for i in range(min(5, len(cleaned_lines)-1)):
        top = int(cleaned_lines[i])
        bottom = int(cleaned_lines[i+1])
        # On ajoute une petite marge pour éviter de couper le texte
        box = (0, top+2, width, bottom-2)
        slices.append((days[i], pil_image.crop(box)))
        
    return slices

def call_gemini_api(image, model_name, day_hint):
    url = f"https://generativelanguage.googleapis.com/v1beta/models/{model_name}:generateContent?key={API_KEY}"
    
    img_byte_arr = io.BytesIO()
    image.save(img_byte_arr, format='JPEG')
    b64_data = base64.b64encode(img_byte_arr.getvalue()).decode('utf-8')

    prompt = f"""
    Analyse cette bande d'emploi du temps pour : {day_hint}.
    GROUPE CIBLE : GB (Groupe B).
    ANNÉE : 2026.

    RÈGLES CRITIQUES :
    1. **COURS EFFACÉS** : L'image a été traitée. Les zones NOIRES sont des cours annulés -> IGNORE TOTALEMENT.
    2. **GROUPES (HAUT vs BAS)** :
       - Dans chaque case, il y a souvent DEUX lignes de texte.
       - HAUT = Groupe A (GA/GC) -> IGNORE.
       - BAS = Groupe B (GB) -> GARDE.
       - Si texte unique centré -> GARDE.
    3. **HORAIRES** :
       - Créneau 1 (Gauche) : 07h45-09h45
       - Créneau 2 : 10h00-12h00
       - Créneau 3 : 13h30-15h30
       - Créneau 4 (Droite) : 15h45-17h45

    FORMAT JSON :
    [
      {{ "summary": "Matière (Prof)", "start": "HH:MM", "end": "HH:MM", "location": "Salle", "position": "BAS/HAUT/UNIQUE" }}
    ]
    Profs: {PROFS_DICT}
    """

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

def get_schedule_robust(image, day_name):
    available = get_available_models()
    # LISTE DE PRIORITÉ EXACTE DU CLIENT
    priority_list = [
        "gemini-3-pro-preview", "gemini-3-flash-preview",
        "gemini-2.5-pro", "gemini-2.5-flash",
        "gemini-2.0-flash-001",
        "gemini-2.5-flash-lite", "gemini-2.0-flash-lite-preview-02-05",
        "gemini-1.5-pro-latest", "gemini-1.5-pro",
        "gemini-1.5-flash-latest", "gemini-1.5-flash", "gemini-1.5-flash-8b"
    ]
    models = [m for m in priority_list if m in available]
    if not models: models = ["gemini-1.5-flash"]

    for model in models:
        try:
            # print(f"   👉 {day_name} via {model}...")
            response = call_gemini_api(image, model, day_name)
            if response.status_code == 200:
                raw = response.json()
                if 'candidates' in raw and raw['candidates']:
                    clean = clean_json_text(raw['candidates'][0]['content']['parts'][0]['text'])
                    return json.loads(clean)
            elif response.status_code in [429, 503]:
                continue
        except: continue
    return []

def create_ics_file(all_events):
    ics = [
        "BEGIN:VCALENDAR",
        "VERSION:2.0",
        "PRODID:-//STRI//Groupe GB//FR",
        "CALSCALE:GREGORIAN",
        "METHOD:PUBLISH"
    ]
    for date, events in all_events.items():
        for evt in events:
            try:
                summary = evt.get('summary', '').strip()
                pos = evt.get('position', 'UNIQUE').upper()
                
                # FILTRE "SPORT" (Sécurité ultime)
                if "SPORT" in summary.upper() and "EXAMEN" not in summary.upper():
                    continue
                
                # FILTRE "HAUT"
                if pos == "HAUT" and "GB" not in summary.upper():
                    continue
                
                # FILTRE TAGS GA/GC
                if "/GA" in summary.upper() or "/GC" in summary.upper():
                    continue

                d = date.replace('-', '')
                s = evt['start'].replace(':', '') + "00"
                e = evt['end'].replace(':', '') + "00"
                
                final_sum = evt.get('summary', 'Cours')
                prio = "5"
                if "EXAMEN" in final_sum.upper():
                    final_sum = "🔴 " + final_sum
                    prio = "1"

                ics.append("BEGIN:VEVENT")
                ics.append(f"DTSTART:{d}T{s}")
                ics.append(f"DTEND:{d}T{e}")
                ics.append(f"SUMMARY:{final_sum}")
                ics.append(f"LOCATION:{evt.get('location', '')}")
                ics.append(f"PRIORITY:{prio}")
                ics.append("DESCRIPTION:Groupe GB")
                ics.append("END:VEVENT")
            except: continue
    ics.append("END:VCALENDAR")
    return "\n".join(ics)

def main():
    if not API_KEY: raise Exception("Clé API manquante")

    print("Téléchargement PDF...")
    response = requests.get(PDF_URL)
    images = convert_from_bytes(response.content, dpi=300) 

    final_data = {}
    from datetime import datetime, timedelta
    current_monday = datetime(2026, 1, 12) 

    print(f"Traitement de {len(images)} pages...")
    for i, img in enumerate(images):
        print(f"--- Page {i+1} ---")
        
        # 1. Noircissement des cours annulés/sport
        clean_img = preprocess_image_destructive(img)
        
        # 2. Découpage intelligent par lignes noires
        day_slices = smart_slice_image(clean_img)
        
        for day_name, day_img in day_slices:
            print(f"   📅 Analyse {day_name}...")
            
            day_idx = ["Lundi", "Mardi", "Mercredi", "Jeudi", "Vendredi"].index(day_name)
            real_date = (current_monday + timedelta(days=day_idx + (i*7))).strftime("%Y-%m-%d")
            
            events = get_schedule_robust(day_img, day_name)
            if events:
                final_data[real_date] = events
                print(f"      ✅ {len(events)} cours.")
        
        time.sleep(2)

    print("Génération ICS...")
    ics_content = create_ics_file(final_data)

    with open(OUTPUT_FILE, "w", encoding="utf-8") as f:
        f.write(ics_content)
    print(f"Terminé. Fichier généré : {OUTPUT_FILE}")

if __name__ == "__main__":
    main()
