import os
import requests
import google.generativeai as genai
from pdf2image import convert_from_bytes
from datetime import datetime

# Configuration
PDF_URL = "https://stri.fr/Gestion_STRI/TAV/L3/EDT_STRI1A_L3IRT_TAV.pdf"
OUTPUT_FILE = "emploi_du_temps.ics"
API_KEY = os.environ.get("GEMINI_API_KEY")

# Liste des profs (injectée dans le prompt)
PROFS_DICT = """
AnAn=Andréi ANDRÉI; AA=André AOUN; AB=Abdelmalek BENZEKRI; AL=Abir LARABA; BC=Bilal CHEBARO; 
BTJ=Boris TIOMELA JOU; CC=Cédric CHAMBAULT; CG=Christine GALY; CT=Cédric TEYSSIE; EG=Eric GONNEAU; 
EL=Emmanuel LAVINAL; FM=Frédéric MOUTIER; GR=Gérard ROUZIES; JGT=Jean-Guy TARTARIN; JS=Jérôme SOKOLOFF; 
KB=Ketty BRAVO; LC=Louisa COT; MCL=Marie-Christine LAGASQUIÉ; MM=MUSTAPHA MOJAHID; OC=Olivier CRIVELLARO; 
OM=Olfa MECHI; PA=Patrick AUSTIN; PhA=Philippe ARGUEL; PIL=Pierre LOTTE; PL=Philippe LATU; PT=Patrice TORGUET; 
RK=Rahim KACIMI; RL=Romain LABORDE; SB=Sonia BADENE; SL=Séverine LALANDE; TD=Thierry DESPRATS; TG=Thierry GAYRAUD.
"""

def get_gemini_response(image):
    genai.configure(api_key=API_KEY)
    model = genai.GenerativeModel('gemini-1.5-flash')
    
    current_year = datetime.now().year
    
    prompt = f"""
    Agis comme un expert en extraction de données d'emploi du temps. Analyse cette image d'emploi du temps pour l'étudiant du groupe "GB".
    
    CONTEXTE :
    - Année courante : {current_year} (Utilise cette année pour les dates extraites comme "12/janv").
    - Ton but est de produire UNIQUEMENT le contenu textuel d'un fichier iCalendar (.ics).

    RÈGLES STRICTES DE LECTURE (VISUEL) :
    1.  **Lignes et Sous-lignes** : Si une journée a deux lignes horizontales, IGNORE celle du HAUT et les cases ORANGE. Lis uniquement la ligne du BAS.
    2.  **Groupes** : Ignore les cours marqués "/GC". Garde uniquement "/GB" ou les cours sans groupe (tronc commun).
    3.  **Horaires (TRES IMPORTANT)** : 
        - Début de journée : 07h45.
        - Chaque petite ligne verticale représente 15 minutes.
        - Les créneaux typiques sont : 07h45-09h45, 10h00-12h00, 13h30-15h30, 15h45-17h45. Ajuste selon les traits verticaux exacts.
    4.  **Lieux et Types** :
        - Petit carré vert (coin haut droite) = SALLE (ex: U3-Amphi).
        - Case Jaune = Mettre "EXAMEN: " au début du titre de l'événement.
    5.  **Professeurs** : Remplace les initiales par les noms complets selon ce dictionnaire : {PROFS_DICT}

    FORMAT DE SORTIE :
    - Retourne une structure RFC 5545 (iCalendar) valide.
    - Ne mets PAS de balises markdown (```ics). Juste le texte brut.
    - Commence par BEGIN:VCALENDAR et finis par END:VCALENDAR.
    - Pour chaque événement, inclus : SUMMARY (Matière + Prof), DTSTART, DTEND, LOCATION (Salle), DESCRIPTION (Groupe GB).
    """

    response = model.generate_content([prompt, image])
    return response.text

def main():
    print(f"Téléchargement du PDF depuis {PDF_URL}...")
    response = requests.get(PDF_URL)
    if response.status_code != 200:
        raise Exception("Erreur téléchargement PDF")

    print("Conversion du PDF en images...")
    # Convertit les pages du PDF en images pour que Gemini puisse "voir" les couleurs et positions
    images = convert_from_bytes(response.content)

    full_ics_content = "BEGIN:VCALENDAR\nVERSION:2.0\nPRODID:-//STRI//Groupe GB//FR\nCALSCALE:GREGORIAN\n"
    
    print(f"Traitement de {len(images)} pages...")
    for i, img in enumerate(images):
        print(f"Analyse de la page {i+1} avec Gemini...")
        ics_part = get_gemini_response(img)
        
        # Nettoyage basique pour fusionner les VEVENT
        lines = ics_part.splitlines()
        for line in lines:
            if line.startswith("BEGIN:VCALENDAR") or line.startswith("END:VCALENDAR") or line.startswith("VERSION:") or line.startswith("PRODID:"):
                continue
            if line.strip():
                full_ics_content += line + "\n"

    full_ics_content += "END:VCALENDAR"

    with open(OUTPUT_FILE, "w", encoding="utf-8") as f:
        f.write(full_ics_content)
    
    print(f"Fichier {OUTPUT_FILE} généré avec succès.")

if __name__ == "__main__":
    main()
