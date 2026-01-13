import os
import io
import json
import base64
import requests
import re
import time
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

def clean_json_text(text):
    text = re.sub(r"```json|```", "", text).strip()
    start = text.find('[')
    end = text.rfind(']')
    if start != -1 and end != -1:
        return text[start:end+1]
    return text

def call_gemini_api(image, model_name):
    """Effectue l'appel API avec gestion des erreurs."""
    url = f"https://generativelanguage.googleapis.com/v1beta/models/{model_name}:generateContent?key={API_KEY}"
    
    img_byte_arr = io.BytesIO()
    image.save(img_byte_arr, format='JPEG')
    b64_data = base64.b64encode(img_byte_arr.getvalue()).decode('utf-8')

    current_year = datetime.now().year
    
    prompt = f"""
    Analyse l'emploi du temps (image) pour le groupe "GB".
    
    RÈGLES :
    1. Si 2 lignes/jour, ignore la ligne du haut.
    2. Ignore les cours "/GC". Garde "/GB" ou sans groupe.
    3. Ignore cases ORANGE.
    4. Horaires: Lignes verticales = 15min. Début 7h45.
    5. Profs: {PROFS_DICT}

    SORTIE JSON STRICTE :
    [
        {{
            "summary": "Matière (Prof)",
            "start": "YYYY-MM-DDTHH:MM:00",
            "end": "YYYY-MM-DDTHH:MM:00",
            "location": "Salle"
        }}
    ]
    Année: {current_year} ou {current_year+1}.
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

    response = requests.post(url, headers={'Content-Type': 'application/json'}, data=json.dumps(payload))
    return response

def get_schedule_robust(image):
    """Essaie plusieurs modèles et plusieurs tentatives."""
    
    # LISTE DE SECOURS (Ordre de préférence)
    # Si le premier est surchargé (503), on passe au suivant.
    models_to_try = [
        "gemini-flash-latest",         # Le standard (souvent surchargé)
        "gemini-2.0-flash-lite-001",   # Très rapide, infrastructure différente
        "gemini-2.0-flash",            # Nouvelle version stable
        "gemini-1.5-flash"             # Le classique
    ]

    for model in models_to_try:
        print(f"   👉 Tentative avec le modèle : {model}...")
        
        # 3 Essais par modèle
        for attempt in range(3):
            try:
                response = call_gemini_api(image, model)

                # Cas de succès
                if response.status_code == 200:
                    raw_resp = response.json()
                    if 'candidates' in raw_resp and raw_resp['candidates']:
                        clean = clean_json_text(raw_resp['candidates'][0]['content']['parts'][0]['text'])
                        return json.loads(clean)
                    else:
                        print("      ⚠️ Réponse vide de l'IA (Retry).")
                
                # Cas Surcharge (503) ou Quota (429) -> On attend et on réessaie
                elif response.status_code in [429, 503]:
                    wait = (attempt + 1) * 10
                    print(f"      ⚠️ Erreur {response.status_code} (Surcharge/Quota). Attente {wait}s...")
                    time.sleep(wait)
                    continue # On réessaie le même modèle
                
                # Autre erreur (404, 400) -> On change de modèle immédiatement
                else:
                    print(f"      ❌ Erreur fatale {response.status_code} avec ce modèle. Passage au suivant.")
                    break # Break la boucle retry pour changer de modèle

            except Exception as e:
                print(f"      ❌ Exception technique : {e}")
                break
        
        # Si on arrive ici sans avoir retourné, c'est que ce modèle a échoué 3 fois.
        print("   ⚠️ Changement de modèle...")

    print("❌ ECHEC TOTAL : Aucun modèle n'a réussi à lire cette page.")
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

    print("Téléchargement PDF...")
    response = requests.get(PDF_URL)
    
    # 150 DPI pour être rapide et léger
    print("Conversion PDF -> Images (Mode Léger)...")
    images = convert_from_bytes(response.content, dpi=150) 

    all_events = []
    print(f"Traitement de {len(images)} pages...")
    
    for i, img in enumerate(images):
        print(f"--- Analyse Page {i+1} ---")
        # Appel de la fonction robuste qui gère les modèles
        events = get_schedule_robust(img)
        
        if events:
            print(f"✅ {len(events)} cours trouvés sur cette page.")
            all_events.extend(events)
        else:
            print("❌ Aucun cours récupéré sur cette page.")

    # Génération ICS même si vide (pour ne pas casser le workflow)
    with open(OUTPUT_FILE, "w", encoding="utf-8") as f:
        f.write(create_ics_file(all_events))
    
    if all_events:
        print(f"🎉 Succès ! {len(all_events)} événements écrits dans {OUTPUT_FILE}")
    else:
        print(f"⚠️ Terminé, mais le fichier est vide (problème persistant sur toutes les tentatives).")

if __name__ == "__main__":
    main()
