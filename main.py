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
    # Nettoyage agressif des balises markdown
    text = re.sub(r"```json|```", "", text).strip()
    # On cherche le premier '{' et le dernier '}' pour attraper l'objet JSON complet
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

    # --- PROMPT RESTRUCTURÉ : FORMAT "JOUR PAR JOUR" ---
    # On demande un dictionnaire par date pour empêcher le mélange des lignes
    prompt = f"""
    Tu es un expert en lecture de grilles complexes.
    
    TACHE : Analyse cette image pour le groupe "GB".
    ANNÉE CIBLE : 2026 (Toutes les dates doivent être en 2026).

    RÈGLES DE LECTURE SPATIALE (TRES IMPORTANT) :
    1.  **Lecture Ligne par Ligne** : Repère d'abord la DATE dans la colonne de gauche (ex: "Lundi 12/janv").
    2.  **Verrouillage Horizontal** : Une fois la date trouvée, déplace-toi vers la droite sur CETTE MÊME LIGNE HORIZONTALE. Ne regarde pas au-dessus, ne regarde pas en dessous.
    3.  **Lignes doubles** : Si le jour a deux lignes, IGNORE la ligne du haut. Lis UNIQUEMENT la ligne du BAS.
    4.  **Colonnes** :
        - 1ère col (après la date) : ~07h45 - 09h45
        - 2ème col : ~10h00 - 12h00
        - 3ème col : ~13h45 - 15h45
        - 4ème col : ~15h45 - 17h45
    5.  **Filtre Groupe** : Garde seulement "/GB" ou sans groupe. Ignore "/GC", "/GA".
    6.  **Filtre Couleur** : Ignore STRICTEMENT les cases à fond ORANGE/JAUNE.
    7.  **Profs** : Utilise ce dictionnaire : {PROFS_DICT}

    FORMAT DE SORTIE ATTENDU (JSON STRUCTURÉ PAR JOUR) :
    {{
      "YYYY-MM-DD": [
         {{
           "summary": "Matière (Prof)",
           "start_time": "HH:MM",  (Heure début format 24h)
           "end_time": "HH:MM",    (Heure fin format 24h)
           "location": "Salle"
         }}
      ]
    }}
    Exemple de clé : "2026-01-12". Ne mets que les jours où il y a cours.
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
    # ORDRE DE PRIORITÉ CHANGÉ :
    # 1. gemini-1.5-pro : C'est le SEUL qui lit correctement les lignes complexes sans mélanger.
    # 2. gemini-1.5-flash : En secours uniquement.
    models_to_try = [
        "gemini-1.5-pro",
        "gemini-1.5-pro-latest",
        "gemini-1.5-flash" 
    ]

    for model in models_to_try:
        print(f"   👉 Analyse avec le modèle : {model}...")
        for attempt in range(2): # Moins d'essais mais plus ciblés
            try:
                response = call_gemini_api(image, model)

                if response.status_code == 200:
                    raw_resp = response.json()
                    if 'candidates' in raw_resp and raw_resp['candidates']:
                        clean = clean_json_text(raw_resp['candidates'][0]['content']['parts'][0]['text'])
                        return json.loads(clean)
                    else:
                        print("      ⚠️ Réponse vide.")
                
                elif response.status_code in [429, 503]:
                    # Le modèle Pro a un quota strict (2 requêtes/min en gratuit).
                    # On met une pause longue (20s) si on touche la limite.
                    print(f"      ⚠️ Quota/Surcharge ({response.status_code}). Pause 20s...")
                    time.sleep(20)
                    continue
                else:
                    print(f"      ❌ Erreur {response.status_code}.")
                    break 

            except Exception as e:
                print(f"      ❌ Exception : {e}")
                break
        
    return {} # Retourne un dict vide en cas d'échec total

def create_ics_file(grouped_events):
    ics = [
        "BEGIN:VCALENDAR",
        "VERSION:2.0",
        "PRODID:-//STRI//Groupe GB//FR",
        "CALSCALE:GREGORIAN",
        "METHOD:PUBLISH"
    ]
    
    # On parcourt le dictionnaire par date
    for date_str, courses in grouped_events.items():
        for evt in courses:
            try:
                # date_str est "YYYY-MM-DD"
                # evt['start_time'] est "HH:MM"
                d_clean = date_str.replace('-', '')
                s_clean = evt['start_time'].replace(':', '') + "00"
                e_clean = evt['end_time'].replace(':', '') + "00"

                ics.append("BEGIN:VEVENT")
                ics.append(f"DTSTART:{d_clean}T{s_clean}")
                ics.append(f"DTEND:{d_clean}T{e_clean}")
                ics.append(f"SUMMARY:{evt.get('summary', 'Cours')}")
                ics.append(f"LOCATION:{evt.get('location', '')}")
                ics.append("DESCRIPTION:Groupe GB")
                ics.append("END:VEVENT")
            except Exception as e:
                print(f"Erreur création événement : {e} pour {evt}")
                continue
                
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
        print(f"--- Analyse Page {i+1} (Cela peut prendre 10-15s pour le modèle Pro) ---")
        
        # Appel API
        page_events = get_schedule_robust(img)
        
        if page_events:
            print(f"✅ Jours trouvés sur cette page : {list(page_events.keys())}")
            all_grouped_events.update(page_events)
        else:
            print("❌ Echec lecture page.")
            
        # PAUSE OBLIGATOIRE POUR LE MODELE PRO (Free Tier)
        # Le free tier limite à 2 requêtes / minute. 
        # Si on a plusieurs pages, il faut attendre entre les deux.
        if i < len(images) - 1:
            print("⏳ Pause de 30s pour respecter le quota API Pro...")
            time.sleep(30)

    print("Génération ICS...")
    ics_content = create_ics_file(all_grouped_events)

    with open(OUTPUT_FILE, "w", encoding="utf-8") as f:
        f.write(ics_content)
    
    print(f"Terminé. Fichier généré : {OUTPUT_FILE}")

if __name__ == "__main__":
    main()
