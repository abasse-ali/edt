import os
import io
import json
import base64
import requests
import re
import time
import numpy as np
from pdf2image import convert_from_bytes
from PIL import Image, ImageDraw

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
    """Récupère les modèles disponibles pour la clé."""
    url = f"https://generativelanguage.googleapis.com/v1beta/models?key={API_KEY}"
    try:
        response = requests.get(url)
        if response.status_code != 200: return []
        return [m['name'].replace('models/', '') for m in response.json().get('models', [])]
    except: return []

def clean_json_text(text):
    text = re.sub(r"```json|```", "", text).strip()
    start = text.find('[')
    end = text.rfind(']')
    if start != -1 and end != -1:
        return text[start:end+1]
    return text

def preprocess_image_colors(pil_image):
    """
    Traite les couleurs spécifiques de l'EDT.
    Orange (#FFB84D) -> Blanc (Effacer)
    Jaune (#FFD966) -> Garder (Examen)
    """
    print("   🎨 Traitement colorimétrique précis...")
    img_array = np.array(pil_image)
    
    # ORANGE (#FFB84D) : R=255, G=184, B=77
    # JAUNE  (#FFD966) : R=255, G=217, B=102
    # La différence principale est le VERT (G). Orange < 200, Jaune > 200.
    
    # Condition ORANGE (Sport, etc.)
    # R > 200 (Rouge fort)
    # 130 < G < 200 (Vert moyen - c'est la clé pour distinguer de jaune)
    # B < 150 (Bleu faible)
    mask_orange = (img_array[:, :, 0] > 200) & \
                  (img_array[:, :, 1] > 130) & (img_array[:, :, 1] < 200) & \
                  (img_array[:, :, 2] < 150)
    
    # On efface l'orange (devient blanc)
    img_array[mask_orange] = [255, 255, 255]
    
    return Image.fromarray(img_array)

