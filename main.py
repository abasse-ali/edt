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

def get_gemini_response(image):
    # CORRECTION MAJEURE ICI :
    # 1. On utilise 'gemini-1.5-flash-001' (version stable spécifique) au lieu de l'alias générique
    # 2. On utilise l'URL directe pour éviter les bugs de librairie Python
    url = f"https://generativelanguage.googleapis.com/v1beta/models/gemini-1.5-flash-001:generateContent?key={API_KEY}"
    
    # Préparation de l'image
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
    - Pas de balises markdown (```).
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
        # Affiche l'erreur exacte renvoyée par Google pour le débogage
        raise Exception(f"Erreur API ({response.status_code}): {response.text}")

    try:
        return response.json()['candidates'][0]['content']['parts'][0]['text']
    except (KeyError, IndexError):
        return ""

def main():
    if not API_KEY:
        raise Exception("Erreur : La clé API GEMINI_API_KEY est introuvable dans les secrets.")

    print(f"Téléchargement du PDF...")
    response = requests.get(PDF_URL)
    if response.status_code != 200:
        raise Exception("Erreur téléchargement PDF")

    print("Conversion PDF -> Images...")
    images = convert_from_bytes(response.content)

    full_ics_content = "BEGIN:VCALENDAR\nVERSION:2.0\nPRODID:-//STRI//Groupe GB//FR\nCALSCALE:GREGORIAN\n"
    
    print(f"Traitement de {len(images)} pages...")
    for i, img in enumerate(images):
        print(f"Envoi page {i+1} à l'API...")
        ics_part = get_gemini_response(img)
        
        # Nettoyage de la réponse
        lines = ics_part.splitlines()
        for line in lines:
            line = line.strip()
            if line.startswith("```"): continue
            if line.startswith("BEGIN:VCALENDAR") or line.startswith("END:VCALENDAR") or line.startswith("VERSION:") or line.startswith("PRODID:"):
                continue
            if line:
                full_ics_content += line + "\n"

    full_ics_content += "END:VCALENDAR"

    with open(OUTPUT_FILE, "w", encoding="utf-8") as f:
        f.write(full_ics_content)
    
    print(f"Succès ! Fichier {OUTPUT_FILE} généré.")

if __name__ == "__main__":
    main()
