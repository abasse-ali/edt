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

PROFS_DICT = """
AnAn=Andréi ANDRÉI; AA=André AOUN; AB=Abdelmalek BENZEKRI; AL=Abir LARABA; BC=Bilal CHEBARO; 
BTJ=Boris TIOMELA JOU; CC=Cédric CHAMBAULT; CG=Christine GALY; CT=Cédric TEYSSIE; EG=Eric GONNEAU; 
EL=Emmanuel LAVINAL; FM=Frédéric MOUTIER; GR=Gérard ROUZIES; JGT=Jean-Guy TARTARIN; JS=Jérôme SOKOLOFF; 
KB=Ketty BRAVO; LC=Louisa COT; MCL=Marie-Christine LAGASQUIÉ; MM=MUSTAPHA MOJAHID; OC=Olivier CRIVELLARO; 
OM=Olfa MECHI; PA=Patrick AUSTIN; PhA=Philippe ARGUEL; PIL=Pierre LOTTE; PL=Philippe LATU; PT=Patrice TORGUET; 
RK=Rahim KACIMI; RL=Romain LABORDE; SB=Sonia BADENE; SL=Séverine LALANDE; TD=Thierry DESPRATS; TG=Thierry GAYRAUD.
"""

def get_best_model_name():
    """Interroge l'API pour trouver le modèle le plus intelligent disponible."""
    url = f"https://generativelanguage.googleapis.com/v1beta/models?key={API_KEY}"
    try:
        response = requests.get(url)
        if response.status_code != 200:
            print("⚠️ Impossible de lister les modèles. Utilisation du défaut.")
            return "gemini-1.5-flash"

        data = response.json()
        # On nettoie les noms (enlève 'models/')
        available_models = [m['name'].replace('models/', '') for m in data.get('models', [])]
        print(f"📋 Modèles disponibles pour votre clé : {available_models}")

        # ORDRE DE PRÉFÉRENCE (Intelligence décroissante)
        # On veut absolument un modèle 'Pro' pour la lecture complexe, sinon Flash
        preferences = [
            "gemini-1.5-pro",          # Le top pour la logique
            "gemini-1.5-pro-latest",
            "gemini-1.5-pro-001",
            "gemini-pro",              # Vieux Pro
            "gemini-flash-latest",     # Flash stable
            "gemini-1.5-flash",
            "gemini-2.0-flash-exp"     # Expérimental
        ]

        for pref in preferences:
            if pref in available_models:
                print(f"✅ Modèle SÉLECTIONNÉ : {pref}")
                return pref
        
        # Fallback : le premier qui contient 'flash'
        for m in available_models:
            if 'flash' in m:
                return m
                
        return "gemini-1.5-flash"

    except Exception as e:
        print(f"Erreur recherche modèle : {e}")
        return "gemini-1.5-flash"

def clean_json_text(text):
    text = re.sub(r"```json|```", "", text).strip()
    start = text.find('{')
    end = text.rfind('}')
    if start != -1 and end != -1:
        return text[start:end+1]
    return text

def call_gemini_api(image, model_name):
    url = f"https://generativelanguage.googleapis.com/v1beta/models/{model_name}:generateContent?key={API_KEY}"
    
    img_byte_arr = io.BytesIO()
    image.save(img_byte_arr, format='JPEG')
    b64_data = base64.b64encode(img_byte_arr.getvalue()).decode('utf-8')

    # PROMPT STRUCTURE PAR JOUR (Pour éviter le mélange des dates)
    prompt = f"""
    Tu es un expert en lecture d'emploi du temps.
    ANALYSE L'IMAGE POUR LE GROUPE "GB".
    ANNÉE : 2026.

    RÈGLES DE LECTURE CRITIQUES :
    1. **SÉPARATION DES JOURS** : Lis le tableau ligne par ligne. Ne mélange pas les cours du Lundi avec le Mardi.
    2. **FILTRE COULEUR** : Si une case est ORANGE/JAUNE/GRIS -> IGNORE-LA TOTALEMENT.
    3. **FILTRE GROUPE** : Garde uniquement "/GB" ou sans groupe. Jette "/GC", "/GA".
    4. **HORAIRES** :
       - Colonne 1 : ~07h45-09h45
       - Colonne 2 : ~10h00-12h00
       - Colonne 3 : ~13h45-15h45
       - Colonne 4 : ~15h45-17h45

    FORMAT DE SORTIE (JSON Dictionnaire) :
    {{
      "2026-01-12": [
         {{ "summary": "Matière (Prof)", "start": "07:45", "end": "09:45", "location": "Salle" }}
      ],
      "2026-01-13": [ ... ]
    }}
    Remplace les profs par : {PROFS_DICT}
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
    # 1. On trouve le BON modèle dispo
    model_name = get_best_model_name()
    
    # 2. On essaie 3 fois en cas d'erreur de quota
    for attempt in range(3):
        try:
            print(f"   👉 Tentative {attempt+1} avec {model_name}...")
            response = call_gemini_api(image, model_name)

            if response.status_code == 200:
                raw_resp = response.json()
                if 'candidates' in raw_resp and raw_resp['candidates']:
                    clean = clean_json_text(raw_resp['candidates'][0]['content']['parts'][0]['text'])
                    return json.loads(clean)
                else:
                    print("      ⚠️ Réponse vide (IA muette).")
            
            elif response.status_code in [429, 503]:
                wait = (attempt + 1) * 20
                print(f"      ⚠️ Surcharge ({response.status_code}). Pause {wait}s...")
                time.sleep(wait)
                continue
            else:
                print(f"      ❌ Erreur {response.status_code}. Stop.")
                return {}

        except Exception as e:
            print(f"      ❌ Exception : {e}")
            return {}
            
    return {}

def create_ics_file(grouped_events):
    ics = [
        "BEGIN:VCALENDAR",
        "VERSION:2.0",
        "PRODID:-//STRI//Groupe GB//FR",
        "CALSCALE:GREGORIAN",
        "METHOD:PUBLISH"
    ]
    
    for date_str, courses in grouped_events.items():
        for evt in courses:
            try:
                # Format ICS : YYYYMMDDTHHMMSS
                d_clean = date_str.replace('-', '')
                s_clean = evt['start'].replace(':', '') + "00"
                e_clean = evt['end'].replace(':', '') + "00"

                ics.append("BEGIN:VEVENT")
                ics.append(f"DTSTART:{d_clean}T{s_clean}")
                ics.append(f"DTEND:{d_clean}T{e_clean}")
                ics.append(f"SUMMARY:{evt.get('summary', 'Cours')}")
                ics.append(f"LOCATION:{evt.get('location', '')}")
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

    all_grouped_events = {}

    print(f"Traitement de {len(images)} pages...")
    for i, img in enumerate(images):
        print(f"--- Analyse Page {i+1} ---")
        page_events = get_schedule_robust(img)
        
        if page_events:
            print(f"✅ Jours trouvés : {list(page_events.keys())}")
            all_grouped_events.update(page_events)
        else:
            print("❌ Echec lecture page.")

    print("Génération ICS...")
    ics_content = create_ics_file(all_grouped_events)

    with open(OUTPUT_FILE, "w", encoding="utf-8") as f:
        f.write(ics_content)
    
    print(f"Terminé. Fichier généré : {OUTPUT_FILE}")

if __name__ == "__main__":
    main()
