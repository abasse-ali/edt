import requests
import pdfplumber
import re
import urllib3
from datetime import datetime, timedelta
from ics import Calendar, Event
from pytz import timezone

# --- CONFIGURATION ---
PDF_URL = "https://stri.fr/Gestion_STRI/TAV/L3/EDT_STRI1A_L3IRT_TAV.pdf"
MY_GROUP = "GB"    # Ton groupe
IGNORE_GROUP = "GC" # Groupe à ignorer
TZ = timezone('Europe/Paris')
YEAR = 2026        # Année cible (2026 car 12 janv = Lundi en 2026)

# Désactiver les warnings SSL
urllib3.disable_warnings(urllib3.exceptions.InsecureRequestWarning)

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
    print(f"Téléchargement: {PDF_URL}")
    try:
        response = requests.get(PDF_URL, verify=False, timeout=30)
        with open("edt.pdf", "wb") as f:
            f.write(response.content)
    except Exception as e:
        print(f"Erreur DL: {e}")
        exit(1)

def parse_month(month_str):
    months = {"janv": 1, "févr": 2, "mars": 3, "avr": 4, "mai": 5, "juin": 6,
              "juil": 7, "août": 8, "sept": 9, "oct": 10, "nov": 11, "déc": 12}
    clean = month_str.lower().strip().replace('.', '')
    for k, v in months.items():
        if k in clean: return v
    return None

def is_exam(rect):
    # Jaune = (1, 1, 0) approx
    if not rect.get('non_stroking_color'): return False
    c = rect['non_stroking_color']
    return len(c) >= 3 and c[0] > 0.8 and c[1] > 0.8 and c[2] < 0.5

