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

# Liste des profs
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
    # Enlève les balises markdown ```json ... ```
    text = re.sub(r"```json|```", "", text).strip()
    # Trouve le début et la fin de la liste JSON [...]
    start = text.find('[')
    end = text.rfind(']')
    if start != -1 and end != -1:
        return text[start:end+1]
    return text

def get_schedule_from_gemini(image):
    # On tente le modèle 'gemini-1.5-flash' qui est rapide et souvent dispo. 
    # Si celui-ci échoue en 404, changez pour 'gemini-1.5-pro'
    model_name = "gemini-1.5-flash-latest" 
    url = f"https://generativelanguage.googleapis.com/v1beta/models/{model_name}:generateContent?key={API_KEY}"
    
    img_byte_arr = io.BytesIO()
    image.save(img_byte_arr, format='JPEG')
    b64_data = base64.b64encode(img_byte_arr.getvalue()).decode('utf-8')

    # Année scolaire : Si on est après aout, on assume que l'année commence, sinon c'est la fin
    current_year = datetime.now().year
    
    prompt = f"""
    Rôle : Tu es un assistant précis qui convertit des images d'emploi du temps en données structurées.
    
    TACHE : Analyse cette image pour l'étudiant du groupe "GB" UNIQUEMENT.
    
    RÈGLES VISUELLES STRICTES :
    1.  **Lignes doubles** : Si une journée contient 2 lignes horizontales, REGARDE UNIQUEMENT LA LIGNE DU BAS. Ignore celle du haut.
    2.  **Filtrage Groupe** : 
        - Si un cours est marqué "/GC", IGNORE-LE.
        - Si un cours est marqué "/GB", GARDE-LE.
        - Si un cours n'a pas de groupe indiqué, GARDE-LE (cours commun).
    3.  **Grille horaire** : Les lignes verticales marquent 15 minutes. Le début est à 07h45.
        - 1er créneau standard : ~07h45 - 09h45
        - 2e créneau standard : ~10h00 - 12h00
        - 3e créneau standard : ~13h30 - 15h30
        - 4e créneau standard : ~15h45 - 17h45
        (Adapte selon la position visuelle exacte).
    4.  **Dates** : Convertis les dates (ex: "Lundi 12/janv") en format "JJ/MM/AAAA". Utilise l'année {current_year} ou {current_year + 1} selon la logique scolaire.
    5.  **Profs** : Remplace les initiales (ex: JGT) par {PROFS_DICT}.
    
    SORTIE :
    Retourne UNIQUEMENT une liste JSON. Exemple :
    [
        {{
            "summary": "Matière (Nom Prof)",
            "start": "2025-01-12T07:45:00",
            "end": "2025-01-12T09:45:00",
            "location": "Salle U3-..."
        }}
    ]
    Si aucun cours n'est trouvé pour le groupe GB, retourne une liste vide [].
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

    if response.status_code != 200:
        print(f"Erreur API ({response.status_code}): {response.text}")
        return []

    try:
        raw_resp = response.json()
        if 'candidates' not in raw_resp or not raw_resp['candidates']:
            print("L'IA n'a rien renvoyé (Candidats vides).")
            return []
            
        raw_text = raw_resp['candidates'][0]['content']['parts'][0]['text']
        clean_text = clean_json_text(raw_text)
        return json.loads(clean_text)
    except Exception as e:
        print(f"Erreur lecture JSON : {e}")
        print(f"Contenu brut reçu : {raw_text if 'raw_text' in locals() else 'Rien'}")
        return []

def create_ics_file(events):
    ics_lines = [
        "BEGIN:VCALENDAR",
        "VERSION:2.0",
        "PRODID:-//STRI//Groupe GB//FR",
        "CALSCALE:GREGORIAN",
        "METHOD:PUBLISH"
    ]
    
    for evt in events:
        try:
            # Nettoyage des dates pour format ICS (YYYYMMDDTHHMMSS)
            start_dt = evt['start'].replace('-', '').replace(':', '') 
            end_dt = evt['end'].replace('-', '').replace(':', '')
            
            # Ajout du Z si absent (UTC) ou gestion locale. Ici on reste simple.
            # Pour être propre en iCal, il vaut mieux éviter les tirets/points
            
            ics_lines.append("BEGIN:VEVENT")
            ics_lines.append(f"DTSTART:{start_dt}")
            ics_lines.append(f"DTEND:{end_dt}")
            ics_lines.append(f"SUMMARY:{evt.get('summary', 'Cours')}")
            if 'location' in evt and evt['location']:
                ics_lines.append(f"LOCATION:{evt['location']}")
            ics_lines.append("DESCRIPTION:Groupe GB")
            ics_lines.append("END:VEVENT")
        except Exception as e:
            print(f"Event mal formé ignoré : {evt} ({e})")
            continue

    ics_lines.append("END:VCALENDAR")
    return "\n".join(ics_lines)

def main():
    if not API_KEY:
        raise Exception("Clé API manquante !")

    print(f"Téléchargement du PDF...")
    response = requests.get(PDF_URL)
    if response.status_code != 200:
        raise Exception("Erreur téléchargement PDF")

    print("Conversion PDF -> Images (Haute Qualité)...")
    # DPI=400 est CRUCIAL pour que l'IA lise les petits caractères
    images = convert_from_bytes(response.content, dpi=400)
    
    all_events = []

    print(f"Traitement de {len(images)} pages...")
    for i, img in enumerate(images):
        print(f"--- Analyse Page {i+1} ---")
        events = get_schedule_from_gemini(img)
        if events:
            print(f"{len(events)} événements trouvés.")
            all_events.extend(events)
        else:
            print("Aucun événement détecté sur cette page (ou erreur).")

    print("Génération du fichier ICS...")
    ics_content = create_ics_file(all_events)
    
    with open(OUTPUT_FILE, "w", encoding="utf-8") as f:
        f.write(ics_content)
    
    print("Terminé avec succès.")

if __name__ == "__main__":
    main()
