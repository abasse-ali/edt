import requests
import pdfplumber
import re
from datetime import datetime, timedelta
from ics import Calendar, Event
from pytz import timezone

# --- CONFIGURATION ---
PDF_URL = "https://stri.fr/Gestion_STRI/TAV/L3/EDT_STRI1A_L3IRT_TAV.pdf"
MY_GROUP = "GB"  # Ton groupe
IGNORE_GROUP = "GC"  # Le groupe à ignorer
TZ = timezone('Europe/Paris')
YEAR = 2026  # Lundi 12 janv tombe en 2026 (si c'est 2025, change ici)

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
    print("Téléchargement du PDF...")
    try:
        response = requests.get(PDF_URL, verify=False) # verify=False si certificat SSL STRI pose souci
        with open("edt.pdf", "wb") as f:
            f.write(response.content)
        print("PDF téléchargé.")
    except Exception as e:
        print(f"Erreur téléchargement: {e}")

def parse_month(month_str):
    months = {
        "janv": 1, "févr": 2, "mars": 3, "avr": 4, "mai": 5, "juin": 6,
        "juil": 7, "août": 8, "sept": 9, "oct": 10, "nov": 11, "déc": 12
    }
    return months.get(month_str.lower())

def is_exam_color(rect):
    """Détecte si un rectangle est jaune (Examen)"""
    if not rect.get('non_stroking_color'): return False
    c = rect['non_stroking_color']
    # Jaune = Rouge élevé, Vert élevé, Bleu faible
    if len(c) >= 3 and c[0] > 0.8 and c[1] > 0.8 and c[2] < 0.5:
        return True
    return False

