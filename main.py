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

# Mapping profs
PROFS_DICT = """
AnAn=Andréi ANDRÉI; AA=André AOUN; AB=Abdelmalek BENZEKRI; AL=Abir LARABA; BC=Bilal CHEBARO; 
BTJ=Boris TIOMELA JOU; CC=Cédric CHAMBAULT; CG=Christine GALY; CT=Cédric TEYSSIE; EG=Eric GONNEAU; 
EL=Emmanuel LAVINAL; FM=Frédéric MOUTIER; GR=Gérard ROUZIES; JGT=Jean-Guy TARTARIN; JS=Jérôme SOKOLOFF; 
KB=Ketty BRAVO; LC=Louisa COT; MCL=Marie-Christine LAGASQUIÉ; MM=MUSTAPHA MOJAHID; OC=Olivier CRIVELLARO; 
OM=Olfa MECHI; PA=Patrick AUSTIN; PhA=Philippe ARGUEL; PIL=Pierre LOTTE; PL=Philippe LATU; PT=Patrice TORGUET; 
RK=Rahim KACIMI; RL=Romain LABORDE; SB=Sonia BADENE; SL=Séverine LALANDE; TD=Thierry DESPRATS; TG=Thierry GAYRAUD.
"""

def get_available_models():
    """Récupère les modèles dispos."""
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

def preprocess_and_clean_image(pil_image):
    """
    1. Efface le Orange (Sport/Annulé).
    2. Retourne l'image nettoyée.
    """
    img_array = np.array(pil_image)
    
    # Détection ORANGE élargie (Sport est souvent vif)
    # R > 200, G entre 100 et 210, B < 100
    red_cond = img_array[:, :, 0] > 200
    green_cond = (img_array[:, :, 1] > 100) & (img_array[:, :, 1] < 210)
    blue_cond = img_array[:, :, 2] < 150
    
    mask_orange = red_cond & green_cond & blue_cond
    
    # On remplace l'orange par du blanc
    img_array[mask_orange] = [255, 255, 255]
    
    return Image.fromarray(img_array)

def slice_image_into_days(pil_image):
    """
    Découpe l'image en bandes horizontales (une par jour).
    C'est une heuristique : on divise la hauteur par 5 (Lundi-Vendredi).
    Ce n'est pas parfait au pixel près mais ça aide l'IA à focus.
    """
    width, height = pil_image.size
    # On ignore l'en-tête (les premiers 10%)
    header_offset = int(height * 0.10)
    content_height = height - header_offset
    
    day_height = content_height / 5 # 5 jours ouvrés
    
    slices = []
    days = ["Lundi", "Mardi", "Mercredi", "Jeudi", "Vendredi"]
    
    for i in range(5):
        top = header_offset + (i * day_height)
        bottom = top + day_height
        # On ajoute une petite marge de sécurité pour ne pas couper le texte
        box = (0, int(top), width, int(bottom))
        slices.append((days[i], pil_image.crop(box)))
        
    return slices

