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
    """Sélectionne le meilleur modèle disponible (2.0/2.5 > Pro > Flash)."""
    url = f"https://generativelanguage.googleapis.com/v1beta/models?key={API_KEY}"
    try:
        response = requests.get(url)
        if response.status_code != 200:
            return "gemini-1.5-flash"

        data = response.json()
        available = [m['name'].replace('models/', '') for m in data.get('models', [])]
        print(f"📋 Modèles dispo : {available}")

        # ORDRE DE PRIORITÉ BASÉ SUR VOS LOGS
        preferences = [
            "gemini-2.0-flash",       # Excellent compromis vitesse/intelligence
            "gemini-2.5-flash",       # Nouvelle génération
            "gemini-1.5-pro",         # Très intelligent
            "gemini-1.5-pro-latest",
            "gemini-flash-latest"     # Fallback
        ]

        for pref in preferences:
            if pref in available:
                print(f"✅ Modèle CHOISI : {pref}")
                return pref
        
        return "gemini-1.5-flash"

    except Exception:
        return "gemini-1.5-flash"

def clean_json_text(text):
    # On cherche le premier '[' et le dernier ']'
    start = text.find('[')
    end = text.rfind(']')
    if start != -1 and end != -1:
        return text[start:end+1]
    # Si échec, on tente de nettoyer le markdown
    text = re.sub(r"```json|```", "", text).strip()
    return text

def call_gemini_api(image, model_name):
    url = f"https://generativelanguage.googleapis.com/v1beta/models/{model_name}:generateContent?key={API_KEY}"
    
    img_byte_arr = io.BytesIO()
    image.save(img_byte_arr, format='JPEG')
    b64_data = base64.b64encode(img_byte_arr.getvalue()).decode('utf-8')

    prompt = f"""
    Analyse l'emploi du temps pour le groupe "GB".
    ANNÉE : 2026 (Force cette année).

    RÈGLES VISUELLES :
    1. **LIGNES COUPÉES** : Si une ligne de jour est divisée en deux (Haut/Bas) :
       - HAUT = Groupe GA/G1 -> IGNORE.
       - BAS = Groupe GB/G2 -> LIS CE COURS.
    2. **FILTRE COULEUR** : IGNORE les cases ORANGES/JAUNES (Examens/Admin). Lis les blanches.
    3. **FILTRE GROUPE** : Garde uniquement "/GB" ou sans groupe. Ignore "/GC".
    4. **HORAIRES** :
       - Matin : 07h45-09h45 et 10h00-12h00.
       - Après-midi : **13h30**-15h30 et 15h45-17h45.
       (Attention : l'après-midi commence souvent à la 2ème graduation après 13h).

    FORMAT DE SORTIE : Une LISTE JSON unique contenant tous les cours de la page.
    [
      {{
        "date": "2026-MM-JJ",
        "summary": "Matière (Prof)",
        "start": "HH:MM",
        "end": "HH:MM",
        "location": "Salle"
      }}
    ]
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
    model_name = get_best_model_name()
    
    # 3 Tentatives en cas de crash
    for attempt in range(3):
        try:
            print(f"   👉 Tentative {attempt+1} avec {model_name}...")
            response = call_gemini_api(image, model_name)

            if response.status_code == 200:
                raw_resp = response.json()
                if 'candidates' in raw_resp and raw_resp['candidates']:
                    # Nettoyage robuste pour éviter l'erreur "Extra data"
                    clean = clean_json_text(raw_resp['candidates'][0]['content']['parts'][0]['text'])
                    return json.loads(clean)
                else:
                    print("      ⚠️ Réponse vide.")
            
            elif response.status_code in [429, 503]:
                wait = (attempt + 1) * 20
                print(f"      ⚠️ Surcharge ({response.status_code}). Pause {wait}s...")
                time.sleep(wait)
                continue
            else:
                print(f"      ❌ Erreur {response.status_code}.")
                return []

        except Exception as e:
            print(f"      ❌ Erreur technique : {e}")
            # Si erreur JSON, on réessaie peut-être que l'IA fera mieux la prochaine fois
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
            # evt['date'] = "2026-01-12", evt['start'] = "13:30"
            d_clean = evt['date'].replace('-', '')
            s_clean = evt['start'].replace(':', '') + "00"
            e_clean = evt['end'].replace(':', '') + "00"
            
            # Sécurité 2026
            if d_clean.startswith("2025"): d_clean = d_clean.replace("2025", "2026", 1)

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
    
    # 300 DPI pour la précision
    print("Conversion PDF -> Images (300 DPI)...")
    images = convert_from_bytes(response.content, dpi=300) 

    all_events = []

    print(f"Traitement de {len(images)} pages...")
    for i, img in enumerate(images):
        print(f"--- Analyse Page {i+1} ---")
        page_events = get_schedule_robust(img)
        
        if page_events:
            print(f"✅ {len(page_events)} cours trouvés.")
            all_events.extend(page_events)
        else:
            print("❌ Echec lecture page.")

    print("Génération ICS...")
    ics_content = create_ics_file(all_events)

    with open(OUTPUT_FILE, "w", encoding="utf-8") as f:
        f.write(ics_content)
    
    print(f"Terminé. Fichier généré : {OUTPUT_FILE}")

if __name__ == "__main__":
    main()
