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

def get_available_models():
    """Récupère la liste de TOUS les modèles disponibles pour votre clé."""
    url = f"https://generativelanguage.googleapis.com/v1beta/models?key={API_KEY}"
    try:
        response = requests.get(url)
        if response.status_code != 200:
            return []
        data = response.json()
        return [m['name'].replace('models/', '') for m in data.get('models', [])]
    except:
        return []

def clean_json_text(text):
    start = text.find('[')
    end = text.rfind(']')
    if start != -1 and end != -1:
        return text[start:end+1]
    text = re.sub(r"```json|```", "", text).strip()
    return text

def call_gemini_api(image, model_name):
    url = f"https://generativelanguage.googleapis.com/v1beta/models/{model_name}:generateContent?key={API_KEY}"
    
    img_byte_arr = io.BytesIO()
    image.save(img_byte_arr, format='JPEG')
    b64_data = base64.b64encode(img_byte_arr.getvalue()).decode('utf-8')

    prompt = f"""
    Analyse l'emploi du temps (Groupe GB).
    ANNÉE : 2026.

    RÈGLES VISUELLES :
    1. **LIGNES** : Si une journée a 2 lignes, IGNORE celle du HAUT (GA). LIS celle du BAS (GB).
    2. **COULEUR** : IGNORE les cases ORANGES. Lis les BLANCHES.
    3. **GROUPE** : Garde uniquement "/GB" ou sans groupe.
    4. **HORAIRES** :
       - Matin : 07h45-09h45 / 10h00-12h00
       - Aprèm : 13h30-15h30 / 15h45-17h45 (décalage possible)

    FORMAT DE SORTIE (Liste JSON) :
    [
      {{
        "date": "2026-MM-JJ",
        "summary": "Matière (Prof)",
        "start": "HH:MM",
        "end": "HH:MM",
        "location": "Salle"
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
    # 1. On récupère ce qu'on a le droit d'utiliser
    available = get_available_models()
    
    # 2. LISTE DE PRIORITÉ (Cascading Failover)
    # On commence par le plus intelligent (2.0), si ça bloque -> 1.5 Flash (Fiable) -> etc.
    priority_list = [
        "gemini-2.0-flash",       # Intelligent mais quota faible
        "gemini-1.5-flash",       # Le "tank" : quota énorme (15 RPM)
        "gemini-flash-latest",    # Alias pour 1.5 Flash
        "gemini-1.5-pro",         # Lent mais précis
        "gemini-2.0-flash-lite-preview-02-05"
    ]

    # On filtre pour ne garder que ceux qui existent vraiment pour votre clé
    models_to_try = [m for m in priority_list if m in available]
    
    # Si la liste est vide (erreur API models), on force une liste par défaut
    if not models_to_try:
        models_to_try = ["gemini-1.5-flash", "gemini-flash-latest"]

    print(f"   📋 Stratégie de secours : {models_to_try}")

    for model in models_to_try:
        print(f"   👉 Tentative avec : {model}...")
        
        # Une seule tentative par modèle (pas de retry si 429, on change direct de modèle)
        try:
            response = call_gemini_api(image, model)

            if response.status_code == 200:
                raw_resp = response.json()
                if 'candidates' in raw_resp and raw_resp['candidates']:
                    clean = clean_json_text(raw_resp['candidates'][0]['content']['parts'][0]['text'])
                    return json.loads(clean)
                else:
                    print("      ⚠️ Réponse vide.")
            
            elif response.status_code == 429:
                print("      ⚠️ Quota dépassé (429). Passage immédiat au modèle suivant...")
                continue # On passe au prochain modèle de la liste
            
            elif response.status_code == 503:
                print("      ⚠️ Surcharge Google (503). Passage au suivant...")
                continue

            else:
                print(f"      ❌ Erreur {response.status_code}.")

        except Exception as e:
            print(f"      ❌ Exception : {e}")
            continue

    print("❌ ECHEC TOTAL : Aucun modèle n'a fonctionné.")
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
            d_clean = evt['date'].replace('-', '')
            s_clean = evt['start'].replace(':', '') + "00"
            e_clean = evt['end'].replace(':', '') + "00"
            
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
