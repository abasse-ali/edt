import pdfplumber
import re
import requests
from datetime import datetime, timedelta
from ics import Calendar, Event
import pytz

# --- CONFIGURATION ---
PDF_URL = "https://stri.fr/Gestion_STRI/TAV/L3/EDT_STRI1A_L3IRT_TAV.pdf"
OUTPUT_FILE = "edt_stri.ics"
TZ = pytz.timezone("Europe/Paris")

# Mapping des mois pour la conversion
MOIS = {
    "janv": 1, "févr": 2, "mars": 3, "avr": 4, "mai": 5, "juin": 6,
    "juil": 7, "août": 8, "sept": 9, "oct": 10, "nov": 11, "déc": 12
}

def download_pdf(url, filename="temp.pdf"):
    print(f"Téléchargement de {url}...")
    response = requests.get(url)
    with open(filename, 'wb') as f:
        f.write(response.content)
    return filename

def get_anchors(page):
    """
    Trouve les coordonnées de '8h' et '9h' pour calibrer l'échelle de temps
    et trouve les coordonnées des Jours pour l'échelle verticale.
    """
    words = page.extract_words()
    
    # 1. Calibrer l'axe horizontal (Temps)
    x_8h = None
    x_9h = None
    
    for w in words:
        if w['text'] == '8h': x_8h = w['x0']
        if w['text'] == '9h': x_9h = w['x0']
    
    if x_8h and x_9h:
        pixels_per_hour = x_9h - x_8h
        # La grille commence 15 min (0.25h) avant 8h
        start_grid_x = x_8h - (pixels_per_hour * 0.25)
    else:
        # Valeurs par défaut si échec de détection (à ajuster si besoin)
        print("Attention: Calibration auto échouée, utilisation valeurs par défaut.")
        pixels_per_hour = 100 
        start_grid_x = 50

    # 2. Trouver les semaines et les jours (Axe Y)
    # On cherche les dates format "XX/Mois" (ex: 12/janv)
    week_anchors = []
    current_year = datetime.now().year # Attention au changement d'année
    
    # Regex pour trouver "12/janv" ou "02/févr"
    date_pattern = re.compile(r"(\d{1,2})/([a-zéû]+)")
    
    for w in words:
        match = date_pattern.match(w['text'])
        if match:
            day_num = int(match.group(1))
            month_str = match.group(2)
            month_num = MOIS.get(month_str, 1)
            
            # Gestion simple année scolaire: si mois < 8 (Août), c'est l'année N+1 par rapport à la rentrée
            # Pour faire simple ici on prend l'année courante du système
            dt = datetime(current_year, month_num, day_num)
            
            # On stocke la position Y (top) de cette semaine
            week_anchors.append({
                'date': dt,
                'top': w['top'],
                'bottom': w['bottom']
            })
            
    return start_grid_x, pixels_per_hour, week_anchors

def parse_pdf(pdf_path):
    calendar = Calendar()
    
    with pdfplumber.open(pdf_path) as pdf:
        for page in pdf.pages:
            start_x, px_per_h, weeks = get_anchors(page)
            
            # On trie les semaines par position verticale
            weeks.sort(key=lambda x: x['top'])
            
            # Récupérer tous les rectangles (les blocs de couleurs)
            rects = page.rects
            
            for rect in rects:
                # Filtrer: On ne garde que les rects qui ont une couleur de fond (non_stroking_color)
                # et qui sont assez grands pour être des cours
                if not rect.get('non_stroking_color'):
                    continue
                
                r_x0 = rect['x0']
                r_top = rect['top']
                r_width = rect['width']
                r_height = rect['height']
                
                # Ignorer les rectangles trop petits (lignes de séparation)
                if r_height < 10 or r_width < 10:
                    continue

                # 1. Identifier la semaine et le jour
                # On cherche dans quelle "bande" verticale se trouve ce rectangle
                found_date = None
                
                # Hauteur approximative d'une semaine sur le PDF
                week_height_approx = 100 # pixels, à estimer
                
                # Trouver à quelle semaine appartient ce bloc (basé sur Y)
                ref_week = None
                for i, w in enumerate(weeks):
                    # Si le rectangle est en dessous du début de la semaine
                    # et au dessus de la semaine suivante (s'il y en a une)
                    next_w_top = weeks[i+1]['top'] if i+1 < len(weeks) else 9999
                    
                    if w['top'] - 20 <= r_top < next_w_top - 20:
                        ref_week = w
                        break
                
                if not ref_week:
                    continue

                # Trouver le jour exact (Lundi, Mardi...) dans la semaine
                # On assume une hauteur standard par jour
                # Lundi est aligné avec la date. Mardi est en dessous, etc.
                # Dans votre PDF, chaque jour fait environ 1/5 de la hauteur de la zone semaine
                # Une méthode plus robuste est de regarder le texte "Lundi", "Mardi" à gauche
                # MAIS SIMPLIFICATION : On calcule le delta Y depuis la date
                delta_y = r_top - ref_week['top']
                
                # Estimation : chaque ligne jour fait environ X pixels de haut
                # Il faut scanner "Lundi, Mardi" pour être précis, mais essayons avec des tranches
                # Si les lignes sont régulières :
                day_index = int(delta_y / (r_height * 0.9)) # Approximation
                if day_index > 4: day_index = 4 # Vendredi max
                
                course_date = ref_week['date'] + timedelta(days=day_index)

                # 2. Calculer l'heure (Axe X)
                # Formule : Heure = 7.75 + (Distance_pixels / Pixels_par_heure)
                start_hour_decimal = 7.75 + (r_x0 - start_x) / px_per_h
                duration_hours = r_width / px_per_h
                
                # Conversion en heures/minutes
                start_h = int(start_hour_decimal)
                start_m = int((start_hour_decimal - start_h) * 60)
                
                # Arrondir aux 5 minutes les plus proches pour éviter 8h59
                start_m = round(start_m / 5) * 5
                if start_m == 60:
                    start_h += 1
                    start_m = 0

                start_dt = course_date.replace(hour=start_h, minute=start_m)
                end_dt = start_dt + timedelta(hours=duration_hours)

                # 3. Extraire le texte DANS le rectangle
                # On découpe la page sur la zone du rectangle
                cropped = page.crop((rect['x0'], rect['top'], rect['x0'] + rect['width'], rect['bottom']))
                text = cropped.extract_text()
                
                if text and len(text.strip()) > 1:
                    # Nettoyage du texte
                    lines = text.split('\n')
                    summary = lines[0] # Titre (ex: TCP/IP)
                    location = lines[-1] if len(lines) > 1 else "" # Salle (souvent en bas)
                    
                    # Création événement ICS
                    e = Event()
                    e.name = summary
                    e.begin = TZ.localize(start_dt)
                    e.end = TZ.localize(end_dt)
                    e.location = location
                    e.description = text # Tout le contenu dans la description
                    
                    calendar.events.add(e)

    return calendar

def main():
    try:
        pdf_file = download_pdf(PDF_URL)
        cal = parse_pdf(pdf_file)
        
        with open(OUTPUT_FILE, 'w') as f:
            f.writelines(cal.serialize())
            
        print(f"Succès ! {len(cal.events)} événements créés dans {OUTPUT_FILE}")
        
    except Exception as e:
        print(f"Erreur : {e}")

if __name__ == "__main__":
    main()