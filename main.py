import os
import requests
import google.generativeai as genai
from datetime import datetime

# Configuration
PDF_URL = "https://stri.fr/Gestion_STRI/TAV/L3/EDT_STRI1A_L3IRT_TAV.pdf"
API_KEY = os.environ["GEMINI_API_KEY"]

# Configuration de Gemini
genai.configure(api_key=API_KEY)

def download_pdf(url, filename="edt.pdf"):
    response = requests.get(url)
    if response.status_code == 200:
        with open(filename, 'wb') as f:
            f.write(response.content)
        return filename
    else:
        raise Exception(f"Erreur téléchargement PDF: {response.status_code}")

def generate_ics_content(pdf_path):
    # On utilise le modèle capable de vision/multimodal (Gemini 1.5 Flash est rapide et efficace pour ça)
    model = genai.GenerativeModel('gemini-1.5-flash')
    
    today_date = datetime.now().strftime("%d/%m/%Y %H:%M")

    # Ton prompt arrangé et nettoyé
    prompt = f"""
    Agis comme un expert en extraction de données d'emploi du temps.
    ANALYSE le fichier PDF fourni qui est un emploi du temps universitaire.
    
    CONTEXTE :
    - Date et Heure d'exécution du script : {today_date}
    - L'étudiant est dans le GROUPE : **GB**
    
    RÈGLES DE DÉCODAGE VISUEL STRICTES :
    1. **Horaires** : La journée commence à 07h45. Chaque "carreau" (ligne fine) verticale représente 15 minutes.
       - Exemple : Un cours de 2h commence souvent à 7h45, 10h00, 13h30 ou 15h45. Calcule l'heure précise en fonction de la position.
    2. **Groupes** : Regarde après le slash "/".
       - "/GB" -> À PRENDRE.
       - "/GC", "/GA" -> IGNORER.
       - Pas de groupe indiqué -> C'est un cours commun, À PRENDRE.
    3. **Lignes doubles** : Si une journée a deux sous-lignes horizontales, IGNORE la ligne du haut (souvent pour un autre groupe). Lis uniquement la ligne du bas qui te concerne.
    4. **Codes couleurs** :
       - Jaune = EXAMEN (Mets "EXAMEN: " au début du titre).
       - Orange = IGNORER (Vacances ou hors cursus).
    5. **Salles** : Les petits carrés verts ou le texte dans les coins (ex: U3-Amphi, U3-307) indiquent le lieu.
    
    DICTIONNAIRE DES PROFESSEURS (Remplace les initiales par le nom complet dans la description) :
    AnAn=Andréi ANDRÉI; AA=André AOUN; AB=Abdelmalek BENZEKRI; AL=Abir LARABA; BC=Bilal CHEBARO; 
    BTJ=Boris TIOMELA JOU; CC=Cédric CHAMBAULT; CG=Christine GALY; CT=Cédric TEYSSIE; EG=Eric GONNEAU; 
    EL=Emmanuel LAVINAL; FM=Frédéric MOUTIER; GR=Gérard ROUZIES; JGT=Jean-Guy TARTARIN; JS=Jérôme SOKOLOFF; 
    KB=Ketty BRAVO; LC=Louisa COT; MCL=Marie-Christine LAGASQUIÉ; MM=MUSTAPHA MOJAHID; OC=Olivier CRIVELLARO; 
    OM=Olfa MECHI; PA=Patrick AUSTIN; PhA=Philippe ARGUEL; PIL=Pierre LOTTE; PL=Philippe LATU; PT=Patrice TORGUET; 
    RK=Rahim KACIMI; RL=Romain LABORDE; SB=Sonia BADENE; SL=Séverine LALANDE; TD=Thierry DESPRATS; TG=Thierry GAYRAUD.
    
    TACHE :
    Génère un fichier au format **iCalendar (.ics)** valide contenant tous les cours détectés pour les semaines visibles dans le PDF.
    - Pour chaque événement (VEVENT), inclus :
      - SUMMARY: Nom du cours
      - DTSTART/DTEND: Dates et heures précises (Format YYYYMMDDTHHMMSS)
      - LOCATION: La salle
      - DESCRIPTION: Professeur (Nom complet) + Type de cours
    - Ne mets PAS de balises markdown (```ics). Donne juste le contenu brut du fichier.
    """

    # Envoi du fichier et du prompt
    myfile = genai.upload_file(pdf_path)
    response = model.generate_content([myfile, prompt])
    
    # Nettoyage basique au cas où le modèle mettrait du markdown
    content = response.text.replace("```ics", "").replace("```", "").strip()
    return content

if __name__ == "__main__":
    print("Téléchargement du PDF...")
    pdf_filename = download_pdf(PDF_URL)
    
    print("Analyse avec Gemini...")
    ics_content = generate_ics_content(pdf_filename)
    
    print("Sauvegarde du fichier ICS...")
    with open("emploi_du_temps.ics", "w", encoding="utf-8") as f:
        f.write(ics_content)
    
    print("Terminé !")
