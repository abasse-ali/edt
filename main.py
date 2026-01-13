import os
import io
import json
import base64
import requests
import re
import time
import numpy as np
from pdf2image import convert_from_bytes
from PIL import Image, ImageDraw, ImageFont

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
    Traite l'image pour effacer l'Orange et accentuer le Jaune.
    Couleurs Cibles :
    - Orange (Ignore) : #FFB84D -> RGB(255, 184, 77)
    - Jaune (Examen)  : #FFD966 -> RGB(255, 217, 102)
    - Vert (Salle)    : #8BC34A -> RGB(139, 195, 74)
    """
    print("   🎨 Prétraitement des couleurs (Gommage Orange / Marquage Jaune)...")
    
    # Conversion en tableau NumPy pour vitesse
    img_array = np.array(pil_image)
    
    # Définition des couleurs et tolérances (car la conversion PDF->Img altère légèrement les couleurs)
    # On utilise une tolérance de +/- 20 sur chaque canal RGB
    
    # ORANGE : [255, 184, 77]
    orange_lower = np.array([235, 164, 57])
    orange_upper = np.array([255, 204, 97])
    
    # JAUNE : [255, 217, 102]
    yellow_lower = np.array([235, 197, 82])
    yellow_upper = np.array([255, 237, 122])

    # Création des masques
    # Masque Orange : (R >= low & R <= high) & (G >= low ...)
    mask_orange = np.all((img_array >= orange_lower) & (img_array <= orange_upper), axis=-1)
    
    # EFFACEMENT ORANGE : On remplace par du Blanc [255, 255, 255]
    img_array[mask_orange] = [255, 255, 255]

    # Reconversion en image PIL pour ajouter du texte sur le jaune
    clean_image = Image.fromarray(img_array)
    draw = ImageDraw.Draw(clean_image)
    
    # Pour le jaune, on ne l'efface pas, mais on peut aider l'IA en détectant les zones
    # (Optionnel : si le jaune est trop pâle, on pourrait le foncer, mais ici on compte sur le prompt)
    
    return clean_image

def call_gemini_api(image, model_name):
    url = f"https://generativelanguage.googleapis.com/v1beta/models/{model_name}:generateContent?key={API_KEY}"
    
    img_byte_arr = io.BytesIO()
    image.save(img_byte_arr, format='JPEG')
    b64_data = base64.b64encode(img_byte_arr.getvalue()).decode('utf-8')

    prompt = f"""
    Tu es un expert en lecture d'emploi du temps.
    TACHE : Extraire les cours du groupe "GB".
    ANNÉE : 2026.

    CONTEXTE VISUEL :
    J'ai pré-traité l'image pour toi :
    1. Les cours ANNULÉS/ADMIN (Fond Orange) ont été EFFACÉS (blancs). Ignore les trous blancs.
    2. Les cours EXAMENS sont sur fond JAUNE.

    RÈGLES DE LECTURE :
    1. **LECTURE LIGNE PAR LIGNE** : Repère le jour à gauche. Lis tous les cours de cette ligne.
    2. **GROUPES (HAUT/BAS)** :
       - Si une case est divisée horizontalement :
         - HAUT = GA -> IGNORE.
         - BAS = GB -> LIS CE COURS.
       - Si texte centré = Cours commun -> LIS.
    3. **FILTRES** :
       - Garde "/GB" ou sans groupe.
       - Si tu vois un fond JAUNE, ajoute "[EXAMEN]" au début du titre.
    
    4. **HORAIRES** :
       - Col 1 : 07h45-09h45
       - Col 2 : 10h00-12h00
       - Col 3 : 13h30-15h30 (Attention : commence à la 2ème graduation après 13h)
       - Col 4 : 15h45-17h45

    FORMAT JSON LIST :
    [
      {{ "date": "2026-MM-JJ", "summary": "Matière (Prof)", "start": "HH:MM", "end": "HH:MM", "location": "Salle" }}
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
    # On prétraite l'image pour gérer les couleurs
    cleaned_img = preprocess_image(image)
    
    # Liste de modèles avec Failover
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
        except Exception as e:
            print(f"      ❌ Erreur : {e}")
            continue
            
    return []

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
            priority = "5"
            # Détection Examen renforcée
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
    
    # 300 DPI obligatoire pour le filtrage couleur
    print("Conversion PDF -> Images (300 DPI)...")
    images = convert_from_bytes(response.content, dpi=300) 

    all_events = []
    print(f"Traitement de {len(images)} pages...")
    for i, img in enumerate(images):
        print(f"--- Analyse Page {i+1} ---")
        events = get_schedule_robust(img)
        if events:
            print(f"✅ {len(events)} cours trouvés.")
            all_events.extend(events)
        else:
            print("❌ Echec lecture page.")

    print("Génération ICS...")
    ics_content = create_ics_file(all_events)

    with open(OUTPUT_FILE, "w", encoding="utf-8") as f:
        f.write(ics_content)
    print(f"Terminé. Fichier généré : {OUTPUT_FILE}")

if __name__ == "__main__":
    main()
