import os
import io
import json
import base64
import requests
import re
import time
import numpy as np
from pdf2image import convert_from_bytes
from PIL import Image

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
    """Récupère les modèles dispos pour votre clé."""
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
    Efface le Orange (Sport/Annulé) mais GARDE le Jaune (Examen).
    Orange (#FFB84D) ~ RGB(255, 184, 77)
    Jaune (#FFD966) ~ RGB(255, 217, 102)
    Différence clé : Le canal VERT (G).
    """
    print("   🎨 Nettoyage couleurs (Suppression Orange, Conservation Jaune)...")
    img_array = np.array(pil_image)
    
    # Condition ORANGE (Sport)
    # Rouge fort (>200)
    # Vert MOYEN (>130 et <205) -> C'est ici qu'on distingue du jaune (qui est >210)
    # Bleu faible (<150)
    red_cond = img_array[:, :, 0] > 200
    green_cond = (img_array[:, :, 1] > 130) & (img_array[:, :, 1] < 205)
    blue_cond = img_array[:, :, 2] < 150
    
    mask_orange = red_cond & green_cond & blue_cond
    
    # On remplace l'orange par du blanc
    img_array[mask_orange] = [255, 255, 255]
    
    return Image.fromarray(img_array)

def call_gemini_api(image, model_name):
    url = f"https://generativelanguage.googleapis.com/v1beta/models/{model_name}:generateContent?key={API_KEY}"
    
    img_byte_arr = io.BytesIO()
    image.save(img_byte_arr, format='JPEG')
    b64_data = base64.b64encode(img_byte_arr.getvalue()).decode('utf-8')

    prompt = f"""
    Analyse cette page d'emploi du temps universitaire pour le GROUPE "GB" (Groupe B).
    ANNÉE : 2026.

    ⚠️ RÈGLES VISUELLES CRITIQUES (Ligne par ligne) :
    
    1. **LECTURE VERTICALE (HAUT vs BAS)** : 
       Dans une même case horaire, il y a souvent DEUX textes superposés séparés par une ligne ou un espace.
       - TEXTE DU HAUT = Groupe A (GA/GC) -> **IGNORE-LE**.
       - TEXTE DU BAS = Groupe B (GB) -> **GARDE-LE**.
       - Si le texte est unique/centré -> GARDE-LE.

    2. **COULEUR** :
       - Les cours sur fond ORANGE ont été effacés (blancs).
       - Les cours sur fond JAUNE sont des EXAMENS -> Ajoute "[EXAMEN]" au début du titre.

    3. **STRUCTURE** :
       - Ligne 1 = Lundi, Ligne 2 = Mardi, etc. Repère les jours à gauche.
       - Colonnes :
         - 07h45 - 09h45
         - 10h00 - 12h00
         - 13h30 - 15h30 (Attention, commence après la pause de midi)
         - 15h45 - 17h45

    FORMAT DE SORTIE JSON :
    [
      {{
        "day": "Lundi/Mardi/Mercredi/Jeudi/Vendredi",
        "summary": "Matière (Prof)",
        "start": "HH:MM",
        "end": "HH:MM",
        "location": "Salle",
        "raw_content_position": "BAS" (ou "UNIQUE", ou "HAUT" si erreur)
      }}
    ]
    Si une case contient "Anglais" en haut et "Espagnol" en bas, renvoie uniquement "Espagnol".
    Profs: {PROFS_DICT}
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
    # --- VOTRE LISTE DE PRIORITÉ EXACTE ---
    priority_list = [
        # --- GÉNÉRATION 3 ---
        "gemini-3-pro-preview",
        "gemini-3-flash-preview",
        # --- GÉNÉRATION 2.5 ---
        "gemini-2.5-pro",
        "gemini-2.5-flash",
        # --- GÉNÉRATION 2.0 ---
        "gemini-2.0-flash-001",
        # --- LITE ---
        "gemini-2.5-flash-lite",
        "gemini-2.0-flash-lite-preview-02-05",
        # --- GÉNÉRATION 1.5 ---
        "gemini-1.5-pro-latest",
        "gemini-1.5-pro",
        "gemini-1.5-flash-latest",
        "gemini-1.5-flash",
        "gemini-1.5-flash-8b"
    ]

    available = get_available_models()
    # On filtre pour ne garder que ceux qui existent vraiment sur votre compte
    models_to_try = [m for m in priority_list if m in available]
    
    # Fallback si liste vide
    if not models_to_try: 
        print("⚠️ Aucun modèle de la liste n'est dispo. Utilisation de gemini-1.5-flash.")
        models_to_try = ["gemini-1.5-flash"]

    print(f"   📋 Ordre de test : {models_to_try}")
    
    # 1. Nettoyage couleur
    clean_img = preprocess_image_colors(image)

    # 2. Boucle de tentative
    for model in models_to_try:
        try:
            print(f"   👉 Tentative avec {model}...")
            response = call_gemini_api(clean_img, model)
            
            if response.status_code == 200:
                raw = response.json()
                if 'candidates' in raw and raw['candidates']:
                    clean_txt = clean_json_text(raw['candidates'][0]['content']['parts'][0]['text'])
                    return json.loads(clean_txt)
                else:
                    print("      ⚠️ Réponse vide.")
                    continue
            
            elif response.status_code in [429, 503]:
                print(f"      ⚠️ Erreur {response.status_code} (Quota/Surcharge). Passage au suivant...")
                continue # On passe au suivant IMMÉDIATEMENT
            
            else:
                print(f"      ❌ Erreur {response.status_code}. Suivant...")
                continue
                
        except Exception as e:
            print(f"      ❌ Exception: {e}. Suivant...")
            continue
            
    print("❌ ECHEC TOTAL : Aucun modèle n'a réussi à lire cette page.")
    return []

def calculate_real_date(week_start_str, day_name):
    """Calcule la date réelle à partir du jour de la semaine."""
    days = ["Lundi", "Mardi", "Mercredi", "Jeudi", "Vendredi"]
    # Nettoyage du nom du jour (ex: "Lundi 12")
    found_day = None
    for d in days:
        if d.lower() in day_name.lower():
            found_day = d
            break
    
    if not found_day: return None

    from datetime import datetime, timedelta
    try:
        start_date = datetime.strptime(week_start_str, "%Y-%m-%d")
        delta = days.index(found_day)
        target_date = start_date + timedelta(days=delta)
        return target_date.strftime("%Y-%m-%d")
    except: return None

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
            # FILTRAGE FINAL DE SÉCURITÉ
            summary = evt.get('summary', '').upper()
            
            # 1. Si "GA" ou "GC" est explicitement écrit -> Poubelle
            if "/GA" in summary or "/GC" in summary: 
                print(f"      🗑️ Rejet (Tag GA/GC): {summary}")
                continue
                
            # 2. Si position "HAUT" détectée par l'IA -> Poubelle (sauf si GB mentionné)
            if evt.get('raw_content_position') == "HAUT" and "GB" not in summary:
                 print(f"      🗑️ Rejet (Position Haut): {summary}")
                 continue

            # 3. Si Sport est encore là (couleur ratée) -> Poubelle (sauf si examen)
            if "SPORT" in summary and "EXAMEN" not in summary:
                print(f"      🗑️ Rejet (Sport): {summary}")
                continue

            d = evt['real_date'].replace('-', '')
            s = evt['start'].replace(':', '') + "00"
            e = evt['end'].replace(':', '') + "00"
            
            final_summary = evt.get('summary', 'Cours')
            priority = "5"
            
            if "EXAMEN" in final_summary.upper():
                if "🔴" not in final_summary: final_summary = "🔴 " + final_summary
                priority = "1"

            ics.append("BEGIN:VEVENT")
            ics.append(f"DTSTART:{d}T{s}")
            ics.append(f"DTEND:{d}T{e}")
            ics.append(f"SUMMARY:{final_summary}")
            ics.append(f"LOCATION:{evt.get('location', '')}")
            ics.append(f"PRIORITY:{priority}")
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
    
    # DATES DE DÉBUT DE SEMAINE (Fixées pour l'exemple 2026)
    # Page 1 = Semaine du 12 Janvier
    start_dates = ["2026-01-12", "2026-01-19", "2026-01-26", "2026-02-02", "2026-02-09"]

    print(f"Traitement de {len(images)} pages...")
    for i, img in enumerate(images):
        print(f"--- Page {i+1} ---")
        
        # Appel API Robuste
        page_events = get_schedule_robust(img)
        
        # Attribution des dates réelles
        week_start = start_dates[i] if i < len(start_dates) else "2026-01-01"
        
        for evt in page_events:
            real_date = calculate_real_date(week_start, evt.get('day', ''))
            if real_date:
                evt['real_date'] = real_date
                all_events.append(evt)
        
        print(f"   ✅ {len(page_events)} cours bruts récupérés.")
        time.sleep(2)

    print("Génération ICS...")
    ics_content = create_ics_file(all_events)

    with open(OUTPUT_FILE, "w", encoding="utf-8") as f:
        f.write(ics_content)
    print(f"Terminé. Fichier généré : {OUTPUT_FILE}")

if __name__ == "__main__":
    main()
