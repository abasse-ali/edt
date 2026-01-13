import os
import requests
import json
import google.generativeai as genai
from pdf2image import convert_from_path
from ics import Calendar, Event
from datetime import datetime
from pytz import timezone

# --- CONFIGURATION ---
PDF_URL = "https://stri.fr/Gestion_STRI/TAV/L3/EDT_STRI1A_L3IRT_TAV.pdf"
API_KEY = os.environ.get("GEMINI_API_KEY")
TZ = timezone('Europe/Paris')

# Configuration IA
genai.configure(api_key=API_KEY)

# Date actuelle pour aider l'IA
now = datetime.now(TZ)
date_str = now.strftime("%d/%m/%Y à %H:%M")

# --- TON PROMPT INTÉGRÉ ---
SYSTEM_PROMPT = f"""
Agis comme un assistant de planification personnel expert.
Ta tâche est de convertir une image d'emploi du temps en données JSON structurées.

Date et Heure actuelle du traitement : {date_str}

RÈGLES DE LECTURE VISUELLE STRICTES (Étudiant Groupe "GB") :
1. **STRUCTURE DES LIGNES (CRITIQUE)** : Si une case horaire est divisée horizontalement en deux sous-lignes :
   - IGNORE la ligne du HAUT.
   - NE LIS QUE la ligne du BAS (c'est souvent là qu'est le groupe GB).
2. **GROUPES** :
   - Regarde après le "/" : Si c'est marqué "/GC", IGNORE le cours.
   - Prends uniquement "/GB" ou les cours sans mention de groupe (tronc commun).
3. **COULEURS** :
   - Case Jaune = EXAMEN (Ajoute "EXAM: " au début du titre).
   - Case Orange = À IGNORER totalement.
   - Petits carrés verts (coin supérieur droit) = Numéro de la SALLE.
4. **PROFESSEURS** : Remplace les sigles par les noms complets selon cette liste :
   AnAn=Andréi ANDRÉI; AA=André AOUN; AB=Abdelmalek BENZEKRI; AL=Abir LARABA; BC=Bilal CHEBARO; BTJ=Boris TIOMELA JOU; CC=Cédric CHAMBAULT; CG=Christine GALY; CT=Cédric TEYSSIE; EG=Eric GONNEAU; EL=Emmanuel LAVINAL; FM=Frédéric MOUTIER; GR=Gérard ROUZIES; JGT=Jean-Guy TARTARIN; JS=Jérôme SOKOLOFF; KB=Ketty BRAVO; LC=Louisa COT; MCL=Marie-Christine LAGASQUIÉ; MM=MUSTAPHA MOJAHID; OC=Olivier CRIVELLARO; OM=Olfa MECHI; PA=Patrick AUSTIN; PhA=Philippe ARGUEL; PIL=Pierre LOTTE; PL=Philippe LATU; PT=Patrice TORGUET; RK=Rahim KACIMI; RL=Romain LABORDE; SB=Sonia BADENE; SL=Séverine LALANDE; TD=Thierry DESPRATS; TG=Thierry GAYRAUD.

CONSIGNES DE SORTIE (FORMAT JSON UNIQUEMENT) :
Tu dois extraire TOUS les cours valides de la semaine affichée sur l'image.
Ne fais pas de phrases. Renvoie uniquement une liste d'objets JSON respectant ce format :
[
  {{
    "summary": "Nom du cours (ex: Télécoms (Jean-Guy TARTARIN))",
    "location": "Salle (ex: U3-Amphi)",
    "start": "YYYY-MM-DD HH:MM", 
    "end": "YYYY-MM-DD HH:MM"
  }}
]

DATES :
- Repère la date du Lundi en haut de l'image (ex: "12/janv").
- Déduis l'année (Si on est en {now.year}, adapte l'année scolaire intelligemment).
- Calcule la date précise (start/end) pour chaque cours.
"""

def download_pdf():
    print("Téléchargement du PDF...")
    try:
        r = requests.get(PDF_URL, verify=False, timeout=30)
        with open("edt.pdf", "wb") as f:
            f.write(r.content)
    except Exception as e:
        print(f"Erreur téléchargement: {e}")
        exit(1)

def extract_schedule_ai():
    print("Conversion PDF -> Images...")
    try:
        images = convert_from_path("edt.pdf")
    except Exception as e:
        print(f"Erreur Poppler: {e}")
        exit(1)

    model = genai.GenerativeModel('gemini-1.5-flash')
    all_events = []

    for i, img in enumerate(images):
        print(f"--- Analyse IA Page {i+1} ---")
        try:
            # Appel API Gemini avec ton prompt
            response = model.generate_content([SYSTEM_PROMPT, img])
            text = response.text.strip()
            
            # Nettoyage du Markdown json
            if "```" in text:
                text = text.replace("```json", "").replace("```", "")
            
            data = json.loads(text)
            
            # Petit filtrage de sécurité (supprime les erreurs vides)
            valid_events = [d for d in data if d.get('summary') and len(d['summary']) > 2]
            
            print(f"   -> {len(valid_events)} cours extraits.")
            all_events.extend(valid_events)
            
        except Exception as e:
            print(f"Erreur IA sur la page {i+1}: {e}")
            # print(text) # Décommenter pour debug

    return all_events

def create_ics(events_data):
    cal = Calendar()
    
    for item in events_data:
        try:
            e = Event()
            e.name = item.get('summary', 'Cours')
            e.location = item.get('location', '')
            
            # Parsing des dates renvoyées par l'IA
            dt_start = datetime.strptime(item['start'], "%Y-%m-%d %H:%M")
            dt_end = datetime.strptime(item['end'], "%Y-%m-%d %H:%M")
            
            # Ajout du fuseau horaire Paris
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