def extract_schedule():
    cal = Calendar()
    print("Début de l'analyse...")

    with pdfplumber.open("edt.pdf") as pdf:
        for page_num, page in enumerate(pdf.pages):
            print(f"Analyse page {page_num + 1}...")
            words = page.extract_words(x_tolerance=2, y_tolerance=2)
            rects = page.rects

            # 1. REPÉRAGE DE LA GRILLE TEMPORELLE (X-AXIS)
            # On cherche les textes "8h", "9h"... pour calibrer l'axe horizontal
            hours_x = {}
            for w in words:
                if re.match(r'^\d{1,2}h$', w['text']):
                    h = int(w['text'].replace('h', ''))
                    hours_x[h] = w['x0']
            
            if not hours_x or len(hours_x) < 2:
                print("Impossible de trouver la grille horaire sur cette page.")
                continue

            # Création d'une fonction de conversion X -> Heure
            # On prend 8h et 18h comme repères (ou les min/max trouvés)
            min_h, max_h = min(hours_x.keys()), max(hours_x.keys())
            px_start, px_end = hours_x[min_h], hours_x[max_h]
            
            def get_time_from_x(x):
                # Interpolation linéaire
                ratio = (x - px_start) / (px_end - px_start)
                hour_float = min_h + ratio * (max_h - min_h)
                
                # Arrondi au quart d'heure (0, 15, 30, 45)
                total_minutes = hour_float * 60
                remainder = total_minutes % 15
                if remainder < 8: total_minutes -= remainder
                else: total_minutes += (15 - remainder)
                return int(total_minutes // 60), int(total_minutes % 60)

            # 2. REPÉRAGE DES JOURS (Y-AXIS)
            # On cherche "Lundi", "Mardi"... et la date associée
            days_y = [] # Liste de dictionnaires {day_name, date_obj, y_top, y_bottom}
            
            # Mots clés des jours
            day_names = ["Lundi", "Mardi", "Mercredi", "Jeudi", "Vendredi"]
            
            # Trouver la date de la semaine (ex: "12/janv")
            week_start_date = None
            for w in words:
                if '/' in w['text'] and w['x0'] < 100: # Date est à gauche
                    parts = w['text'].split('/')
                    if len(parts) == 2:
                        m = parse_month(parts[1])
                        if m:
                            # C'est la date du Lundi
                            try:
                                week_start_date = datetime(YEAR, m, int(parts[0]))
                            except: pass
            
            if not week_start_date:
                print("Pas de date trouvée sur cette page.")
                continue

            # Trouver les positions Y de chaque jour
            sorted_words = sorted(words, key=lambda w: w['top'])
            current_day = None
            
            for w in sorted_words:
                if w['text'] in day_names and w['x0'] < 100:
                    if current_day:
                        current_day['y_bottom'] = w['top'] # Le jour précédent finit ici
                        days_y.append(current_day)
                    
                    day_offset = day_names.index(w['text'])
                    current_day = {
                        'name': w['text'],
                        'date': week_start_date + timedelta(days=day_offset),
                        'y_top': w['top'],
                        'y_bottom': None # Sera défini au prochain jour
                    }
            if current_day:
                current_day['y_bottom'] = page.height # Le dernier jour va jusqu'en bas
                days_y.append(current_day)

            # 3. ANALYSE DES COURS
            # On filtre les mots qui ne sont ni des headers ni des dates
            content_words = [w for w in words if w['x0'] > px_start - 20]

            for day in days_y:
                # Récupérer les mots dans la bande Y du jour
                day_words = [w for w in content_words if day['y_top'] <= w['top'] + (w['height']/2) < day['y_bottom']]
                
                # Grouper les mots par proximité spatiale
                # On trie par X (heure) puis Y (ligne)
                day_words.sort(key=lambda w: (w['x0'], w['top']))
                
                blocks = []
                while day_words:
                    current = day_words.pop(0)
                    # Créer un bloc avec ce mot
                    block_words = [current]
                    
                    # Chercher les voisins proches
                    # Tolérance: meme créneau horaire (X proche) et verticalement connecté
                    to_remove = []
                    for other in day_words:
                        # Si overlap horizontal (même heure) ET proche verticalement
                        if (abs(other['x0'] - current['x0']) < 150) and (abs(other['top'] - current['top']) < 30):
                             block_words.append(other)
                             to_remove.append(other)
                             # Mettre à jour la "box" du bloc courant pour attraper les suivants
                             current = other 
                    
                    for r in to_remove:
                        if r in day_words: day_words.remove(r)
                    
                    blocks.append(block_words)

                # Traiter chaque bloc
                for block in blocks:
                    # Coordonnées du bloc
                    b_x0 = min(w['x0'] for w in block)
                    b_x1 = max(w['x1'] for w in block)
                    b_top = min(w['top'] for w in block)
                    b_bottom = max(w['bottom'] for w in block)
                    text = " ".join([w['text'] for w in block])

                    # --- FILTRES ---
                    
                    # 1. Filtre GROUPE (GB vs GC)
                    # Si le texte contient GC et pas GB, on jette
                    if IGNORE_GROUP in text and MY_GROUP not in text:
                        continue
                    # Si c'est séparé par un slash ex: "Info (OM)/GC", c'est souvent la ligne du haut
                    if "/" in text and MY_GROUP not in text and "Commun" not in text:
                        # Petite sécurité : si le texte est très court, c'est peut-être juste le nom du prof
                        if len(text) > 5: 
                            pass # On garde pour l'instant, le filtre spatial est plus fiable

                    # 2. Filtre LIGNE DU HAUT (Si 2 cours au même moment)
                    # On regarde s'il existe un AUTRE bloc au MEME moment (overlap X) mais EN DESSOUS (y plus grand)
                    # Si oui, et qu'on est GB (souvent en bas), on garde le bas.
                    # L'utilisateur a dit: "la première rangée... ne me concerne pas". 
                    # Donc si on détecte une superposition verticale stricte, on prend celle du BAS.
                    # Note: C'est risqué, on va plutôt se fier au texte "/GB".

                    # Nettoyage et Profs
                    clean_text = text
                    for sigle, nom in PROFS.items():
                        clean_text = clean_text.replace(f"({sigle})", f"({nom})")

                    # Détection Exam (Jaune)
                    is_exam = False
                    block_center_x = (b_x0 + b_x1) / 2
                    block_center_y = (b_top + b_bottom) / 2
                    for r in rects:
                        if is_exam_color(r):
                            # Si le centre du bloc est dans le rectangle
                            if r['x0'] < block_center_x < r['x1'] and r['top'] < block_center_y < r['bottom']:
                                is_exam = True

                    # Calcul Heures
                    h_start, m_start = get_time_from_x(b_x0)
                    h_end, m_end = get_time_from_x(b_x1)
                    
                    # Correction fin de journée (parfois déborde)
                    if h_end > 20: h_end = 20

                    start_dt = day['date'].replace(hour=h_start, minute=m_start, tzinfo=TZ)
                    end_dt = day['date'].replace(hour=h_end, minute=m_end, tzinfo=TZ)
                    
                    # Si durée < 30 min, c'est probablement un artefact ou erreur
                    if (end_dt - start_dt).total_seconds() < 1800:
                        continue

                    # Création Event
                    e = Event()
                    e.name = f"{'EXAM: ' if is_exam else ''}{clean_text}"
                    e.begin = start_dt
                    e.end = end_dt
                    
                    # Salles (Regex pour U3-xxx, Amphi...)
                    salle_match = re.search(r'(U\d-\w+|Amphi|U\d-Amphi)', text)
                    if salle_match:
                        e.location = salle_match.group(0)
                    
                    cal.events.add(e)

    with open("edt.ics", "w") as f:
        f.write(cal.serialize())
    print("Fichier edt.ics généré avec succès.")

if __name__ == "__main__":
    download_pdf()
    extract_schedule()
