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

def get_available_models():
    """Récupère la liste réelle des modèles activés pour votre clé."""
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

def preprocess_image(pil_image):
    """
    Efface chirurgicalement l'orange (info inutile) sans toucher au jaune (examen).
    """
    print("   🎨 Nettoyage des couleurs...")
    img_array = np.array(pil_image)

    # Détection de l'orange : Rouge élevé, Bleu faible, Vert "moyen" (l'orange est ~180, le jaune ~220)
    red_cond = img_array[:, :, 0] > 200
    blue_cond = img_array[:, :, 2] < 180
    green_orange_cond = (img_array[:, :, 1] > 130) & (img_array[:, :, 1] < 205)

    mask_orange = red_cond & blue_cond & green_orange_cond
    img_array[mask_orange] = [255, 255, 255] # On remplace par du blanc

    return Image.fromarray(img_array)

def call_gemini_api(image, model_name):
    url = f"https://generativelanguage.googleapis.com/v1beta/models/{model_name}:generateContent?key={API_KEY}"
    
    img_byte_arr = io.BytesIO()
    image.save(img_byte_arr, format='JPEG')
    b64_data = base64.b64encode(img_byte_arr.getvalue()).decode('utf-8')

    prompt = f"""
    Analyse cette image d'emploi du temps (nettoyée).
    ANNÉE : 2026.

    OBJECTIF : Lister TOUS les cours visibles, sans filtrer. Je filtrerai moi-même ensuite.

    POUR CHAQUE COURS, DÉTECTE CES 3 ATTRIBUTS :
    1. **background** : "JAUNE" (si fond jaune vif), "BLANC" (sinon). (L'orange a été effacé).
    2. **position** : "HAUT" (si demi-ligne supérieure), "BAS" (si demi-ligne inférieure), "UNIQUE" (si ligne entière).
    3. **text** : Le texte complet (Matière, Prof, Groupe ex: /GC, /GA, /GB).

    RÈGLES HORAIRES :
    - Col 1 : 07h45 - 09h45
    - Col 2 : 10h00 - 12h00
    - Col 3 : 13h30 - 15h30 (Début 13h30 strict)
    - Col 4 : 15h45 - 17h45

    FORMAT JSON STRICT :
    [
      {{
        "date": "2026-MM-JJ",
        "summary": "Matière (Prof)",
        "start": "HH:MM",
        "end": "HH:MM",
        "location": "Salle",
        "background": "JAUNE/BLANC",
        "position": "HAUT/BAS/UNIQUE",
        "raw_text": "Texte complet lu"
      }}
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
    # 1. On récupère TOUS les modèles disponibles
    available_in_account = get_available_models()
    
    # 2. LISTE COMPLÈTE DE PRIORITÉ (Du plus intelligent au plus rapide)
    priority_list = [
        # Les modèles "Pro" (Meilleure vision)
        "gemini-2.5-pro",
        "gemini-1.5-pro",
        "gemini-1.5-pro-latest",
        "gemini-1.5-pro-001",
        
        # Les modèles "Flash" récents (Rapides)
        "gemini-2.5-flash",
        "gemini-2.0-flash",
        "gemini-2.0-flash-001",
        "gemini-2.0-flash-lite-preview-02-05",
        
        # Les "Classiques" (Très fiables en quota)
        "gemini-1.5-flash",
        "gemini-flash-latest",
        "gemini-1.5-flash-latest",
        "gemini-1.5-flash-001"
    ]

    # On croise les deux listes pour ne tester que ce qui existe
    models_to_try = [m for m in priority_list if m in available_in_account]
    
    # Si la liste est vide (bug API), on force les classiques
    if not models_to_try:
        models_to_try = ["gemini-1.5-flash", "gemini-flash-latest"]

    print(f"   📋 {len(models_to_try)} modèles prêts à être testés : {models_to_try}")

    # Prétraitement unique
    cleaned_img = preprocess_image(image)

    # BOUCLE DE FAILOVER
    for model in models_to_try:
        print(f"   👉 Tentative avec : {model}...")
        try:
            response = call_gemini_api(cleaned_img, model)

            if response.status_code == 200:
                raw_resp = response.json()
                if 'candidates' in raw_resp and raw_resp['candidates']:
                    clean = clean_json_text(raw_resp['candidates'][0]['content']['parts'][0]['text'])
                    return json.loads(clean)
                else:
                    print("      ⚠️ Réponse vide (IA muette). Suivant...")
                    continue
            
            elif response.status_code in [429, 503]:
                print(f"      ⚠️ Bloqué ({response.status_code}). Suivant immédiat...")
                continue # On passe direct au modèle suivant
            else:
                print(f"      ❌ Erreur {response.status_code}. Suivant...")
                continue

        except Exception as e:
            print(f"      ❌ Exception : {e}. Suivant...")
            continue

    print("❌ ECHEC TOTAL : Aucun modèle n'a fonctionné.")
    return []

def filter_events_strict(events):
    valid_events = []
    print(f"   🧹 Filtrage de {len(events)} événements bruts...")
    
    for evt in events:
        summary = evt.get('summary', '').upper()
        raw_text = evt.get('raw_text', '').upper()
        bg_color = evt.get('background', 'BLANC').upper()
        position = evt.get('position', 'UNIQUE').upper()

        # RÈGLE 1 : Couleur ORANGE = POUBELLE
        # (Normalement déjà effacé par le script, mais double sécurité)
        if "ORANGE" in bg_color:
            continue
            
        # RÈGLE 2 : Groupe Explicitement Interdit dans le texte
        if "/GA" in raw_text or "/GC" in raw_text or "(GA)" in summary or "(GC)" in summary:
            print(f"      🗑️ Rejet (Groupe GA/GC) : {summary}")
            continue

        # RÈGLE 3 : Position HAUT = POUBELLE (sauf si explicitement marqué GB)
        # Si c'est en haut et qu'il n'y a PAS marqué "GB", on jette.
        if position == "HAUT":
            if "/GB" not in raw_text and "GB" not in summary:
                print(f"      🗑️ Rejet (Position Haut sans GB) : {summary}")
                continue

        valid_events.append(evt)

    print(f"   ✅ {len(valid_events)} événements conservés.")
    return valid_events

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
            bg_color = evt.get('background', '').upper()
            
            priority = "5"
            if "JAUNE" in bg_color or "EXAMEN" in summary.upper():
                summary = "🔴 [EXAMEN] " + summary.replace("[EXAMEN] ", "")
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
    
    print("Conversion PDF -> Images (300 DPI)...")
    images = convert_from_bytes(response.content, dpi=300) 

    all_events = []
    print(f"Traitement de {len(images)} pages...")
    for i, img in enumerate(images):
        print(f"--- Analyse Page {i+1} ---")
        raw = get_schedule_robust(img)
        filtered = filter_events_strict(raw)
        all_events.extend(filtered)

    print("Génération ICS...")
    ics_content = create_ics_file(all_events)

    with open(OUTPUT_FILE, "w", encoding="utf-8") as f:
        f.write(ics_content)
    print(f"Terminé. Fichier généré : {OUTPUT_FILE}")

if __name__ == "__main__":
    main()
