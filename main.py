import os
import io
import json
import base64
import requests
import re
import time
import numpy as np
from pdf2image import convert_from_bytes
from PIL import Image, ImageDraw

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

def clean_json_text(text):
    text = re.sub(r"```json|```", "", text).strip()
    start = text.find('[')
    end = text.rfind(']')
    if start != -1 and end != -1:
        return text[start:end+1]
    return text

def preprocess_image(pil_image):
    """
    Efface chirurgicalement l'orange sans toucher au jaune.
    Orange (#FFB84D) : R=255, G=184, B=77
    Jaune (#FFD966)  : R=255, G=217, B=102
    Différence clé : Le canal VERT (G). 
    Orange < 200 < Jaune.
    """
    print("   🎨 Nettoyage des couleurs...")
    img_array = np.array(pil_image)

    # On cible les pixels qui sont "colorés" (pas noirs, pas blancs)
    # R > 200 (C'est une couleur claire)
    # B < 150 (Il y a du jaune/orange, pas du bleu)
    # 130 < G < 200 (C'est la zone ORANGE spécifique, le jaune est au-dessus de 200)
    
    red_cond = img_array[:, :, 0] > 200
    blue_cond = img_array[:, :, 2] < 180
    green_orange_cond = (img_array[:, :, 1] > 130) & (img_array[:, :, 1] < 205)

    # Masque combiné : C'est de l'orange !
    mask_orange = red_cond & blue_cond & green_orange_cond
    
    # On remplace l'orange par du BLANC pur
    img_array[mask_orange] = [255, 255, 255]

    return Image.fromarray(img_array)

def call_gemini_api(image, model_name):
    url = f"https://generativelanguage.googleapis.com/v1beta/models/{model_name}:generateContent?key={API_KEY}"
    
    img_byte_arr = io.BytesIO()
    image.save(img_byte_arr, format='JPEG')
    b64_data = base64.b64encode(img_byte_arr.getvalue()).decode('utf-8')

    prompt = f"""
    Analyse cette image d'emploi du temps (nettoyée).
    ANNÉE : 2026.

    RÈGLES CRITIQUES :
    1. **LIS TOUT** : Extrais le texte de tous les cours visibles.
    2. **MENTIONNE LE GROUPE** : Si tu vois "/GA", "/GC" ou "/GB" écrit dans la case, ÉCRIS-LE dans le champ 'summary'. C'est vital.
    3. **LIGNES DOUBLES** : Si une journée est coupée en deux lignes :
       - La ligne du HAUT contient souvent "/GA" ou "/GC".
       - La ligne du BAS contient souvent "/GB".
       - Essaie de lire la ligne du bas en priorité, mais renvoie tout ce que tu vois. Je filtrerai après.
    
    4. **EXAMENS** : Les cases sur fond JAUNE sont des EXAMENS. Ajoute "[EXAMEN]" dans le titre si le fond est jaune.

    5. **HORAIRES** :
       - Col 1 : 07h45-09h45
       - Col 2 : 10h00-12h00
       - Col 3 : 13h30-15h30 (Début 13h30 strict)
       - Col 4 : 15h45-17h45

    SORTIE JSON :
    [
      {{ "date": "2026-MM-JJ", "summary": "Matière /Groupe (Prof)", "start": "HH:MM", "end": "HH:MM", "location": "Salle" }}
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

def get_schedule_robust(image):
    # Prétraitement couleur agressif
    cleaned_img = preprocess_image(image)
    
    models = ["gemini-1.5-pro", "gemini-1.5-pro-latest", "gemini-2.0-flash", "gemini-flash-latest"]
    
    for model in models:
        print(f"   👉 Lecture avec : {model}...")
        try:
            response = call_gemini_api(cleaned_img, model)
            if response.status_code == 200:
                raw = response.json()
                if 'candidates' in raw and raw['candidates']:
                    clean = clean_json_text(raw['candidates'][0]['content']['parts'][0]['text'])
                    return json.loads(clean)
            elif response.status_code in [429, 503]:
                print(f"      ⚠️ Surcharge ({response.status_code}). Suivant...")
                continue
        except Exception:
            continue
    return []

def filter_events_python(events):
    """
    Filtre ultime en Python : On supprime les cours qui ne concernent pas GB.
    """
    final_events = []
    print(f"   🧹 Filtrage Python de {len(events)} événements...")
    
    for evt in events:
        summary = evt.get('summary', '').upper()
        
        # 1. Suppression des groupes interdits (GA, GC, G1)
        # On vérifie si "/GA", "/GC" sont présents.
        # Attention : Parfois "CPO (GA)" ou "Informatique /GC"
        if "/GC" in summary or "/GA" in summary or "(GA)" in summary or "(GC)" in summary:
            print(f"      🗑️ Rejeté (Groupe incorrect) : {summary}")
            continue
            
        # 2. On garde le reste (GB ou rien)
        final_events.append(evt)
        
    print(f"   ✅ Reste {len(final_events)} événements valides.")
    return final_events

def create_ics_file(events):
    ics = [
        "BEGIN:VCALENDAR",
        "VERSION:2.0",
        "PRODID:-//STRI//Groupe GB//FR",
        "CALSCALE:GREGORIAN",
        "METHOD:PUBLISH"
    ]
    for evt in events:
        try:
            d = evt['date'].replace('-', '')
            if d.startswith("2025"): d = d.replace("2025", "2026", 1)
            s = evt['start'].replace(':', '') + "00"
            e = evt['end'].replace(':', '') + "00"
            
            summary = evt.get('summary', 'Cours')
            
            # Gestion Priorité Examen
            priority = "5"
            if "EXAMEN" in summary.upper():
                summary = "🔴 " + summary
                priority = "1"

            ics.append("BEGIN:VEVENT")
            ics.append(f"DTSTART:{d}T{s}")
            ics.append(f"DTEND:{d}T{e}")
            ics.append(f"SUMMARY:{summary}")
            ics.append(f"LOCATION:{evt.get('location', '')}")
            ics.append(f"PRIORITY:{priority}")
            ics.append("DESCRIPTION:Groupe GB")
            ics.append("END:VEVENT")
        except: continue
    ics.append("END:VCALENDAR")
    return "\n".join(ics)

def main():
    if not API_KEY: raise Exception("Clé API manquante")

    print("Téléchargement PDF...")
    response = requests.get(PDF_URL)
    
    # 400 DPI : Haute résolution pour bien voir la séparation des lignes
    print("Conversion PDF -> Images (400 DPI)...")
    images = convert_from_bytes(response.content, dpi=400) 

    all_events = []
    print(f"Traitement de {len(images)} pages...")
    for i, img in enumerate(images):
        print(f"--- Analyse Page {i+1} ---")
        raw_events = get_schedule_robust(img)
        
        # Filtrage Python strict
        valid_events = filter_events_python(raw_events)
        
        if valid_events:
            all_events.extend(valid_events)
        else:
            print("❌ Aucun cours valide trouvé sur cette page.")

    print("Génération ICS...")
    ics_content = create_ics_file(all_events)

    with open(OUTPUT_FILE, "w", encoding="utf-8") as f:
        f.write(ics_content)
    print(f"Terminé. Fichier généré : {OUTPUT_FILE}")

if __name__ == "__main__":
    main()
