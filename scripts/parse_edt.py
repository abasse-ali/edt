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
    words = page.extract_words()
    
    # 1. Calibration horizontale (Temps)
    x_8h = None
    x_9h = None
    for w in words:
        if w['text'] == '8h': x_8h = w['x0']
        if w['text'] == '9h': x_9h = w['x0']
    
    if x_8h and x_9h:
        pixels_per_hour = x_9h - x_8h
        start_grid_x = x_8h - (pixels_per_hour * 0.25) # Commence à 7h45
    else:
        print("WARN: Calibration auto échouée, valeurs par défaut.")
        pixels_per_hour = 100 
        start_grid_x = 50

    # 2. Calibration verticale (Dates)
    week_anchors = []
    current_year = datetime.now().year 
    
    # Regex pour trouver "12/janv"
    date_pattern = re.compile(r"(\d{1,2})/([a-zéû]+)")
    
    for w in words:
        match = date_pattern.match(w['text'])
        if match:
            day_num = int(match.group(1))
            month_str = match.group(2)
            month_num = MOIS.get(month_str, 1)
            
            # Gestion année scolaire (si on est en janvier 2025, le pdf est bon)
            # Si le mois détecté est Septembre/Octobre, c'est l'année N-1 si on est en Janvier
            # Pour l'instant on garde simple : année courante système
            dt = datetime(current_year, month_num, day_num)
            
            week_anchors.append({
                'date': dt,
                'top': w['top'],
                'bottom': w['bottom']
            })
            
    return start_grid_x, pixels_per_hour, week_anchors

def clean_text(text):
    """Sépare le Titre du cours et la Salle intelligemment"""
    lines = [line.strip() for line in text.split('\n') if line.strip()]
    
    if not lines:
        return "Cours Inconnu", ""

    # Mots clés qui indiquent une salle
    room_keywords = ["U3-", "Amphi", "U6-", "K01", "Labo", "Salle", "U4-"]
    
    title_parts = []
    location_parts = []
    
    for line in lines:
        # Si la ligne ressemble à une salle, on la met dans location
        if any(k in line for k in room_keywords):
            location_parts.append(line)
        else:
            # Sinon c'est le titre ou le prof
            title_parts.append(line)
    
    summary = " ".join(title_parts)
    location = " ".join(location_parts)
    
    # Fallback : Si on n'a rien trouvé en titre, on prend tout
    if not summary:
        summary = location
        location = ""
        
    return summary, location

def parse_pdf(pdf_path):
    calendar = Calendar()
    
    with pdfplumber.open(pdf_path) as pdf:
        for page in pdf.pages:
            start_x, px_per_h, weeks = get_anchors(page)
            weeks.sort(key=lambda x: x['top'])
            rects = page.rects
            
            for rect in rects:
                if not rect.get('non_stroking_color'): continue
                
                # Ignorer les trop petits
                if rect['height'] < 10 or rect['width'] < 10: continue

                r_top = rect['top']
                
                # Identifier la semaine
                ref_week = None
                for i, w in enumerate(weeks):
                    next_w_top = weeks[i+1]['top'] if i+1 < len(weeks) else 9999
                    if w['top'] - 20 <= r_top < next_w_top - 20:
                        ref_week = w
                        break
                
                if not ref_week: continue

                # Identifier le jour (Lundi=0, Mardi=1...)
                delta_y = r_top - ref_week['top']
                # Ajustement hauteur approximative d'une ligne jour (environ 30-40px selon PDF)
                # On divise la hauteur de semaine par 5 jours
                week_height = (weeks[1]['top'] - weeks[0]['top']) if len(weeks) > 1 else 200
                day_height = week_height / 5 # Approximation grossière mais souvent suffisante
                
                # Alternative plus simple basée sur l'image :
                # Lundi est tout en haut. Chaque jour fait environ 35px de haut.
                day_index = int(delta_y / 35) 
                if day_index > 4: day_index = 4
                
                course_date = ref_week['date'] + timedelta(days=day_index)

                # Calcul Heure
                start_hour_decimal = 7.75 + (rect['x0'] - start_x) / px_per_h
                duration_hours = rect['width'] / px_per_h
                
                start_h = int(start_hour_decimal)
                start_m = int((start_hour_decimal - start_h) * 60)
                start_m = round(start_m / 5) * 5
                if start_m == 60:
                    start_h += 1; start_m = 0

                start_dt = course_date.replace(hour=start_h, minute=start_m)
                end_dt = start_dt + timedelta(hours=duration_hours)

                # Extraction Texte
                cropped = page.crop((rect['x0'], rect['top'], rect['x0'] + rect['width'], rect['bottom']))
                raw_text = cropped.extract_text()
                
                if raw_text and len(raw_text.strip()) > 1:
                    summary, location = clean_text(raw_text)
                    
                    e = Event()
                    e.name = summary
                    e.begin = TZ.localize(start_dt)
                    e.end = TZ.localize(end_dt)
                    e.location = location
                    
                    calendar.events.add(e)

    return calendar

def main():
    try:
        pdf_file = download_pdf(PDF_URL)
        cal = parse_pdf(pdf_file)
        with open(OUTPUT_FILE, 'w') as f:
            f.writelines(cal.serialize())
        print(f"Succès ! {len(cal.events)} événements créés.")
    except Exception as e:
        print(f"Erreur : {e}")

if __name__ == "__main__":
    main()
