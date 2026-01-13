import os
import requests
import json
import google.generativeai as genai
from pdf2image import convert_from_path
from ics import Calendar, Event
from datetime import datetime
from pytz import timezone
import urllib3

# --- CONFIGURATION ---
PDF_URL = "https://stri.fr/Gestion_STRI/TAV/L3/EDT_STRI1A_L3IRT_TAV.pdf"
API_KEY = os.environ.get("GEMINI_API_KEY")
TZ = timezone('Europe/Paris')

# Désactiver les avertissements SSL
urllib3.disable_warnings(urllib3.exceptions.InsecureRequestWarning)

# Configuration IA
genai.configure(api_key=API_KEY)

# Date actuelle pour l'IA
now = datetime.now(TZ)
date_str = now.strftime("%A %d %B %Y à %H:%M")

# --- TON PROMPT PERSONNALISÉ INTÉGRÉ ---
SYSTEM_PROMPT = f"""
Agis comme un assistant de planification personnel. Je te fournis mon emploi du temps (image ou fichier). Analyse-le en respectant strictement les règles de décodage ci-dessous pour l'étudiant en groupe "GB".

Date et Heure actuelle : {date_str}

RÈGLES DE LECTURE VISUELLE :
1. Structure des lignes : Si une journée (ligne horizontale) est divisée en deux sous-lignes, IGNORE la ligne du haut et les cases ORANGE. Ne lis que la ligne du bas.
2. Groupes : Ce qui est après le "/" concerne le groupe. Je suis le groupe GB. Si c'est marqué "/GC", ignore le cours. Prends uniquement "/GB" ou les cours sans mention de groupe (tronc commun).
3. Couleurs :
   - Case Jaune = EXAMEN (Priorité haute).
   - Case Orange = À IGNORER totalement.
   - Petits carrés verts (coin supérieur droit d'une case) = Numéro de la SALLE.
4. Professeurs : Les initiales entre parenthèses (...) sont les professeurs. Utilise la liste ci-dessous pour remplacer les initiales par les noms complets. Si pas de parenthèses, c'est un cours magistral ou un prof non listé.

LISTE DES INTERVENANTS (Dictionnaire) :
AnAn=Andréi ANDRÉI; AA=André AOUN; AB=Abdelmalek BENZEKRI; AL=Abir LARABA; BC=Bilal CHEBARO; BTJ=Boris TIOMELA JOU; CC=Cédric CHAMBAULT; CG=Christine GALY; CT=Cédric TEYSSIE; EG=Eric GONNEAU; EL=Emmanuel LAVINAL; FM=Frédéric MOUTIER; GR=Gérard ROUZIES; JGT=Jean-Guy TARTARIN; JS=Jérôme SOKOLOFF; KB=Ketty BRAVO; LC=Louisa COT; MCL=Marie-Christine LAGASQUIÉ; MM=MUSTAPHA MOJAHID; OC=Olivier CRIVELLARO; OM=Olfa MECHI; PA=Patrick AUSTIN; PhA=Philippe ARGUEL; PIL=Pierre LOTTE; PL=Philippe LATU; PT=Patrice TORGUET; RK=Rahim KACIMI; RL=Romain LABORDE; SB=Sonia BADENE; SL=Séverine LALANDE; TD=Thierry DESPRATS; TG=Thierry GAYRAUD.

CONSIGNES DE SORTIE (FORMAT JSON STRICT) :
Attention : Je ne veux pas de liste à puces. Je veux UNIQUEMENT une liste d'objets JSON pour que mon programme puisse créer le calendrier.
Format attendu :
[
  {{
    "summary": "Nom du cours (ex: Télécoms (Jean-Guy TARTARIN))",
    "location": "Salle (ex: U3-Amphi)",
    "start": "YYYY-MM-DD HH:MM", 
    "end": "YYYY-MM-DD HH:MM"
  }}
]

DATES :
- Repère la date du Lundi en haut de l'image.
- Déduis l'année intelligemment.
- Calcule la date précise (start/end) pour chaque cours de la semaine visible.
"""

def download_pdf():
    print("Téléchargement du PDF...")
    # On se fait passer pour un navigateur (Chrome) pour éviter le blocage
    headers = {
        "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36"
    }
    try:
        r = requests.get(PDF_URL, headers=headers, verify=False, timeout=30)
        
        # Vérification que c'est bien un PDF
        content_type = r.headers.get('Content-Type', '').lower()
        if 'html' in content_type:
            print("ERREUR FATALE: Le site a renvoyé une page Web au lieu du PDF.")
            print("Contenu reçu (début):", r.text[:200])
            exit(1)
            
        with open("edt.pdf", "wb") as f:
            f.write(r.content)
        print("PDF téléchargé avec succès.")
    except Exception as e:
        print(f"Erreur téléchargement: {e}")
        exit(1)

def extract_schedule_ai():
    print("Conversion PDF -> Images...")
    try:
        images = convert_from_path("edt.pdf")
    except Exception as e:
        print(f"Erreur Poppler (Fichier invalide ?): {e}")
        exit(1)

    model = genai.GenerativeModel('gemini-1.5-flash')
    all_events = []

    for i, img in enumerate(images):
        print(f"--- Analyse IA Page {i+1} ---")
        try:
            response = model.generate_content([SYSTEM_PROMPT, img])
            text = response.text.strip()
            
            # Nettoyage JSON
            if "```" in text:
                text = text.replace("```json", "").replace("```", "")
            
            data = json.loads(text)
            
            # Validation rapide
            valid_events = [d for d in data if d.get('summary')]
            print(f"   -> {len(valid_events)} cours extraits.")
            all_events.extend(valid_events)
            
        except Exception as e:
            print(f"Erreur IA sur la page {i+1}: {e}")

    return all_events

def create_ics(events_data):
    cal = Calendar()
    
    for item in events_data:
        try:
            e = Event()
            e.name = item.get('summary', 'Cours')
            e.location = item.get('location', '')
            
            dt_start = datetime.strptime(item['start'], "%Y-%m-%d %H:%M")
            dt_end = datetime.strptime(item['end'], "%Y-%m-%d %H:%M")
            
            e.begin = TZ.localize(dt_start)
            e.end = TZ.localize(dt_end)
            
            cal.events.add(e)
        except Exception as e:
            print(f"Erreur event: {item} -> {e}")

    with open("edt.ics", "w") as f:
        f.write(cal.serialize())
    print("Fichier ICS généré avec succès.")

if __name__ == "__main__":
    if not API_KEY:
        print("ERREUR: Clé API manquante.")
        exit(1)
    
    download_pdf()
    data = extract_schedule_ai()
    if data:
        create_ics(data)
    else:
        print("Aucune donnée trouvée.")
