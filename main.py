import os
import io
import json
import base64
import requests
import re
import time
from pdf2image import convert_from_bytes

# --- CONFIGURATION ---
PDF_URL = "https://stri.fr/Gestion_STRI/TAV/L3/EDT_STRI1A_L3IRT_TAV.pdf"
OUTPUT_FILE = "emploi_du_temps.ics"
API_KEY = os.environ.get("GEMINI_API_KEY")

# Mapping des professeurs
PROFS_DICT = """
AnAn=Andréi ANDRÉI; AA=André AOUN; AB=Abdelmalek BENZEKRI; AL=Abir LARABA; BC=Bilal CHEBARO; 
BTJ=Boris TIOMELA JOU; CC=Cédric CHAMBAULT; CG=Christine GALY; CT=Cédric TEYSSIE; EG=Eric GONNEAU; 
EL=Emmanuel LAVINAL; FM=Frédéric MOUTIER; GR=Gérard ROUZIES; JGT=Jean-Guy TARTARIN; JS=Jérôme SOKOLOFF; 
KB=Ketty BRAVO; LC=Louisa COT; MCL=Marie-Christine LAGASQUIÉ; MM=MUSTAPHA MOJAHID; OC=Olivier CRIVELLARO; 
OM=Olfa MECHI; PA=Patrick AUSTIN; PhA=Philippe ARGUEL; PIL=Pierre LOTTE; PL=Philippe LATU; PT=Patrice TORGUET; 
RK=Rahim KACIMI; RL=Romain LABORDE; SB=Sonia BADENE; SL=Séverine LALANDE; TD=Thierry DESPRATS; TG=Thierry GAYRAUD.
"""

def get_available_models():
    """Récupère les modèles disponibles."""
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

def call_gemini_api(image, model_name):
    url = f"https://generativelanguage.googleapis.com/v1beta/models/{model_name}:generateContent?key={API_KEY}"
    
    img_byte_arr = io.BytesIO()
    image.save(img_byte_arr, format='JPEG')
    b64_data = base64.b64encode(img_byte_arr.getvalue()).decode('utf-8')

    # PROMPT : On demande à l'IA de qualifier chaque cours (Couleur, Position, Groupe)
    # On délègue le filtrage au Python.
    prompt = f"""
    Tu es un assistant de vision par ordinateur.
    TACHE : Extrais TOUS les rectangles de cours visibles sur l'image.
    ANNÉE : 2026.

    POUR CHAQUE COURS, TU DOIS DÉTECTER 3 ATTRIBUTS VISUELS :
    1. **background_color** : "ORANGE" (si fond orange/saumon), "JAUNE" (si fond jaune vif), "BLANC" (sinon).
    2. **vertical_position** : "HAUT" (si le cours est sur la demi-ligne supérieure d'une journée divisée), "BAS" (si sur la demi-ligne inférieure), "UNIQUE" (si la ligne n'est pas divisée).
    3. **text_content** : Le texte exact écrit (Matière, Prof, Salle, Groupe ex: /GC, /GA, /GB).

    RÈGLES HORAIRES :
    - Colonne 1 : 07h45 - 09h45
    - Colonne 2 : 10h00 - 12h00
    - Colonne 3 : 13h30 - 15h30
    - Colonne 4 : 15h45 - 17h45

    FORMAT DE SORTIE (JSON STRICT) :
    [
      {{
        "date": "2026-MM-JJ",
        "summary": "Matière (Prof)",
        "start": "HH:MM",
        "end": "HH:MM",
        "location": "Salle",
        "background_color": "ORANGE/JAUNE/BLANC",
        "vertical_position": "HAUT/BAS/UNIQUE",
        "group_tag": "/GB ou /GC ou /GA ou AUCUN"
      }}
    ]
    Remplace les noms de profs selon : {PROFS_DICT}
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
    available = get_available_models()
    # Priorité aux modèles intelligents pour bien détecter la position HAUT/BAS
    priority_list = [
        "gemini-1.5-pro",
        "gemini-1.5-pro-latest",
        "gemini-2.0-flash",
        "gemini-flash-latest"
    ]
    models_to_try = [m for m in priority_list if m in available]
    if not models_to_try: models_to_try = ["gemini-1.5-flash"]

    print(f"   📋 Modèles testés : {models_to_try}")

    for model in models_to_try:
        print(f"   👉 Appel API avec {model}...")
        try:
            response = call_gemini_api(image, model)
            if response.status_code == 200:
                raw = response.json()
                if 'candidates' in raw and raw['candidates']:
                    clean = clean_json_text(raw['candidates'][0]['content']['parts'][0]['text'])
                    data = json.loads(clean)
                    print(f"      ✅ Reçu {len(data)} objets bruts.")
                    return data
            elif response.status_code in [429, 503]:
                print(f"      ⚠️ Surcharge {response.status_code}. Suivant...")
                continue
            else:
                print(f"      ❌ Erreur {response.status_code}.")
        except Exception as e:
            print(f"      ❌ Exception : {e}")
            continue
    return []

def filter_events_strict(events):
    """
    C'est ICI que toute la magie opère. On filtre brutalement via le code.
    """
    valid_events = []
    print(f"   🧹 Filtrage de {len(events)} événements bruts...")
    
    for evt in events:
        summary = evt.get('summary', '').upper()
        bg_color = evt.get('background_color', 'BLANC').upper()
        position = evt.get('vertical_position', 'UNIQUE').upper()
        group_tag = evt.get('group_tag', '').upper()

        # RÈGLE 1 : Couleur ORANGE = POUBELLE
        if "ORANGE" in bg_color:
            print(f"      🗑️ Rejet (Couleur Orange) : {summary}")
            continue
            
        # RÈGLE 2 : Position HAUT = POUBELLE (sauf si explicitement marqué GB)
        if position == "HAUT" and "/GB" not in group_tag and "GB" not in summary:
            print(f"      🗑️ Rejet (Position Haut/GA) : {summary}")
            continue

        # RÈGLE 3 : Tag de groupe explicite
        if "/GA" in group_tag or "/GC" in group_tag or "(GA)" in summary or "(GC)" in summary:
            print(f"      🗑️ Rejet (Groupe GA/GC) : {summary}")
            continue

        # Si on arrive ici, c'est bon !
        # On nettoie le titre (on enlève les mentions de position inutiles)
        valid_events.append(evt)

    print(f"   ✅ {len(valid_events)} événements conservés pour l'agenda.")
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
            bg_color = evt.get('background_color', '').upper()
            
            # Gestion EXAMEN (Si Jaune ou mention explicite)
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
    
    # 300 DPI est suffisant si on ne fait pas de pixel-art
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
