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

    # --- PROMPT RESTRUCTURÉ POUR ÉVITER LES ERREURS DE JOURS ---
    prompt = f"""
    Tu es un robot de lecture d'emploi du temps. Analyse cette image pixel par pixel.

    CONTEXTE :
    - Année : **2026** (Force cette année).
    - Cible : Groupe **"GB"**.

    ALGORITHME DE LECTURE (A SUIVRE STRICTEMENT) :
    1. **REPÉRAGE DES LIGNES (JOURS)** :
       - L'image est un tableau. Chaque ligne commence par un Jour (ex: "Lundi 12/janv").
       - Repère le jour à gauche. TOUS les cours situés à droite sur cette même ligne horizontale appartiennent à CE JOUR précis. Ne mélange pas les lignes.
       - Si un jour a deux sous-lignes : **IGNORE** la sous-ligne du haut. Lis uniquement celle du BAS.

    2. **FILTRE COULEUR (CRITIQUE)** :
       - Regarde le fond des cases.
       - Si le fond est **ORANGE** (ou jaune foncé/grisâtre) : **C'EST INTERDIT**. Jette ce cours immédiatement. Ne l'inclus pas.
       - Si le fond est BLANC ou clair : C'est OK.

    3. **FILTRE GROUPE** :
       - Garde uniquement "/GB" ou les cours sans mention de groupe.
       - Jette "/GC", "/GA".

    4. **HORAIRES** :
       - Les colonnes représentent les heures.
       - Les traits verticaux marquent les 15 minutes.
       - Début journée : 07h45.
       - Calcule le début et la fin en fonction de la position horizontale de la case.

    5. **PROFS** : {PROFS_DICT}

    SORTIE JSON STRICTE :
    [
        {{
            "summary": "Matière (Prof)",
            "start": "2026-MM-JJTHH:MM:00",
            "end": "2026-MM-JJTHH:MM:00",
            "location": "Salle"
        }}
    ]
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
    # On garde gemini-1.5-flash en priorité car il respecte mieux les instructions système complexes que les modèles 'lite'
    models_to_try = [
        "gemini-1.5-flash",
        "gemini-flash-latest",
        "gemini-2.0-flash",
        "gemini-1.5-pro"
    ]

    for model in models_to_try:
        print(f"   Tentative avec le modèle : {model}...")
        for attempt in range(3):
            try:
                response = call_gemini_api(image, model)

                if response.status_code == 200:
                    raw_resp = response.json()
                    if 'candidates' in raw_resp and raw_resp['candidates']:
                        clean = clean_json_text(raw_resp['candidates'][0]['content']['parts'][0]['text'])
                        return json.loads(clean)
                    else:
                        print("      Réponse vide.")
                
                elif response.status_code in [429, 503]:
                    wait = (attempt + 1) * 10
                    print(f"      Surcharge ({response.status_code}). Attente {wait}s...")
                    time.sleep(wait)
                    continue
                else:
                    print(f"      Erreur {response.status_code}. Suivant.")
                    break 

            except Exception as e:
                print(f"      Exception : {e}")
                break
        
    print("ECHEC : Aucun modèle n'a réussi.")
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
            
            # Sécurité année 2026
            if s.startswith("2025"): s = s.replace("2025", "2026", 1)
            if e.startswith("2025"): e = e.replace("2025", "2026", 1)

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

    print("Téléchargement PDF...")
    response = requests.get(PDF_URL)
    
    # 300 DPI pour bien voir les couleurs (Orange vs Blanc)
    print("Conversion PDF -> Images (300 DPI)...")
    images = convert_from_bytes(response.content, dpi=300) 

    all_events = []
    print(f"Traitement de {len(images)} pages...")
    
    for i, img in enumerate(images):
        print(f"--- Analyse Page {i+1} ---")
        events = get_schedule_robust(img)
        if events:
            print(f"{len(events)} cours trouvés.")
            all_events.extend(events)
        else:
            print("Aucun cours trouvé.")

    with open(OUTPUT_FILE, "w", encoding="utf-8") as f:
        f.write(create_ics_file(all_events))
    
    print(f"Terminé. Fichier généré : {OUTPUT_FILE}")

if __name__ == "__main__":
    main()
