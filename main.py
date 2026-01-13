import os
import requests
from google import genai
from datetime import datetime

# Configuration
PDF_URL = "https://stri.fr/Gestion_STRI/TAV/L3/EDT_STRI1A_L3IRT_TAV.pdf"
API_KEY = os.environ["GEMINI_API_KEY"]

# Initialisation du client (Nouvelle méthode)
client = genai.Client(api_key=API_KEY)

def download_pdf(url, filename="edt.pdf"):
    response = requests.get(url)
    if response.status_code == 200:
        with open(filename, 'wb') as f:
            f.write(response.content)
        return filename
    else:
        raise Exception(f"Erreur téléchargement PDF: {response.status_code}")

def generate_ics_content(pdf_path):
    today_date = datetime.now().strftime("%d/%m/%Y %H:%M")

    # Prompt optimisé
    prompt = f"""
    Agis comme un expert en extraction de données d'emploi du temps.
    ANALYSE le fichier PDF fourni qui est un emploi du temps universitaire.
    
    CONTEXTE :
    - Date et Heure d'exécution du script : {today_date}
    - L'étudiant est dans le GROUPE : **GB**
    
    RÈGLES DE DÉCODAGE VISUEL STRICTES :
    1. **Horaires** : La journée commence à 07h45. Chaque "carreau" (ligne fine) verticale représente 15 minutes.
    2. **Groupes** : Regarde après le slash "/".
       - "/GB" -> À PRENDRE.
       - "/GC", "/GA" -> IGNORER.
       - Pas de groupe indiqué -> C'est un cours commun, À PRENDRE.
    3. **Lignes doubles** : Si une journée a deux sous-lignes horizontales, IGNORE la ligne du haut. Lis uniquement la ligne du bas qui te concerne.
    4. **Codes couleurs** :
       - Jaune = EXAMEN (Mets "EXAMEN: " au début du titre).
       - Orange = IGNORER.
    5. **Salles** : Les petits carrés verts ou le texte dans les coins indiquent le lieu.
    
    DICTIONNAIRE DES PROFESSEURS (Remplace les initiales par le nom complet) :
    AnAn=Andréi ANDRÉI; AA=André AOUN; AB=Abdelmalek BENZEKRI; AL=Abir LARABA; BC=Bilal CHEBARO; 
    BTJ=Boris TIOMELA JOU; CC=Cédric CHAMBAULT; CG=Christine GALY; CT=Cédric TEYSSIE; EG=Eric GONNEAU; 
    EL=Emmanuel LAVINAL; FM=Frédéric MOUTIER; GR=Gérard ROUZIES; JGT=Jean-Guy TARTARIN; JS=Jérôme SOKOLOFF; 
    KB=Ketty BRAVO; LC=Louisa COT; MCL=Marie-Christine LAGASQUIÉ; MM=MUSTAPHA MOJAHID; OC=Olivier CRIVELLARO; 
    OM=Olfa MECHI; PA=Patrick AUSTIN; PhA=Philippe ARGUEL; PIL=Pierre LOTTE; PL=Philippe LATU; PT=Patrice TORGUET; 
    RK=Rahim KACIMI; RL=Romain LABORDE; SB=Sonia BADENE; SL=Séverine LALANDE; TD=Thierry DESPRATS; TG=Thierry GAYRAUD.
    
    TACHE :
    Génère un fichier au format **iCalendar (.ics)** valide contenant tous les cours détectés.
    - Pour chaque événement (VEVENT), inclus : SUMMARY, DTSTART, DTEND, LOCATION, DESCRIPTION.
    - Format date : YYYYMMDDTHHMMSS
    - Donne juste le contenu brut, sans balises markdown.
    """

    # 1. Upload du fichier vers l'API Gemini (File API)
    # Cette étape est nécessaire pour les fichiers PDF avec la nouvelle librairie
    print("Envoi du fichier à Gemini...")
    file_upload = client.files.upload(path=pdf_path)

    # 2. Génération du contenu
    print("Analyse en cours...")
    response = client.models.generate_content(
        model='gemini-1.5-flash',
        contents=[file_upload, prompt]
    )
    
    # Nettoyage
    content = response.text.replace("```ics", "").replace("```", "").strip()
    return content

if __name__ == "__main__":
    print("Téléchargement du PDF...")
    pdf_filename = download_pdf(PDF_URL)
    
    try:
        ics_content = generate_ics_content(pdf_filename)
        
        print("Sauvegarde du fichier ICS...")
        with open("emploi_du_temps.ics", "w", encoding="utf-8") as f:
            f.write(ics_content)
        
        print("Terminé !")
        
    except Exception as e:
        print(f"Une erreur est survenue : {e}")
        exit(1)