def call_gemini_api(image, model_name, day_hint):
    url = f"https://generativelanguage.googleapis.com/v1beta/models/{model_name}:generateContent?key={API_KEY}"
    
    img_byte_arr = io.BytesIO()
    image.save(img_byte_arr, format='JPEG')
    b64_data = base64.b64encode(img_byte_arr.getvalue()).decode('utf-8')

    prompt = f"""
    Analyse cette portion d'emploi du temps correspondant au jour : {day_hint}.
    GROUPE CIBLE : GB (Groupe B).
    ANNÉE : 2026.

    RÈGLES VISUELLES CRITIQUES :
    1. **HAUT vs BAS** : Dans chaque colonne (créneau horaire), il peut y avoir deux matières l'une sur l'autre.
       - La matière du HAUT est pour le Groupe A (GA/GC) -> **IGNORE-LA**.
       - La matière du BAS est pour le Groupe B (GB) -> **GARDE-LA**.
       - Si le texte est centré (une seule matière), garde-le.
    
    2. **COULEUR** : Les cases ORANGES ont été effacées (blanches). Les cases JAUNES sont des EXAMENS.
    
    3. **HORAIRES** :
       L'image représente une seule ligne (un seul jour) de gauche à droite.
       - Créneau 1 (Gauche) : 07h45 - 09h45
       - Créneau 2 : 10h00 - 12h00
       - Créneau 3 : 13h30 - 15h30
       - Créneau 4 (Droite) : 15h45 - 17h45

    FORMAT JSON LIST :
    [
      {{ 
        "summary": "Matière (Prof)", 
        "start": "HH:MM", 
        "end": "HH:MM", 
        "location": "Salle",
        "is_exam": true/false
      }}
    ]
    Si aucun cours n'est pertinent pour le groupe GB, renvoie une liste vide [].
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

def get_schedule_robust(image, day_name):
    # Liste complète de modèles pour éviter l'échec
    available = get_available_models()
    priority_list = [
        # --- GÉNÉRATION 3 (Le futur / Cutting Edge) ---
        # Les plus intelligents actuellement, capacités de raisonnement "agentique" extrêmes.
        "gemini-3-pro-preview",    # Le plus puissant absolu (Raisonnement profond)
        "gemini-3-flash-preview",  # Plus intelligent que le 2.5 Pro mais rapide
    
        # --- GÉNÉRATION 2.5 (Le Standard Actuel / Stable) ---
        # L'équilibre parfait et la version de production recommandée.
        "gemini-2.5-pro",          # Le standard "Pro" stable (Penseur, codeur, multimodal)
        "gemini-2.5-flash",        # Le "Flash" nouvelle génération (Polyvalent)
        
        # --- GÉNÉRATION 2.0 (L'ancienne référence) ---
        # Toujours très capables, souvent utilisés en fallback.
        "gemini-2.0-flash-001",    # (Note: Sera retiré courant 2026)
        
        # --- MODÈLES "LITE" (Optimisés pour la vitesse/coût) ---
        # Moins "intelligents" sur les nuances, mais imbattables pour des tâches simples à haut volume.
        "gemini-2.5-flash-lite",   
        "gemini-2.0-flash-lite-preview-02-05",
    
        # --- GÉNÉRATION 1.5 (Legacy / Anciens) ---
        # Gardés pour la compatibilité, mais dépassés par les versions 2.0+ et 2.5+.
        "gemini-1.5-pro-latest",
        "gemini-1.5-pro",
        "gemini-1.5-flash-latest",
        "gemini-1.5-flash",
        "gemini-1.5-flash-8b"      # Le plus petit, pour des tâches très basiques
    ]
    models = [m for m in priority_list if m in available]
    if not models: models = ["gemini-1.5-flash"]

    for model in models:
        try:
            # print(f"   👉 {day_name} avec {model}...")
            response = call_gemini_api(image, model, day_name)
            if response.status_code == 200:
                raw = response.json()
                if 'candidates' in raw and raw['candidates']:
                    clean = clean_json_text(raw['candidates'][0]['content']['parts'][0]['text'])
                    return json.loads(clean)
            elif response.status_code in [429, 503]:
                continue # On passe au modèle suivant en silence
        except:
            continue
    return []

def calculate_date(week_start_date, day_name):
    # Fonction simple pour mapper Lundi->Date
    # On suppose que la page commence le 12 Janvier 2026 d'après vos logs précédents
    # (A adapter si vous voulez que ça détecte la date auto, mais fixons 2026)
    base_date = "2026-01-12" # Lundi de la première page visible
    days = ["Lundi", "Mardi", "Mercredi", "Jeudi", "Vendredi"]
    
    try:
        delta = days.index(day_name)
        # Calcul basique, pour un vrai système il faut lire la date sur l'image
        # Ici on simplifie pour l'exemple
        from datetime import datetime, timedelta
        dt = datetime.strptime(base_date, "%Y-%m-%d") + timedelta(days=delta)
        return dt.strftime("%Y-%m-%d")
    except:
        return "2026-01-01"

def create_ics_file(all_events):
    ics = [
        "BEGIN:VCALENDAR",
        "VERSION:2.0",
        "PRODID:-//STRI//Groupe GB//FR",
        "CALSCALE:GREGORIAN",
        "METHOD:PUBLISH"
    ]
    for date, events in all_events.items():
        for evt in events:
            try:
                # Filtrage ultime
                summary = evt.get('summary', '').upper()
                if "SPORT" in summary and "EXAMEN" not in summary: continue # Sécurité Sport
                
                d = date.replace('-', '')
                s = evt['start'].replace(':', '') + "00"
                e = evt['end'].replace(':', '') + "00"
                
                final_summary = evt.get('summary', 'Cours')
                priority = "5"
                if evt.get('is_exam') or "EXAMEN" in final_summary.upper():
                    if "EXAMEN" not in final_summary.upper():
                        final_summary = "🔴 [EXAMEN] " + final_summary
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

    final_calendar_data = {}

    # DATE DE DÉBUT (À ajuster chaque semaine ou lire sur l'image)
    # Pour ce test on fixe au 12 Janvier 2026 comme vu sur l'EDT
    from datetime import datetime, timedelta
    current_monday = datetime(2026, 1, 12) 

    print(f"Traitement de {len(images)} pages...")
    for i, img in enumerate(images):
        print(f"--- Page {i+1} ---")
        
        # 1. Nettoyage Couleur
        clean_img = preprocess_and_clean_image(img)
        
        # 2. Découpage par jour
        day_slices = slice_image_into_days(clean_img)
        
        for day_name, day_img in day_slices:
            print(f"   📅 Analyse {day_name}...")
            
            # Calcul de la date réelle
            day_offset = ["Lundi", "Mardi", "Mercredi", "Jeudi", "Vendredi"].index(day_name)
            real_date = (current_monday + timedelta(days=day_offset + (i*7))).strftime("%Y-%m-%d")
            
            # Appel API sur la BANDE du jour uniquement
            events = get_schedule_robust(day_img, day_name)
            
            if events:
                print(f"      ✅ {len(events)} cours.")
                final_calendar_data[real_date] = events
            else:
                print("      ❌ Aucun cours ou erreur.")
        
        # Pause pour quota
        time.sleep(2)

    print("Génération ICS...")
    ics_content = create_ics_file(final_calendar_data)

    with open(OUTPUT_FILE, "w", encoding="utf-8") as f:
        f.write(ics_content)
    print(f"Terminé. Fichier généré : {OUTPUT_FILE}")

if __name__ == "__main__":
    main()