def extract_schedule():
    cal = Calendar()
    
    with pdfplumber.open("edt.pdf") as pdf:
        for page_num, page in enumerate(pdf.pages):
            print(f"--- Traitement Page {page_num + 1} ---")
            words = page.extract_words(x_tolerance=3, y_tolerance=3)
            rects = page.rects

            # 1. Calibrage X (Heures)
            hours_map = {}
            for w in words:
                txt = w['text'].strip()
                if re.match(r'^\d{1,2}h$', txt):
                    try:
                        h = int(txt.replace('h', ''))
                        hours_map[h] = w['x0']
                    except: pass
            
            if len(hours_map) < 2: continue
            
            min_h, max_h = min(hours_map.keys()), max(hours_map.keys())
            px_start, px_end = hours_map[min_h], hours_map[max_h]
            px_per_hour = (px_end - px_start) / (max_h - min_h) if max_h > min_h else 100

            def x_to_time(x):
                hours = min_h + (x - px_start) / px_per_hour
                total_min = int(hours * 60)
                # Arrondi 15 min
                rem = total_min % 15
                if rem < 8: total_min -= rem
                else: total_min += (15 - rem)
                return int(total_min // 60), int(total_min % 60)

            # 2. Découpage Y (Jours)
            days = []
            day_names = ["Lundi", "Mardi", "Mercredi", "Jeudi", "Vendredi"]
            week_date = None
            
            # Date du Lundi
            for w in words:
                if '/' in w['text'] and w['x0'] < 150:
                    parts = w['text'].split('/')
                    if len(parts) >= 2:
                        m = parse_month(parts[1])
                        if m:
                            d_str = re.sub(r'\D', '', parts[0])
                            if d_str:
                                week_date = datetime(YEAR, m, int(d_str))
                                break
            
            if not week_date: continue

            # Zones Y
            sorted_by_y = sorted([w for w in words if w['text'] in day_names and w['x0'] < 150], key=lambda w: w['top'])
            for i, w in enumerate(sorted_by_y):
                d_idx = day_names.index(w['text'])
                y_top = w['top']
                y_bottom = sorted_by_y[i+1]['top'] if i < len(sorted_by_y)-1 else page.height
                days.append({
                    'date': week_date + timedelta(days=d_idx),
                    'y_top': y_top,
                    'y_bottom': y_bottom
                })

            # 3. Extraction Cours (Cluster strict)
            content = [w for w in words if w['x0'] > px_start - 20]
            
            for day in days:
                # Mots du jour
                d_words = [w for w in content if day['y_top'] <= w['top'] + w['height']/2 < day['y_bottom']]
                d_words.sort(key=lambda w: w['x0']) # Tri gauche -> droite
                
                blocks = []
                if not d_words: continue

                # Algorithme de découpage par "GAP" (Vide)
                current_block = [d_words[0]]
                
                for w in d_words[1:]:
                    prev = current_block[-1]
                    # Si le mot est à plus de 60px (environ 45-50min) du précédent, on coupe
                    gap = w['x0'] - prev['x1']
                    # OU si le mot est sur une ligne très différente (superposition verticale)
                    v_gap = abs(w['top'] - prev['top'])
                    
                    if gap > 60 or v_gap > 20:
                        blocks.append(current_block)
                        current_block = [w]
                    else:
                        current_block.append(w)
                blocks.append(current_block)

                # Traitement des blocs
                final_events = []
                for b in blocks:
                    text = " ".join([w['text'] for w in b])
                    
                    # Filtres basiques
                    if re.match(r'^\d{1,2}h$', text): continue
                    if "Page" in text and len(text) < 10: continue

                    # Calcul Géométrie
                    b_x0 = min(w['x0'] for w in b)
                    b_x1 = max(w['x1'] for w in b)
                    b_y = sum(w['top'] for w in b) / len(b) # Y moyen

                    # Filtre Groupe TEXTUEL
                    if IGNORE_GROUP in text and MY_GROUP not in text: continue

                    # Nettoyage Profs
                    clean = text
                    for k, v in PROFS.items():
                        clean = clean.replace(f"({k})", f"({v})")

                    # Heures
                    h_start, m_start = x_to_time(b_x0)
                    h_end, m_end = x_to_time(b_x1)
                    
                    if h_start < 7: h_start = 7
                    if h_end > 20: h_end = 20
                    
                    start = day['date'].replace(hour=h_start, minute=m_start, tzinfo=TZ)
                    end = day['date'].replace(hour=h_end, minute=m_end, tzinfo=TZ)
                    
                    if (end - start).total_seconds() < 1800: continue # Trop court

                    # Détection Exam
                    exam = False
                    mx, my = (b_x0+b_x1)/2, (min(w['top'] for w in b) + max(w['bottom'] for w in b))/2
                    for r in rects:
                        if is_exam(r) and r['x0'] < mx < r['x1'] and r['top'] < my < r['bottom']:
                            exam = True
                    
                    # Salle
                    loc = ""
                    s_match = re.search(r'(U\d[-\w]+|Amphi)', text)
                    if s_match: loc = s_match.group(0)

                    final_events.append({
                        'name': f"{'EXAM: ' if exam else ''}{clean}",
                        'start': start, 'end': end,
                        'loc': loc,
                        'y': b_y,  # Pour gérer les superpositions
                        'text': text
                    })

                # GESTION DES SUPERPOSITIONS (Même heure, deux lignes)
                # On regroupe les events qui se chevauchent temporellement
                filtered_events = []
                while final_events:
                    curr = final_events.pop(0)
                    overlaps = [curr]
                    # Trouver ceux qui chevauchent curr
                    others = []
                    for other in final_events:
                        # Chevauchement si (StartA < EndB) et (EndA > StartB)
                        if curr['start'] < other['end'] and curr['end'] > other['start']:
                            overlaps.append(other)
                        else:
                            others.append(other)
                    final_events = others # On continue avec ceux qui restent

                    if len(overlaps) == 1:
                        filtered_events.append(overlaps[0])
                    else:
                        # CONFLIT : On a plusieurs cours en même temps (Lignes haut/bas)
                        # Stratégie : 
                        # 1. Si l'un contient explicitement "GB", on le garde.
                        # 2. Sinon, on garde celui qui est le plus BAS (Y plus grand) car "première ligne ne me concerne pas"
                        
                        keep = None
                        # Check GB explicite
                        for cand in overlaps:
                            if MY_GROUP in cand['text']:
                                keep = cand
                                break
                        
                        # Sinon check position (Le plus grand Y = Le plus bas sur la page)
                        if not keep:
                            keep = max(overlaps, key=lambda x: x['y'])
                        
                        filtered_events.append(keep)

                # Ajout au calendrier
                for ev in filtered_events:
                    e = Event()
                    e.name = ev['name']
                    e.begin = ev['start']
                    e.end = ev['end']
                    e.location = ev['loc']
                    cal.events.add(e)

    with open("edt.ics", "w") as f:
        f.write(cal.serialize())

if __name__ == "__main__":
    download_pdf()
    extract_schedule()
