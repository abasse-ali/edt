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
    url = f"https://generativelanguage.googleapis.com/v1beta/models?key={API_KEY}"
    try:
        response = requests.get(url)
        if response.status_code != 200: return []
        return [m['name'].replace('models/', '') for m in response.json().get('models', [])]
    except: return []

def clean_json_text(text):
    # Nettoyage robuste
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

    prompt = f"""
    Tu es un expert en lecture d'emploi du temps complexe.
    TACHE : Extraire les cours pour le groupe "GB".
    ANNÉE : 2026.

    RÈGLES DE LECTURE GÉOMÉTRIQUE (CRITIQUE) :
    1. **LECTURE LIGNE PAR LIGNE** : 
       - Repère le jour à gauche (ex: "Lundi").
       - Lis UNIQUEMENT les cases alignées horizontalement avec ce jour.
       - NE SAUTE PAS de lignes, ne mélange pas les jours.

    2. **SOUS-LIGNES (HAUT/BAS)** :
       - Sur une même journée, il y a souvent deux lignes de cours superposées.
       - Ligne du HAUT = Groupe GA/G1 -> **IGNORER**.
       - Ligne du BAS = Groupe GB/G2 -> **C'EST TA CIBLE (GARDER)**.
       - Si une seule ligne centrée -> Garder (Cours commun).

    3. **COULEURS (ATTENTION)** :
       - Case **JAUNE** = **EXAMEN** -> GARDER IMPÉRATIVEMENT (Ajoute "[EXAMEN]" dans le titre).
       - Case **ORANGE** = INFO ADMIN/ANNULÉ -> **IGNORER/JETER**.
       - Case BLANCHE = COURS NORMAL -> GARDER.

    4. **HORAIRES** :
       - Col 1 : 07h45 - 09h45
       - Col 2 : 10h00 - 12h00
       - Col 3 : **13h30** - 15h30 (Commence à la 2ème graduation après 13h)
       - Col 4 : 15h45 - 17h45

    FORMAT DE SORTIE (JSON LIST) :
    [
      {{
        "date": "2026-MM-JJ",
        "summary": "Matière (Prof)",
        "start": "HH:MM",
        "end": "HH:MM",
        "location": "Salle"
      }}
    ]
    Utilise ce dictionnaire pour les profs : {PROFS_DICT}
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
    
    # NOUVELLE LISTE DE PRIORITÉ (On vise les modèles "Pro" pour l'intelligence spatiale)
    priority_list = [
        "gemini-2.5-pro",         # Le plus puissant (si dispo)
        "gemini-1.5-pro",         # Très bon en layout
        "gemini-1.5-pro-latest",
        "gemini-2.0-flash",       # Rapide mais parfois 429
        "gemini-1.5-flash",       # Le "tank" de secours
        "gemini-flash-latest"
    ]

    models_to_try = [m for m in priority_list if m in available]
    if not models_to_try: models_to_try = ["gemini-1.5-flash", "gemini-flash-latest"]

    print(f"   📋 Stratégie : {models_to_try}")

    for model in models_to_try:
        print(f"   👉 Tentative avec : {model}...")
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
                print("      ⚠️ Quota dépassé (429). Suivant...")
                continue
            elif response.status_code == 503:
                print("      ⚠️ Surcharge (503). Suivant...")
                continue
            else:
                print(f"      ❌ Erreur {response.status_code}.")
                continue

        except Exception as e:
            print(f"      ❌ Exception : {e}")
            continue

    print("❌ ECHEC TOTAL : Aucun modèle n'a réussi.")
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

            # Gestion spécifique EXAMEN
            summary = evt.get('summary', 'Cours')
            if "[EXAMEN]" in summary.upper() or "EXAMEN" in summary.upper():
                priority = "PRIORITY:1" # Haute priorité pour examens
            else:
                priority = "PRIORITY:5"

            ics.append("BEGIN:VEVENT")
            ics.append(f"DTSTART:{d_clean}T{s_clean}")
            ics.append(f"DTEND:{d_clean}T{e_clean}")
            ics.append(f"SUMMARY:{summary}")
            ics.append(f"LOCATION:{evt.get('location', '')}")
            ics.append(priority)
            ics.append("DESCRIPTION:Groupe GB")
            ics.append("END:VEVENT")
        except: continue
    ics.append("END:VCALENDAR")
    return "\n".join(ics)

def main():
    if not API_KEY: raise Exception("Clé API manquante")

    print("Téléchargement PDF...")
    response = requests.get(PDF_URL)
    
    # 300 DPI est nécessaire pour voir les différences de couleur (Jaune vs Orange)
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
