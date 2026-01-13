import os
import io
import json
import base64
import requests
from pdf2image import convert_from_bytes
from datetime import datetime

# Configuration
PDF_URL = "https://stri.fr/Gestion_STRI/TAV/L3/EDT_STRI1A_L3IRT_TAV.pdf"
OUTPUT_FILE = "emploi_du_temps.ics"
API_KEY = os.environ.get("GEMINI_API_KEY")

# Liste des profs
PROFS_DICT = """
AnAn=Andréi ANDRÉI; AA=André AOUN; AB=Abdelmalek BENZEKRI; AL=Abir LARABA; BC=Bilal CHEBARO; 
BTJ=Boris TIOMELA JOU; CC=Cédric CHAMBAULT; CG=Christine GALY; CT=Cédric TEYSSIE; EG=Eric GONNEAU; 
EL=Emmanuel LAVINAL; FM=Frédéric MOUTIER; GR=Gérard ROUZIES; JGT=Jean-Guy TARTARIN; JS=Jérôme SOKOLOFF; 
KB=Ketty BRAVO; LC=Louisa COT; MCL=Marie-Christine LAGASQUIÉ; MM=MUSTAPHA MOJAHID; OC=Olivier CRIVELLARO; 
OM=Olfa MECHI; PA=Patrick AUSTIN; PhA=Philippe ARGUEL; PIL=Pierre LOTTE; PL=Philippe LATU; PT=Patrice TORGUET; 
RK=Rahim KACIMI; RL=Romain LABORDE; SB=Sonia BADENE; SL=Séverine LALANDE; TD=Thierry DESPRATS; TG=Thierry GAYRAUD.
"""

def find_best_model():
    """Demande à l'API quels modèles sont disponibles pour cette clé."""
    url = f"https://generativelanguage.googleapis.com/v1beta/models?key={API_KEY}"
    try:
        response = requests.get(url)
        if response.status_code != 200:
            print(f"Impossible de lister les modèles ({response.status_code}). Utilisation du défaut.")
            return "gemini-1.5-flash"
        
        data = response.json()
        models = [m['name'].replace('models/', '') for m in data.get('models', [])]
        
        # Ordre de préférence des modèles
        preferences = [
            "gemini-1.5-flash",
            "gemini-1.5-flash-001",
            "gemini-1.5-flash-latest",
            "gemini-2.0-flash-exp",
            "gemini-1.5-pro",
            "gemini-1.5-pro-001"
        ]
        
        print(f"Modèles disponibles pour votre clé : {models}")
        
        for pref in preferences:
            if pref in models:
                print(f"Modèle choisi : {pref}")
                return pref
        
        # Si aucun favori n'est trouvé, on prend le premier qui contient "gemini"
        for m in models:
            if "gemini" in m and "vision" not in m: # On évite les vieux modèles vision-only
                return m
                
        return "gemini-1.5-flash" # Fallback ultime
        
    except Exception as e:
        print(f"Erreur lors de la recherche de modèle : {e}")
        return "gemini-1.5-flash"

def get_gemini_response(image, model_name):
    url = f"https://generativelanguage.googleapis.com/v1beta/models/{model_name}:generateContent?key={API_KEY}"
    
    img_byte_arr = io.BytesIO()
    image.save(img_byte_arr, format='JPEG')
    img_bytes = img_byte_arr.getvalue()
    b64_data = base64.b64encode(img_bytes).decode('utf-8')

    current_year = datetime.now().year

    prompt_text = f"""
    Agis comme un assistant de planification expert. Analyse cette image d'emploi du temps pour l'étudiant du groupe "GB".
    
    CONTEXTE :
    - Année courante : {current_year}
    - Objectif : Extraire les événements pour un calendrier (.ics).

    RÈGLES DE LECTURE VISUELLE :
    1. Si une journée a deux lignes horizontales, IGNORE la ligne du HAUT et les cases ORANGE. Lis UNIQUEMENT la ligne du BAS.
    2. Groupe : Ignore les cours "/GC". Garde uniquement "/GB" ou les cours sans groupe.
    3. Horaires : Les lignes verticales marquent 15min. Début journée 7h45.
       Ex: 07h45-09h45, 10h00-12h00, 13h30-15h30, 15h45-17h45.
    4. Salles : Petit carré vert en haut à droite.
    5. Profs : Utilise ce dictionnaire : {PROFS_DICT}

    SORTIE ATTENDUE (Format ICS Brut) :
    - Doit commencer par BEGIN:VCALENDAR et finir par END:VCALENDAR.
    - Pas de balises markdown.
    - Pour chaque cours: SUMMARY (Matière + Prof), LOCATION (Salle), DTSTART, DTEND.
    """

    payload = {
        "contents": [{
            "parts": [
                {"text": prompt_text},
                {
                    "inline_data": {
                        "mime_type": "image/jpeg",
                        "data": b64_data
                    }
                }
            ]
        }]
    }

    headers = {'Content-Type': 'application/json'}
    response = requests.post(url, headers=headers, data=json.dumps(payload))

    if response.status_code != 200:
        raise Exception(f"Erreur API ({response.status_code}) avec le modèle {model_name}: {response.text}")

    try:
        return response.json()['candidates'][0]['content']['parts'][0]['text']
    except (KeyError, IndexError):
        return ""

def main():
    if not API_KEY:
        raise Exception("Erreur : La clé API GEMINI_API_KEY est introuvable.")

    # 1. Trouver le bon modèle dynamiquement
    print("Recherche du meilleur modèle disponible...")
    best_model = find_best_model()

    print(f"Téléchargement du PDF...")
    response = requests.get(PDF_URL)
    if response.status_code != 200:
        raise Exception("Erreur téléchargement PDF")

    print("Conversion PDF -> Images...")
    images = convert_from_bytes(response.content)

    full_ics_content = "BEGIN:VCALENDAR\nVERSION:2.0\nPRODID:-//STRI//Groupe GB//FR\nCALSCALE:GREGORIAN\n"
    
    print(f"Traitement de {len(images)} pages avec le modèle '{best_model}'...")
    for i, img in enumerate(images):
        print(f"   - Analyse page {i+1}...")
        try:
            ics_part = get_gemini_response(img, best_model)
            
            lines = ics_part.splitlines()
            for line in lines:
                line = line.strip()
                if line.startswith("```"): continue
                if line.startswith("BEGIN:VCALENDAR") or line.startswith("END:VCALENDAR") or line.startswith("VERSION:") or line.startswith("PRODID:"):
                    continue
                if line:
                    full_ics_content += line + "\n"
        except Exception as e:
            print(f"Erreur sur la page {i+1}: {e}")

    full_ics_content += "END:VCALENDAR"

    with open(OUTPUT_FILE, "w", encoding="utf-8") as f:
        f.write(full_ics_content)
    
    print(f"Succès ! Fichier {OUTPUT_FILE} généré.")

if __name__ == "__main__":
    main()