def call_gemini_api(image, model_name):
    url = f"https://generativelanguage.googleapis.com/v1beta/models/{model_name}:generateContent?key={API_KEY}"
    
    img_byte_arr = io.BytesIO()
    image.save(img_byte_arr, format='JPEG')
    b64_data = base64.b64encode(img_byte_arr.getvalue()).decode('utf-8')

    prompt = f"""
    Tu es un expert en lecture d'emploi du temps.
    TACHE : Analyse cette image pour l'étudiant du **Groupe GB**.
    ANNÉE : 2026.

    ⚠️ RÈGLES DE LECTURE GÉOMÉTRIQUE ET STRUCTURELLE (TRES IMPORTANT) :

    1. **ANCRAGE PAR JOUR** : 
       - Cherche le mot "Lundi" à gauche. Tout ce qui est sur cette ligne horizontale est pour Lundi.
       - Cherche "Mardi". Tout ce qui est sur cette ligne est pour Mardi.
       - Et ainsi de suite. Ne mélange pas les lignes !

    2. **GESTION DES SOUS-LIGNES (HAUT/BAS)** :
       - Dans une case horaire, il y a souvent DEUX matières l'une au-dessus de l'autre.
       - CELLE DU HAUT = Groupe 1 (G1/GA) -> **TU DOIS L'IGNORER**.
       - CELLE DU BAS = Groupe 2 (GB) -> **C'EST CELLE-LA QUE TU DOIS GARDER**.
       - Si le texte est centré verticalement (pas de division), c'est un cours commun -> GARDER.

    3. **COULEURS** :
       - Les cases ORANGES (ex: Sport) ont été effacées (sont blanches maintenant). Ignore les vides.
       - Les cases JAUNES sont des EXAMENS -> Ajoute "[EXAMEN]" au début du nom.

    4. **HORAIRES** :
       - Colonne 1 : 07h45 - 09h45
       - Colonne 2 : 10h00 - 12h00
       - Colonne 3 : **13h30** - 15h30 (Attention, commence à la 2ème graduation après 13h)
       - Colonne 4 : 15h45 - 17h45

    FORMAT DE SORTIE JSON ATTENDU :
    [
      {{
        "day_name": "Lundi/Mardi/...",
        "summary": "Matière (Prof)",
        "start": "HH:MM",
        "end": "HH:MM",
        "location": "Salle",
        "group_position": "BAS/UNIQUE/HAUT"
      }}
    ]
    (Remplace les profs avec : {PROFS_DICT})
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
    
    # On privilégie GEMINI 1.5 PRO pour sa capacité à lire les tableaux complexes sans "halluciner" les lignes
    priority_list = [
        "gemini-1.5-pro",
        "gemini-1.5-pro-latest",
        "gemini-1.5-pro-001",
        "gemini-2.0-flash", # Bon backup
        "gemini-flash-latest"
    ]
    
    models_to_try = [m for m in priority_list if m in available]
    if not models_to_try: models_to_try = ["gemini-1.5-flash"] # Fallback ultime

    cleaned_img = preprocess_image_colors(image)

    for model in models_to_try:
        print(f"   👉 Tentative avec : {model}...")
        try:
            response = call_gemini_api(cleaned_img, model)
            
            if response.status_code == 200:
                raw_resp = response.json()
                if 'candidates' in raw_resp and raw_resp['candidates']:
                    clean = clean_json_text(raw_resp['candidates'][0]['content']['parts'][0]['text'])
                    return json.loads(clean)
            
            elif response.status_code in [429, 503]:
                print(f"      ⚠️ Bloqué ({response.status_code}). Suivant...")
                continue
            
        except Exception as e:
            print(f"      ❌ Erreur : {e}")
            continue
            
    print("❌ Tous les modèles ont échoué sur cette page.")
    return []

def calculate_date(base_date, day_name):
    # Mapping simple pour la semaine du 12 Janvier 2026 (Semaine 1)
    # Pour un script de prod, il faudrait lire la date sur l'image ("12/janv")
    days_map = {"Lundi": 0, "Mardi": 1, "Mercredi": 2, "Jeudi": 3, "Vendredi": 4}
    
    # Nettoyage du nom du jour (ex: "Lundi 12")
    day_key = None
    for d in days_map:
        if d in day_name:
            day_key = d
            break
            
    if day_key is None: return None
    
    from datetime import datetime, timedelta
    start = datetime.strptime(base_date, "%Y-%m-%d")
    target = start + timedelta(days=days_map[day_key])
    return target.strftime("%Y-%m-%d")

def filter_and_format_events(raw_events, week_start_date):
    formatted = []
    print(f"   🧹 Filtrage Logique (Haut/Bas et Sport)...")
    
    for evt in raw_events:
        summary = evt.get('summary', '').strip()
        pos = evt.get('group_position', 'UNIQUE').upper()
        
        # 1. Filtre SPORT (si le nettoyage couleur a raté)
        if "SPORT" in summary.upper() and "EXAMEN" not in summary.upper():
            print(f"      🗑️ Rejet (Sport) : {summary}")
            continue
            
        # 2. Filtre Position HAUT (Sauf si GB mentionné explicitement)
        if pos == "HAUT" and "GB" not in summary.upper():
            print(f"      🗑️ Rejet (Groupe Haut/GA) : {summary}")
            continue
            
        # 3. Filtre Groupe Explicite GA/GC
        if "/GA" in summary.upper() or "/GC" in summary.upper() or "(GA)" in summary.upper():
            print(f"      🗑️ Rejet (Tag GA/GC) : {summary}")
            continue

        # Calcul date
        real_date = calculate_date(week_start_date, evt.get('day_name', 'Lundi'))
        if not real_date: continue
        
        evt['real_date'] = real_date
        
        # Nettoyage titre
        if "EXAMEN" in summary.upper() and not summary.startswith("🔴"):
             evt['summary'] = "🔴 " + summary
             evt['priority'] = "1"
        else:
             evt['priority'] = "5"
             
        formatted.append(evt)
        
    return formatted

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
            d = evt['real_date'].replace('-', '')
            s = evt['start'].replace(':', '') + "00"
            e = evt['end'].replace(':', '') + "00"
            
            ics.append("BEGIN:VEVENT")
            ics.append(f"DTSTART:{d}T{s}")
            ics.append(f"DTEND:{d}T{e}")
            ics.append(f"SUMMARY:{evt['summary']}")
            ics.append(f"LOCATION:{evt.get('location', '')}")
            ics.append(f"PRIORITY:{evt['priority']}")
            ics.append("DESCRIPTION:Groupe GB")
            ics.append("END:VEVENT")
        except: continue
    ics.append("END:VCALENDAR")
    return "\n".join(ics)

def main():
    if not API_KEY: raise Exception("Clé API manquante")

    print("Téléchargement PDF...")
    response = requests.get(PDF_URL)
    
    print("Conversion PDF -> Images (300 DPI)...")
    images = convert_from_bytes(response.content, dpi=300) 

    all_events = []
    
    # Dates de début de semaine (Supposées pour l'exemple, à adapter)
    # Page 1 = Semaine du 12 Janvier 2026
    # Page 2 = Semaine du 19 Janvier 2026...
    start_dates = ["2026-01-12", "2026-01-19", "2026-01-26", "2026-02-02", "2026-02-09"]

    print(f"Traitement de {len(images)} pages...")
    for i, img in enumerate(images):
        print(f"--- Page {i+1} ---")
        
        # Récupération brute
        raw_data = get_schedule_robust(img)
        
        # Filtrage et Calcul de date
        week_date = start_dates[i] if i < len(start_dates) else "2026-01-01"
        valid_events = filter_and_format_events(raw_data, week_date)
        
        all_events.extend(valid_events)
        
        # Pause anti-quota
        time.sleep(2)

    print("Génération ICS...")
    ics_content = create_ics_file(all_events)

    with open(OUTPUT_FILE, "w", encoding="utf-8") as f:
        f.write(ics_content)
    print(f"Terminé. Fichier généré : {OUTPUT_FILE}")

if __name__ == "__main__":
    main()
