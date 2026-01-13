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

# Mapping des professeurs (Identique)
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

    # PROMPT RENFORCÉ POUR LES HORAIRES ET L'ANNÉE 2026
    prompt = f"""
    Tu es un expert en extraction de données d'agenda. Analyse cette image pour le groupe "GB".

    RÈGLES ABSOLUES :
    1. **ANNÉE : 2026**. Toutes les dates doivent être en 2026.
    2. **GROUPE** : 
       - GARDE les cours marqués "/GB" ou sans groupe.
       - JETTE les cours marqués "/GC" ou "/GA".
    3. **STRUCTURE VISUELLE** :
       - Si une journée (ligne horizontale) est divisée en deux sous-lignes : IGNORE la ligne du HAUT et les cases ORANGES. Lis UNIQUEMENT la ligne du BAS.
       - Les petits carrés verts en haut à droite des cases sont les SALLES.
    
    4. **HORAIRES PRÉCIS (Attention aux traits verticaux de 15min)** :
       Le début de la journée est à **07h45**.
       Calcule les heures de début et fin selon la grille.
       Les créneaux classiques sont souvent :
       - Matin : 07h45-09h45 ou 10h00-12h00
       - Après-midi : 13h30-15h30 (ou 13h45) ou 15h45-17h45
       *Regarde bien la position des blocs par rapport aux lignes verticales.*

    5. **PROFS** : Remplace les initiales selon : {PROFS_DICT}

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
    """Logique de tentative multiple (Failover) pour contourner les erreurs 429/503."""
    models_to_try = [
        "gemini-1.5-flash",            # Priorité 1 : Le plus stable pour le JSON
        "gemini-flash-latest",         # Priorité 2
        "gemini-2.0-flash-lite-preview-02-05", # Priorité 3
        "gemini-2.0-flash"             # Priorité 4
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
                        print("      Réponse vide (IA muette).")
                
                elif response.status_code in [429, 503]:
                    wait = (attempt + 1) * 10
                    print(f"      Surcharge ({response.status_code}). Attente {wait}s...")
                    time.sleep(wait)
                    continue
                else:
                    print(f"      Erreur {response.status_code}. Passage au modèle suivant.")
                    break 

            except Exception as e:
                print(f"      Exception : {e}")
                break
        
    print("ECHEC : Aucun modèle n'a pu lire cette page.")
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
            # Nettoyage et formatage
            s = evt['start'].replace('-', '').replace(':', '')
            e = evt['end'].replace('-', '').replace(':', '')
            
            # Sécurité année 2026 (si l'IA a quand même mis 2025)
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
    
    # On remonte à 300 DPI pour que l'IA voie bien les traits de 15min
    # Si ça plante (quota), redescendez à 200, mais pas 150.
    print("Conversion PDF -> Images (Qualité Moyenne 300 DPI)...")
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
            print("Aucun cours trouvé sur cette page.")

    with open(OUTPUT_FILE, "w", encoding="utf-8") as f:
        f.write(create_ics_file(all_events))
    
    print(f"Terminé. Fichier généré : {OUTPUT_FILE}")

if __name__ == "__main__":
    main()
