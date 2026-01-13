import os
import requests
import json
import google.generativeai as genai
from pdf2image import convert_from_path
from ics import Calendar, Event
from datetime import datetime
from pytz import timezone

# --- CONFIG ---
PDF_URL = "https://stri.fr/Gestion_STRI/TAV/L3/EDT_STRI1A_L3IRT_TAV.pdf"
API_KEY = os.environ.get("GEMINI_API_KEY")
MY_GROUP = "GB"

# Configuration Gemini
genai.configure(api_key=API_KEY)

# Prompt Système : C'est ici qu'on donne l'intelligence à l'IA
SYSTEM_PROMPT = """
Tu es un assistant administratif précis. Ta tâche est d'extraire l'emploi du temps d'une image de calendrier universitaire.
Voici les règles STRICTES :
1. Analyse l'image fournie (une page d'emploi du temps).
2. Repère la semaine et l'année. Si l'année n'est pas écrite, déduis-la intelligemment (si on est en janvier, c'est l'année actuelle, si septembre c'est l'année scolaire en cours).
3. Extrais CHAQUE cours sous forme d'objet JSON.
4. FILTRE DE GROUPE : L'étudiant est dans le groupe "GB".
   - Si une case est divisée en deux horizontalement (une ligne haut, une ligne bas), le groupe GB est souvent en BAS.
   - Si le texte mentionne "GC" explicitement et pas "GB", IGNORE ce cours.
   - Si le cours est commun (pas de groupe mentionné), GARDE-LE.
5. EXAMENS : Les cases sur fond JAUNE sont des examens. Ajoute "EXAM: " au début du nom.
6. FORMAT JSON ATTENDU : Une liste d'objets avec :
   - "summary": Nom du cours (nettoyé, remplace les sigles profs par leur nom complet si possible).
   - "location": Salle (ex: U3-Amphi, U3-203).
   - "start": Date et heure de début au format "YYYY-MM-DD HH:MM".
   - "end": Date et heure de fin au format "YYYY-MM-DD HH:MM".

Liste des profs pour référence (remplace les sigles) :
AnAn=Andréi ANDRÉI, AA=André AOUN, JGT=Jean-Guy TARTARIN, PT=Patrice TORGUET, MM=MUSTAPHA MOJAHID, OM=Olfa MECHI.

Ne réponds QUE le JSON, rien d'autre. Pas de markdown ```json```.
"""

def download_pdf():
    print("Téléchargement PDF...")
    r = requests.get(PDF_URL, verify=False)
    with open("edt.pdf", "wb") as f:
        f.write(r.content)

def get_schedule_from_ai():
    # Conversion PDF -> Images
    print("Conversion PDF en images...")
    try:
        images = convert_from_path("edt.pdf")
    except Exception as e:
        print(f"Erreur conversion image (Poppler installé ?): {e}")
        return []

    model = genai.GenerativeModel('gemini-1.5-flash')
    full_schedule = []

    for i, img in enumerate(images):
        print(f"Analyse IA de la page {i+1}...")
        try:
            # Envoi à l'IA
            response = model.generate_content([SYSTEM_PROMPT, img])
            text_resp = response.text.strip()
            
            # Nettoyage si l'IA met des balises markdown
            if text_resp.startswith("```json"):
                text_resp = text_resp.replace("```json", "").replace("```", "")
            
            data = json.loads(text_resp)
            full_schedule.extend(data)
        except Exception as e:
            print(f"Erreur IA sur page {i+1}: {e}")
            print("Réponse brute:", response.text if 'response' in locals() else "None")

    return full_schedule

def create_ics(json_data):
    cal = Calendar()
    tz = timezone('Europe/Paris')

    for item in json_data:
        try:
            e = Event()
            e.name = item.get('summary', 'Cours')
            e.location = item.get('location', '')
            
            # Parsing dates
            # Le format attendu est YYYY-MM-DD HH:MM
            start_local = datetime.strptime(item['start'], "%Y-%m-%d %H:%M")
            end_local = datetime.strptime(item['end'], "%Y-%m-%d %H:%M")
            
            # Ajout Timezone
            e.begin = tz.localize(start_local)
            e.end = tz.localize(end_local)
            
            cal.events.add(e)
        except Exception as e:
            print(f"Erreur création event {item}: {e}")

    with open("edt.ics", "w") as f:
        f.write(cal.serialize())
    print("Fichier edt.ics généré via IA.")

if __name__ == "__main__":
    download_pdf()
    schedule_data = get_schedule_from_ai()
    if schedule_data:
        create_ics(schedule_data)
    else:
        print("Aucune donnée extraite par l'IA.")
