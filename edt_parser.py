import requests
import pdfplumber
import re
from datetime import datetime, timedelta
from ics import Calendar, Event
from pytz import timezone

# --- CONFIGURATION ---
PDF_URL = "https://stri.fr/Gestion_STRI/TAV/L3/EDT_STRI1A_L3IRT_TAV.pdf"
MY_GROUP = "GB"
IGNORE_GROUP = "GC"
TZ = timezone('Europe/Paris')
YEAR = 2025  # À ajuster selon l'année scolaire en cours ou à automatiser

# Mapping des profs
PROFS = {
    "AnAn": "Andréi ANDRÉI", "AA": "André AOUN", "AB": "Abdelmalek BENZEKRI",
    "AL": "Abir LARABA", "BC": "Bilal CHEBARO", "BTJ": "Boris TIOMELA JOU",
    "CC": "Cédric CHAMBAULT", "CG": "Christine GALY", "CT": "Cédric TEYSSIE",
    "EG": "Eric GONNEAU", "EL": "Emmanuel LAVINAL", "FM": "Frédéric MOUTIER",
    "GR": "Gérard ROUZIES", "JGT": "Jean-Guy TARTARIN", "JS": "Jérôme SOKOLOFF",
    "KB": "Ketty BRAVO", "LC": "Louisa COT", "MCL": "Marie-Christine LAGASQUIÉ",
    "MM": "MUSTAPHA MOJAHID", "OC": "Olivier CRIVELLARO", "OM": "Olfa MECHI",
    "PA": "Patrick AUSTIN", "PhA": "Philippe ARGUEL", "PIL": "Pierre LOTTE",
    "PL": "Philippe LATU", "PT": "Patrice TORGUET", "RK": "Rahim KACIMI",
    "RL": "Romain LABORDE", "SB": "Sonia BADENE", "SL": "Séverine LALANDE",
    "TD": "Thierry DESPRATS", "TG": "Thierry GAYRAUD"
}

def download_pdf():
    response = requests.get(PDF_URL)
    with open("edt.pdf", "wb") as f:
        f.write(response.content)

def parse_date(date_str):
    # Format attendu: "12/janv"
    try:
        day, month_str = date_str.split('/')
        months = {
            "janv": 1, "févr": 2, "mars": 3, "avr": 4, "mai": 5, "juin": 6,
            "juil": 7, "août": 8, "sept": 9, "oct": 10, "nov": 11, "déc": 12
        }
        month = months.get(month_str.lower())
        if not month: return None
        return datetime(YEAR, month, int(day))
    except:
        return None

