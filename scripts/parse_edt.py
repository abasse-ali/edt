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
    try:
        response = requests.get(url, timeout=10)
        response.raise_for_status()
        with open(filename, 'wb') as f:
            f.write(response.content)
        return filename
    except Exception as e:
        print(f"Erreur téléchargement: {e}")
        return None

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
        start_grid_x = x_8h - (pixels_per_hour * 0.25) # 7h45
    else:
        # Valeurs par défaut (fallback)
        pixels_per_hour = 100 
        start_grid_x = 50

    # 2. Calibration verticale (Dates)
    week_anchors = []
    current_year = datetime.now().year 
    
    date_pattern = re.compile(r"(\d{1,2})/([a-zéû]+)")
    
    for w in words:
        match = date_pattern.match(w['text'])
        if match:
            day_num = int(match.group(1))
            month_str = match.group(2)
            month_num = MOIS.get(month_str, 1)
            
            # Gestion simple de l'année
            dt = datetime(current_year, month_num, day_num)
            
            week_anchors.append({
                'date': dt,
                'top': w['top'],
                'bottom': w['bottom']
            })
            
    return start_grid_x, pixels_per_hour, week_anchors

def clean_text(text):
    """Sépare le Titre du cours et la Salle"""
    lines = [line.strip() for line in text.split('\n') if line.strip()]
    
    if not lines:
        return "Cours", ""

    # Mots clés étendus pour détecter les salles
    room_keywords = ["U3-", "Amphi", "U6-", "K01", "Labo", "Salle", "U4-", "J3-", "L3-"]
    
    title_parts = []
    location_parts = []
    
    for line in lines:
        # Si la ligne contient un mot clé de salle -> Location
        if any(k in line for k in room_keywords):
            location_parts.append(line)
        else:
            # Sinon -> Titre
            title_parts.append(line)
    
    summary = " ".join(title_parts)
    location = " ".join(location_parts)
    
    # Sécurité : Si aucun titre trouvé (tout a été détecté comme salle ?)
    if not summary and location:
        summary = location # On remet la salle en titre faute de mieux
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
                if rect['height'] < 10 or rect['width'] < 10: continue

                r_top = rect['top']
                
                # Identifier la semaine
                ref_week = None
                for i, w in enumerate(weeks):
                    # Zone de tolérance pour la semaine
                    next_w_top = weeks[i+1]['top'] if i+1 < len(weeks) else 9999
                    if w['top'] - 50 <= r_top < next_w_top - 50:
                        ref_week = w
                        break
                
                if not ref_week: continue

                # Identifier le jour
                delta_y = r_top - ref_week['top']
                # Estimation : ~35px par jour (à affiner si besoin)
                day_index = int(delta_y / 35) 
                if day_index > 4: day_index = 4
                if day_index < 0: day_index = 0
                
                course_date = ref_week['date'] + timedelta(days=day_index)

                # Calcul Heure
                start_hour_decimal = 7.75 + (rect['x0'] - start_x) / px_per_h
                duration_hours = rect['width'] / px_per_h
                
                start_h = int(start_hour_decimal)
                start_m = int((start_hour_decimal - start_h) * 60)
                # Arrondi 5 min
                start_m = round(start_m / 5) * 5
                if start_m == 60:
                    start_h += 1; start_m = 0

                start_dt = course_date.replace(hour=start_h, minute=start_m)
                end_dt = start_dt + timedelta(hours=duration_hours)

                # --- CORRECTION MAJEURE ICI : MARGES ---
                # On élargit la zone de crop de 2 pixels de chaque côté
                # pour être sûr d'attraper le texte qui dépasse
                x0 = max(0, rect['x0'] - 2)
                top = max(0, rect['top'] - 2)
                x1 = min(page.width, rect['x0'] + rect['width'] + 2)
                bottom = min(page.height, rect['bottom'] + 2)
                
                cropped = page.crop((x0, top, x1, bottom))
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
    pdf_file = download_pdf(PDF_URL)
    if pdf_file:
        cal = parse_pdf(pdf_file)
        with open(OUTPUT_FILE, 'w') as f:
            f.writelines(cal.serialize())
        print(f"Succès ! {len(cal.events)} événements générés.")

if __name__ == "__main__":
    main()
