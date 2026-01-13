import os
import io
import json
import base64
import requests
import re
from pdf2image import convert_from_bytes
from datetime import datetime

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

def get_dynamic_model_name():
    """Interroge l'API pour trouver le modèle disponible pour cette clé."""
    url = f"https://generativelanguage.googleapis.com/v1beta/models?key={API_KEY}"
    try:
        response = requests.get(url)
        if response.status_code != 200:
            print(f"⚠️ Impossible de lister les modèles ({response.status_code}).")
            return "gemini-1.5-flash" # Fallback risqué mais nécessaire si l'API models échoue

        data = response.json()
        # On récupère juste les noms (ex: "models/gemini-pro")
        available_models = [m['name'].replace('models/', '') for m in data.get('models', []) if 'generateContent' in m.get('supportedGenerationMethods', [])]
        
        print(f"📋 Modèles disponibles pour votre clé : {available_models}")

        # Ordre de préférence (du plus performant/récent au plus vieux)
        preferences = [
            "gemini-1.5-pro-latest",
            "gemini-1.5-pro",
            "gemini-1.5-pro-001",
            "gemini-1.5-flash-latest",
            "gemini-1.5-flash",
            "gemini-1.5-flash-001",
            "gemini-2.0-flash-exp",
            "gemini-1.0-pro-vision-latest", # Vieux modèle vision
            "gemini-pro-vision"
        ]

        for pref in preferences:
            if pref in available_models:
                print(f"✅ Modèle sélectionné : {pref}")
                return pref
        
        # Si aucun favori n'est trouvé, on prend le premier qui a "gemini"
        if available_models:
            return available_models[0]
            
        return "gemini-1.5-flash"

    except Exception as e:
        print(f"Erreur détection modèle : {e}")
        return "gemini-1.5-flash"

def clean_json_text(text):
    text = re.sub(r"```json|```", "", text).strip()
    start = text.find('[')
    end = text.rfind(']')
    if start != -1 and end != -1:
        return text[start:end+1]
    return text

def get_schedule_from_gemini(image, model_name):
    url = f"https://generativelanguage.googleapis.com/v1beta/models/{model_name}:generateContent?key={API_KEY}"
    
    img_byte_arr = io.BytesIO()
    image.save(img_byte_arr, format='JPEG')
    b64_data = base64.b64encode(img_byte_arr.getvalue()).decode('utf-8')

    current_year = datetime.now().year
    
    prompt = f"""
    Analyse l'emploi du temps (image) pour le groupe "GB".
    
    RÈGLES VISUELLES :
    1. Si 2 lignes pour une journée, ignore la ligne du haut.
    2. Ignore les cours "/GC". Garde "/GB" ou sans groupe.
    3. Ignore les cases ORANGE.
    4. Horaires: Lignes verticales = 15min. Début 7h45.
    5. Profs: {PROFS_DICT}

    SORTIE JSON UNIQUEMENT :
    [
        {{
            "summary": "Matière (Prof)",
            "start": "YYYY-MM-DDTHH:MM:00",
            "end": "YYYY-MM-DDTHH:MM:00",
            "location": "Salle"
        }}
    ]
    Utilise l'année {current_year} ou {current_year+1}.
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

    try:
        response = requests.post(url, headers={'Content-Type': 'application/json'}, data=json.dumps(payload))
        
        if response.status_code != 200:
            print(f"⚠️ Erreur API ({response.status_code}): {response.text}")
            return []

        raw_resp = response.json()
        if 'candidates' not in raw_resp or not raw_resp['candidates']:
            return []
            
        clean_text = clean_json_text(raw_resp['candidates'][0]['content']['parts'][0]['text'])
        return json.loads(clean_text)
    except Exception as e:
        print(f"Erreur parsing: {e}")
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
            s = evt['start'].replace('-', '').replace(':', '')
            e = evt['end'].replace('-', '').replace(':', '')
            ics.append("BEGIN:VEVENT")
            ics.append(f"DTSTART:{s}")
            ics.append(f"DTEND:{e}")
            ics.append(f"SUMMARY:{evt.get('summary', 'Cours')}")
            ics.append(f"LOCATION:{evt.get('location', '')}")
            ics.append("DESCRIPTION:Groupe GB")
            ics.append("END:VEVENT")
        except: continue
    ics.append("END:VCALENDAR")
    return "\n".join(ics)

def main():
    if not API_KEY: raise Exception("Clé API manquante")

    # 1. On trouve le BON modèle
    model_name = get_dynamic_model_name()
    print(f"🚀 Démarrage avec le modèle : {model_name}")

    print("Téléchargement PDF...")
    response = requests.get(PDF_URL)
    images = convert_from_bytes(response.content, dpi=400) # Haute qualité requise

    all_events = []
    print(f"Traitement de {len(images)} pages...")
    
    for i, img in enumerate(images):
        print(f"Analyse Page {i+1}...")
        events = get_schedule_from_gemini(img, model_name)
        if events:
            print(f"✅ {len(events)} cours trouvés.")
            all_events.extend(events)
        else:
            print("❌ Rien trouvé sur cette page.")

    with open(OUTPUT_FILE, "w", encoding="utf-8") as f:
        f.write(create_ics_file(all_events))
    print("Fini.")

if __name__ == "__main__":
    main()
