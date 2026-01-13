import requests
import pdfplumber
import re
import urllib3
from datetime import datetime, timedelta
from ics import Calendar, Event
from pytz import timezone

# --- CONFIGURATION ---
PDF_URL = "https://stri.fr/Gestion_STRI/TAV/L3/EDT_STRI1A_L3IRT_TAV.pdf"
MY_GROUP = "GB"
IGNORE_GROUP = "GC"
TZ = timezone('Europe/Paris')

# Désactiver les alertes SSL
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

def get_academic_year(month_target):
    now = datetime.now()
    if now.month >= 9:
        return now.year + 1 if month_target < 9 else now.year
    else:
        return now.year - 1 if month_target >= 9 else now.year

def is_exam(rect):
    if not rect.get('non_stroking_color'): return False
    c = rect['non_stroking_color']
    return len(c) >= 3 and c[0] > 0.8 and c[1] > 0.8 and c[2] < 0.5

def extract_schedule():
    cal = Calendar()
    
    with pdfplumber.open("edt.pdf") as pdf:
        for page_num, page in enumerate(pdf.pages):
            print(f"--- Page {page_num + 1} ---")
            
            raw_words = page.extract_words(x_tolerance=3, y_tolerance=3)
            
            # 1. ANCRAGE TEMPOREL PRÉCIS
            # On cherche "8h" et "18h" (ou max) pour définir l'échelle
            hours_anchors = {}
            for w in raw_words:
                txt = w['text'].strip()
                if w['top'] > 100: continue # Ignorer les heures dans le corps du texte
                
                # Regex stricte pour trouver les entêtes horaires
                if re.match(r'^(8|9|10|11|12|13|14|15|16|17|18|19)h$', txt):
                    try:
                        h = int(txt.replace('h', ''))
                        hours_anchors[h] = w['x0']
                    except: pass

            if 8 not in hours_anchors:
                # Si on ne trouve pas 8h, on essaie de déduire avec 9h
                if 9 in hours_anchors:
                    hours_anchors[8] = hours_anchors[9] - (hours_anchors.get(10, 0) - hours_anchors[9])
                else:
                    print("Impossible de calibrer l'heure (pas de 8h/9h). Page ignorée.")
                    continue
            
            # Calcul du ratio Pixels / Heure
            # On prend les deux ancres les plus éloignées
            min_h, max_h = min(hours_anchors.keys()), max(hours_anchors.keys())
            if max_h == min_h: max_h = min_h + 1 # Sécurité
            
            px_start = hours_anchors[min_h]
            px_end = hours_anchors[max_h]
            px_per_hour = (px_end - px_start) / (max_h - min_h)

            def x_to_time(x):
                # On calcule par rapport à l'ancre 8h (ou min_h)
                offset_pixels = x - hours_anchors[min_h]
                offset_hours = offset_pixels / px_per_hour
                actual_time = min_h + offset_hours
                
                total_min = int(actual_time * 60)
                # Arrondi 15 min
                rem = total_min % 15
                if rem < 8: total_min -= rem
                else: total_min += (15 - rem)
                return int(total_min // 60), int(total_min % 60)

            # 2. DÉTECTION DES JOURS
            days = []
            day_names = ["Lundi", "Mardi", "Mercredi", "Jeudi", "Vendredi"]
            week_date = None
            
            for w in raw_words:
                if '/' in w['text'] and w['x0'] < 150:
                    parts = w['text'].split('/')
                    if len(parts) >= 2:
                        m = parse_month(parts[1])
                        if m:
                            d_str = re.sub(r'\D', '', parts[0])
                            if d_str:
                                year = get_academic_year(m)
                                week_date = datetime(year, m, int(d_str))
                                break
            
            if not week_date: continue

            headers = sorted([w for w in raw_words if w['text'] in day_names and w['x0'] < 150], key=lambda w: w['top'])
            for i, w in enumerate(headers):
                d_idx = day_names.index(w['text'])
                y_top = w['top']
                y_bottom = headers[i+1]['top'] if i < len(headers)-1 else page.height
                days.append({
                    'date': week_date + timedelta(days=d_idx),
                    'y_top': y_top, 'y_bottom': y_bottom, 'height': y_bottom - y_top
                })

            # 3. CONTENU (Filtrage des heures textes)
            content_words = []
            for w in raw_words:
                # On exclut les entêtes et les textes type "8h" qui polluent
                if w['x0'] < px_start - 20: continue 
                if re.match(r'^\d{1,2}h$', w['text']): continue
                content_words.append(w)

            for day in days:
                d_words = [w for w in content_words if day['y_top'] <= w['top'] + w['height']/2 < day['y_bottom']]
                d_words.sort(key=lambda w: w['x0'])
                
                blocks = []
                if not d_words: continue
                
                curr = [d_words[0]]
                for w in d_words[1:]:
                    prev = curr[-1]
                    # On coupe si écart horizontal OU écart vertical significatif
                    if (w['x0'] - prev['x1'] > 40) or (abs(w['top'] - prev['top']) > 30):
                        blocks.append(curr)
                        curr = [w]
                    else:
                        curr.append(w)
                blocks.append(curr)

                for b in blocks:
                    raw_txt = " ".join([w['text'] for w in b])
                    clean_txt = re.sub(r'\b\d{1,2}h\b', '', raw_txt).strip()
                    
                    if len(clean_txt) < 3: continue
                    if re.match(r'^(U\d[-\w]+|Amphi)$', clean_txt): continue

                    # GEOMETRIE DU BLOC
                    b_top = min(w['top'] for w in b)
                    b_bottom = max(w['bottom'] for w in b)
                    b_height = b_bottom - b_top
                    b_center_y = (b_top + b_bottom) / 2
                    
                    # --- FILTRAGE CRITIQUE "LIGNE DU HAUT" ---
                    # 1. Position relative dans la case jour (0.0 = haut, 1.0 = bas)
                    day_center = (day['y_top'] + day['y_bottom']) / 2
                    is_top_half = b_center_y < day_center
                    
                    # 2. Est-ce un "demi-cours" (hauteur faible) ?
                    # Un cours commun fait toute la hauteur (~90-100% disons > 70%)
                    # Un cours divisé fait ~50% ou moins
                    is_split_row = b_height < (day['height'] * 0.7)

                    # LOGIQUE DE REJET :
                    # Si c'est un cours "demi-hauteur" ET qu'il est dans la moitié haute -> C'est GC.
                    if is_split_row and is_top_half:
                        # Exception : Si ça contient explicitement "GB", on garde
                        if MY_GROUP in clean_txt:
                            pass
                        elif "Commun" in clean_txt: # Parfois écrit
                            pass
                        else:
                            print(f"   [REJETÉ] Ligne Haute (GC): {clean_txt}")
                            continue

                    # Filtre Texte Classique
                    if IGNORE_GROUP in clean_txt and MY_GROUP not in clean_txt:
                        print(f"   [REJETÉ] Groupe {IGNORE_GROUP}: {clean_txt}")
                        continue
                    
                    # Remplacement Profs
                    final_txt = clean_txt
                    for k, v in PROFS.items():
                        final_txt = final_txt.replace(f"({k})", f"({v})")

                    # Temps
                    b_x0, b_x1 = min(w['x0'] for w in b), max(w['x1'] for w in b)
                    h_s, m_s = x_to_time(b_x0)
                    h_e, m_e = x_to_time(b_x1)
                    
                    if h_s < 7: h_s = 7
                    if h_e > 21: h_e = 21
                    
                    start = day['date'].replace(hour=h_s, minute=m_s, tzinfo=TZ)
                    end = day['date'].replace(hour=h_e, minute=m_e, tzinfo=TZ)
                    
                    if (end - start).total_seconds() < 1800: continue

                    # Salle
                    loc = ""
                    lm = re.search(r'(U\d[-\w/]+|Amphi)', clean_txt)
                    if lm: loc = lm.group(0)

                    # Exam
                    is_ex = False
                    mx, my = (b_x0+b_x1)/2, b_center_y
                    for r in page.rects:
                        if is_exam(r) and r['x0']<mx<r['x1'] and r['top']<my<r['bottom']:
                            is_ex = True

                    e = Event()
                    e.name = f"{'EXAM: ' if is_ex else ''}{final_txt}"
                    e.begin = start
                    e.end = end
                    e.location = loc
                    cal.events.add(e)

    with open("edt.ics", "w") as f:
        f.write(cal.serialize())
    print("Génération terminée.")

if __name__ == "__main__":
    download_pdf()
    extract_schedule()