def extract_schedule():
    cal = Calendar()
    
    with pdfplumber.open("edt.pdf") as pdf:
        for page in pdf.pages:
            words = page.extract_words()
            rects = page.rects # Pour détecter les couleurs (Examens)
            
            # 1. Trouver les dates (lundi 12/janv, etc.)
            # On cherche les textes qui ressemblent à une date dans l'en-tête
            # Ceci est une simplification, on assume que les jours sont alignés en colonnes
            days_coords = [] # Stocke (x_start, x_end, date_obj)
            
            # Calibration approximative des colonnes (à ajuster si besoin)
            # Page largeur typique A4 landscape ~842 points
            # On découpe la page verticalement en 5 jours
            page_width = page.width
            col_width = (page_width - 50) / 5 # 50pts marge gauche
            
            # On scanne le haut de page pour trouver la date précise
            header_words = [w for w in words if w['top'] < 100]
            current_week_dates = []
            
            for w in header_words:
                if '/' in w['text']:
                    d = parse_date(w['text'])
                    if d:
                        current_week_dates.append({'date': d, 'x': w['x0']})
            
            # Trier les dates trouvées par position X
            current_week_dates.sort(key=lambda k: k['x'])
            
            if not current_week_dates:
                continue

            # Définir les colonnes basées sur les dates trouvées
            # On assume 5 jours : Lundi, Mardi, Mercredi, Jeudi, Vendredi
            for i, date_info in enumerate(current_week_dates):
                x_start = date_info['x'] - 20
                x_end = x_start + col_width
                days_coords.append({
                    "date": date_info['date'],
                    "x_start": x_start,
                    "x_end": x_end
                })

            # 2. Analyser le contenu des colonnes
            # Grille de temps : Y commence vers 100 (à vérifier) et finit vers 500
            # 7h45 correspond au Y du haut. 15min = environ 12-15 pixels de hauteur (à calibrer)
            y_start_grid = 110 # Début du premier cours (7h45)
            pixels_per_hour = 60 # Estimation
            
            # Récupérer tous les textes qui ne sont pas l'en-tête
            content_words = [w for w in words if w['top'] > 100]
            
            # Grouper les mots par proximité pour former des blocs de cours
            # C'est la partie complexe. Pour simplifier, on itère par jour.
            
            for day in days_coords:
                # Filtrer les mots qui sont dans la colonne de ce jour
                day_words = [w for w in content_words if day['x_start'] <= w['x0'] <= day['x_end']]
                
                # Regrouper les mots proches verticalement (même créneau)
                blocks = []
                if not day_words: continue
                
                day_words.sort(key=lambda w: w['top'])
                
                current_block = [day_words[0]]
                for w in day_words[1:]:
                    # Si le mot est proche du précédent (< 10 px verticalement), c'est le même bloc
                    if w['top'] - current_block[-1]['bottom'] < 20: 
                        current_block.append(w)
                    else:
                        blocks.append(current_block)
                        current_block = [w]
                blocks.append(current_block)
                
                for block in blocks:
                    block_text = " ".join([b['text'] for b in block])
                    top_y = min([b['top'] for b in block])
                    bottom_y = max([b['bottom'] for b in block])
                    mid_y = (top_y + bottom_y) / 2
                    
                    # FILTRE 1 : Règle "Première rangée ne me concerne pas"
                    # Si dans ce créneau (même Y), il y a un autre bloc au DESSUS dans la même colonne
                    # C'est difficile à détecter sans structure stricte.
                    # On va plutôt utiliser le filtre de groupe.
                    
                    # FILTRE 2 : Groupes
                    if IGNORE_GROUP in block_text and MY_GROUP not in block_text:
                        continue # C'est pour GC uniquement
                    
                    if "/" in block_text:
                        # S'il y a un slash, on vérifie si GB est présent
                        if MY_GROUP not in block_text and "Commun" not in block_text:
                             # Parfois les cours communs n'ont pas de groupe, on garde par prudence
                             # Si le texte contient explicitement un autre groupe et pas GB, on saute
                             pass 

                    # Détection Exam (Case Jaune)
                    is_exam = False
                    for r in rects:
                        # Vérifier si un rectangle jaune est sous ce texte
                        # Couleur jaune en PDF (R,G,B) souvent (1, 1, 0) ou proche
                        if r['non_stroking_color'] and len(r['non_stroking_color']) >= 3:
                            c = r['non_stroking_color']
                            if c[0] > 0.9 and c[1] > 0.9 and c[2] < 0.2: # Jaune
                                # Vérifier l'intersection
                                if (r['x0'] < day['x_end'] and r['x1'] > day['x_start'] and
                                    r['top'] <= mid_y <= r['bottom']):
                                    is_exam = True

                    # Calcul de l'heure
                    # 7h45 est à y_start_grid.
                    # Delta Y = top_y - y_start_grid
                    # Conversion pixel -> minutes
                    minutes_from_start = ((top_y - y_start_grid) / pixels_per_hour) * 60
                    
                    # Arrondir aux 15 min les plus proches
                    start_minutes_total = 7*60 + 45 + minutes_from_start
                    remainder = start_minutes_total % 15
                    if remainder < 8: start_minutes_total -= remainder
                    else: start_minutes_total += (15 - remainder)
                    
                    h_start = int(start_minutes_total // 60)
                    m_start = int(start_minutes_total % 60)
                    
                    # Durée basée sur la hauteur du bloc
                    height = bottom_y - top_y
                    duration_minutes = (height / pixels_per_hour) * 60
                    # Arrondir durée (min 1h)
                    if duration_minutes < 45: duration_minutes = 60 # Min safety
                    
                    start_dt = day['date'].replace(hour=h_start, minute=m_start, tzinfo=TZ)
                    end_dt = start_dt + timedelta(minutes=duration_minutes)

                    # Remplacement des sigles profs
                    clean_text = block_text
                    for sigle, nom in PROFS.items():
                        clean_text = clean_text.replace(f"({sigle})", f"({nom})")

                    # Création événement
                    e = Event()
                    e.name = f"{'EXAM: ' if is_exam else ''}{clean_text}"
                    e.begin = start_dt
                    e.end = end_dt
                    e.description = block_text
                    
                    # Tentative d'extraction de salle (texte vert ou pattern U3-...)
                    # Regex simple pour trouver les salles courantes
                    room_match = re.search(r'(U3-\w+|U6-\w+|Amphi)', block_text)
                    if room_match:
                        e.location = room_match.group(0)

                    cal.events.add(e)

    with open("edt.ics", "w") as f:
        f.write(cal.serialize())

if __name__ == "__main__":
    download_pdf()
    extract_schedule()
