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

# Dictionnaire profs
PROFS_DICT = """
AnAn=Andréi ANDRÉI; AA=André AOUN; AB=Abdelmalek BENZEKRI; AL=Abir LARABA; BC=Bilal CHEBARO; 
BTJ=Boris TIOMELA JOU; CC=Cédric CHAMBAULT; CG=Christine GALY; CT=Cédric TEYSSIE; EG=Eric GONNEAU; 
EL=Emmanuel LAVINAL; FM=Frédéric MOUTIER; GR=Gérard ROUZIES; JGT=Jean-Guy TARTARIN; JS=Jérôme SOKOLOFF; 
KB=Ketty BRAVO; LC=Louisa COT; MCL=Marie-Christine LAGASQUIÉ; MM=MUSTAPHA MOJAHID; OC=Olivier CRIVELLARO; 
OM=Olfa MECHI; PA=Patrick AUSTIN; PhA=Philippe ARGUEL; PIL=Pierre LOTTE; PL=Philippe LATU; PT=Patrice TORGUET; 
RK=Rahim KACIMI; RL=Romain LABORDE; SB=Sonia BADENE; SL=Séverine LALANDE; TD=Thierry DESPRATS; TG=Thierry GAYRAUD.
"""

def find_best_model():
    """Cherche un modèle valide."""
    url = f"https://generativelanguage.googleapis.com/v1beta/models?key={API_KEY}"
    try:
        response = requests.get(url)
        if response.status_code != 200:
            return "gemini-1.5-flash"
        
        data = response.json()
        models = [m['name'].replace('models/', '') for m in data.get('models', [])]
        print(f"ℹ️ Modèles disponibles : {models}")
        
        # On préfère le 1.5 Pro pour la lecture de tableaux complexes, sinon Flash
        preferences = ["gemini-1.5-pro", "gemini-1.5-pro-latest", "gemini-1.5-flash", "gemini-1.5-flash-latest"]
        
        for pref in preferences:
            if pref in models:
                return pref
        return "gemini-1.5-flash"
    except:
        return "gemini-1.5-flash"

def get_gemini_response(image, model_name):
    url = f"https://generativelanguage.googleapis.com/v1beta/models/{model_name}:generateContent?key={API_KEY}"
    
    img_byte_arr = io.BytesIO()
    image.save(img_byte_arr, format='JPEG')
    b64_data = base64.b64encode(img_byte_arr.getvalue()).decode('utf-8')

    # Prompt renforcé
    prompt_text = f"""
    Tu es un convertisseur de données. Analyse cette image d'emploi du temps.
    EXTRACTION STRICTE POUR LE GROUPE "GB".

    RÈGLES :
    1. Année des cours : Considère que nous sommes en Janvier/Février 2025 (Année scolaire 2024-2025).
    2. Lignes : Ignore la ligne du haut si une journée est divisée. Ignore les cases oranges.
    3. Groupe : Garde uniquement les cours "/GB" ou Tronc Commun (sans groupe). Ignore "/GC".
    4. Créneaux : Les lignes verticales sont des pas de 15min. Début 7h45.
    5. Profs : Utilise ce mapping : {PROFS_DICT}

    SORTIE :
    Donne MOI UNIQUEMENT le code ICS valide. 
    Pas de ```, pas de phrase d'intro.
    Format:
    BEGIN:VEVENT
    SUMMARY:Matière (Prof)
    DTSTART:20250112T074500
    DTEND:20250112T094500
    LOCATION:Salle
    DESCRIPTION:Groupe GB
    END:VEVENT
    """

    payload = {
        "contents": [{"parts": [{"text": prompt_text}, {"inline_data": {"mime_type": "image/jpeg", "data": b64_data}}]}],
        # AJOUT CRUCIAL : Désactivation des sécurités qui bloquent souvent les emplois du temps
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
        print(f"❌ ERREUR HTTP {response.status_code}: {response.text}")
        return ""

    data = response.json()
    
    # DEBUG : Vérifier pourquoi l'IA ne répond pas si c'est vide
    if 'candidates' not in data or not data['candidates']:
        print(f"⚠️ AUCUN CANDIDAT GÉNÉRÉ. Réponse brute : {data}")
        # Souvent dû à un finishReason: SAFETY ou RECITATION
        return ""
    
    try:
        text = data['candidates'][0]['content']['parts'][0]['text']
        print(f"✅ Texte généré (longueur): {len(text)} caractères")
        return text
    except KeyError:
        print(f"⚠️ Erreur structure JSON : {data}")
        return ""

def main():
    if not API_KEY:
        raise Exception("Clé API manquante")

    print("Recherche modèle...")
    best_model = find_best_model()
    print(f"Modèle utilisé : {best_model}")

    print("Téléchargement PDF...")
    response = requests.get(PDF_URL)
    images = convert_from_bytes(response.content)

    # En-tête ICS
    full_ics = "BEGIN:VCALENDAR\nVERSION:2.0\nPRODID:-//STRI//Groupe GB//FR\nCALSCALE:GREGORIAN\n"
    
    print(f"Traitement de {len(images)} pages...")
    for i, img in enumerate(images):
        print(f"--- Page {i+1} ---")
        ics_part = get_gemini_response(img, best_model)
        
        # Nettoyage brutal pour ne garder que les VEVENT
        lines = ics_part.splitlines()
        for line in lines:
            line = line.strip()
            if "BEGIN:VEVENT" in line or "END:VEVENT" in line or "DTSTART" in line or "DTEND" in line or "SUMMARY" in line or "LOCATION" in line or "DESCRIPTION" in line:
                full_ics += line + "\n"
            # On accepte aussi les UID et DTSTAMP si générés
            elif line.startswith("UID:") or line.startswith("DTSTAMP:"):
                full_ics += line + "\n"

    full_ics += "END:VCALENDAR"

    with open(OUTPUT_FILE, "w", encoding="utf-8") as f:
        f.write(full_ics)
    
    print("Fini.")

if __name__ == "__main__":
    main()
