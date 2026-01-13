import os
import io
import json
import base64
import requests
import re
from pdf2image import convert_from_bytes
from datetime import datetime

# --- CONFIGURATION ---
PDF_URL = "https://stri.fr/Gestion_STRI/TAV/L3/EDT_STRI1A_L3IRT_TAV.pdf"
OUTPUT_FILE = "emploi_du_temps.ics"
API_KEY = os.environ.get("GEMINI_API_KEY")

# Mapping des profs
PROFS_DICT = """
AnAn=Andréi ANDRÉI; AA=André AOUN; AB=Abdelmalek BENZEKRI; AL=Abir LARABA; BC=Bilal CHEBARO; 
BTJ=Boris TIOMELA JOU; CC=Cédric CHAMBAULT; CG=Christine GALY; CT=Cédric TEYSSIE; EG=Eric GONNEAU; 
EL=Emmanuel LAVINAL; FM=Frédéric MOUTIER; GR=Gérard ROUZIES; JGT=Jean-Guy TARTARIN; JS=Jérôme SOKOLOFF; 
KB=Ketty BRAVO; LC=Louisa COT; MCL=Marie-Christine LAGASQUIÉ; MM=MUSTAPHA MOJAHID; OC=Olivier CRIVELLARO; 
OM=Olfa MECHI; PA=Patrick AUSTIN; PhA=Philippe ARGUEL; PIL=Pierre LOTTE; PL=Philippe LATU; PT=Patrice TORGUET; 
RK=Rahim KACIMI; RL=Romain LABORDE; SB=Sonia BADENE; SL=Séverine LALANDE; TD=Thierry DESPRATS; TG=Thierry GAYRAUD.
"""

def clean_json_text(text):
    """Nettoie le texte pour extraire uniquement le JSON valide."""
    # On cherche ce qui est entre [ et ]
    match = re.search(r'\[.*\]', text, re.DOTALL)
    if match:
        return match.group(0)
    # Sinon on essaie de nettoyer les balises markdown
    text = text.replace("```json", "").replace("```", "").strip()
    return text

def get_schedule_from_gemini(image):
    # On utilise gemini-1.5-pro car il lit mieux les grilles complexes que flash
    url = f"https://generativelanguage.googleapis.com/v1beta/models/gemini-1.5-pro:generateContent?key={API_KEY}"
    
    img_byte_arr = io.BytesIO()
    image.save(img_byte_arr, format='JPEG')
    b64_data = base64.b64encode(img_byte_arr.getvalue()).decode('utf-8')

    # On force l'année 2025 pour les dates (janvier/février)
    current_year = datetime.now().year
    if datetime.now().month > 8: # Si on est en sept/oct, l'année scolaire commence
        target_year = current_year + 1 # Janvier sera l'année suivante
    else:
        target_year = current_year

    prompt = f"""
    Analyse cet emploi du temps (image) pour le groupe "GB".
    
    CONTEXTE:
    - Année : {target_year}
    - Ignore les cours du groupe "GC".
    - Ignore les lignes supérieures si une journée est coupée en deux.
    - Ignore les cases ORANGE.
    - Les heures : Lignes verticales = 15min. Début journée 07h45.
    - Profs : Utilise ce mapping : {PROFS_DICT}

    TACHE :
    Extrais les cours sous forme de liste JSON stricte.
    Format attendu :
    [
      {{
        "date": "JJ/MM/AAAA",
        "start": "HH:MM",
        "end": "HH:MM",
        "summary": "Nom du cours + Prof",
        "location": "Salle (carré vert)"
      }}
    ]
    
    Exemple de date : Si tu vois "Lundi 12/janv", mets "12/01/{target_year}".
    """

    payload = {
        "contents": [{"parts": [{"text": prompt}, {"inline_data": {"mime_type": "image/jpeg", "data": b64_data}}]}],
        "generationConfig": {"response_mime_type": "application/json"}, # Force le JSON
        "safetySettings": [
             {"category": "HARM_CATEGORY_HARASSMENT", "threshold": "BLOCK_NONE"},
             {"category": "HARM_CATEGORY_HATE_SPEECH", "threshold": "BLOCK_NONE"},
             {"category": "HARM_CATEGORY_SEXUALLY_EXPLICIT", "threshold": "BLOCK_NONE"},
             {"category": "HARM_CATEGORY_DANGEROUS_CONTENT", "threshold": "BLOCK_NONE"}
        ]
    }

    headers = {'Content-Type': 'application/json'}
    response = requests.post(url, headers=headers, data=json.dumps(payload))

    if response.status_code != 200:
        print(f"Erreur API: {response.text}")
        return []

    try:
        raw_text = response.json()['candidates'][0]['content']['parts'][0]['text']
        clean_text = clean_json_text(raw_text)
        return json.loads(clean_text)
    except Exception as e:
        print(f"Erreur parsing JSON: {e}")
        print(f"Texte reçu: {raw_text if 'raw_text' in locals() else 'Rien'}")
        return []

def create_ics_file(events):
    ics_content = [
        "BEGIN:VCALENDAR",
        "VERSION:2.0",
        "PRODID:-//STRI//Groupe GB//FR",
        "CALSCALE:GREGORIAN",
        "METHOD:PUBLISH"
    ]
    
    for event in events:
        try:
            # Conversion date format JJ/MM/AAAA vers YYYYMMDD
            d_parts = event['date'].split('/')
            date_str = f"{d_parts[2]}{d_parts[1]}{d_parts[0]}"
            
            # Conversion heure HH:MM vers HHMMSS
            start_str = event['start'].replace(':', '') + "00"
            end_str = event['end'].replace(':', '') + "00"
            
            ics_content.append("BEGIN:VEVENT")
            ics_content.append(f"DTSTART:{date_str}T{start_str}")
            ics_content.append(f"DTEND:{date_str}T{end_str}")
            ics_content.append(f"SUMMARY:{event['summary']}")
            if event.get('location'):
                ics_content.append(f"LOCATION:{event['location']}")
            ics_content.append("DESCRIPTION:Groupe GB - Généré par IA")
            ics_content.append("END:VEVENT")
        except Exception as e:
            print(f"Event ignoré (données invalides) : {event} - {e}")
            continue

    ics_content.append("END:VCALENDAR")
    return "\n".join(ics_content)

def main():
    if not API_KEY:
        raise Exception("Clé API manquante")

    print("Téléchargement du PDF...")
    response = requests.get(PDF_URL)
    images = convert_from_bytes(response.content)
    
    all_events = []

    print(f"Traitement de {len(images)} pages...")
    for i, img in enumerate(images):
        print(f"Analyse page {i+1}...")
        events = get_schedule_from_gemini(img)
        if events:
            print(f" -> {len(events)} cours trouvés sur cette page.")
            all_events.extend(events)
        else:
            print(" -> Aucun cours détecté.")

    print("Génération du fichier ICS...")
    ics_text = create_ics_file(all_events)
    
    with open(OUTPUT_FILE, "w", encoding="utf-8") as f:
        f.write(ics_text)
    
    print("Terminé.")

if __name__ == "__main__":
    main()
