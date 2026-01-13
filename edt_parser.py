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
    # Si on est en fin d'année (Sept-Dec), Janvier est l'année prochaine
    if now.month >= 9:
        return now.year + 1 if month_target < 9 else now.year
    # Si on est en début d'année (Jan-Aout), Octobre était l'année d'avant
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
            
            # 1. RECUPERATION ET NETTOYAGE DES MOTS
            raw_words_all = page.extract_words(x_tolerance=3, y_tolerance=3)
            
            # Repérage des ancres horaires (8h, 9h...)
            hours_anchors = {}
            cleaned_words = [] # Mots sans les "8h", "10h"
            
            for w in raw_words_all:
                txt = w['text'].strip()
                # Est-ce une ancre horaire ?
                if re.match(r'^(8|9|10|11|12|13|14|15|16|17|18|19)h$', txt):
                    try:
                        h = int(txt.replace('h', ''))
                        hours_anchors[h] = w['x0']
                    except: pass
                    continue # On ne l'ajoute pas au contenu !
                
                # Nettoyage préventif
                if re.match(r'^\d{1,2}h$', txt): continue # "16h" isolé
                if txt == "Page" or re.match(r'^\d+$', txt): continue # Pagination
                
                cleaned_words.append(w)

            # Calibration Temporelle
            if not hours_anchors:
                print("Pas d'horaires trouvés. Page ignorée.")
                continue
                
            min_h = min(hours_anchors.keys())
            max_h = max(hours_anchors.keys())
            if max_h == min_h: max_h = min_h + 2
            
            px_start = hours_anchors[min_h]
            px_end = hours_anchors[max_h]
            px_per_hour = (px_end - px_start) / (max_h - min_h)

            def x_to_time(x):
                offset = (x - px_start) / px_per_hour
                time_float = min_h + offset
                total = int(time_float * 60)
                # Arrondi
                rem = total % 15
                if rem < 8: total -= rem
                else: total += (15 - rem)
                return int(total // 60), int(total % 60)

            # 2. DETECTION DATE ET ZONES
            week_date = None
            for w in raw_words_all:
                if '/' in w['text'] and w['x0'] < 150:
                    parts = w['text'].split('/')
                    if len(parts) >= 2:
                        m = parse_month(parts[1])
                        if m:
                            d_str = re.sub(r'\D', '', parts[0])
                            if d_str:
                                y = get_academic_year(m)
                                week_date = datetime(y, m, int(d_str))
                                break
            
            if not week_date: continue

            day_names = ["Lundi", "Mardi", "Mercredi", "Jeudi", "Vendredi"]
            headers = sorted([w for w in raw_words_all if w['text'] in day_names and w['x0'] < 150], key=lambda w: w['top'])
            
            days = []
            for i, w in enumerate(headers):
                d_idx = day_names.index(w['text'])
                y_top = w['top']
                y_bottom = headers[i+1]['top'] if i < len(headers)-1 else page.height
                days.append({
                    'date': week_date + timedelta(days=d_idx),
                    'y_top': y_top, 'y_bottom': y_bottom
                })

            # 3. CREATION DES CANDIDATS (Blocs bruts)
            candidates = []
            content = [w for w in cleaned_words if w['x0'] > px_start - 20] # A droite de 8h
            
            for day in days:
                d_words = [w for w in content if day['y_top'] <= w['top'] + w['height']/2 < day['y_bottom']]
                d_words.sort(key=lambda w: w['x0'])
                
                if not d_words: continue
                
                # Clustering
                blocks = []
                curr = [d_words[0]]
                for w in d_words[1:]:
                    prev = curr[-1]
                    # Si écart > 50px (trou temps) OU > 30px (trou vertical ligne)
                    if (w['x0'] - prev['x1'] > 50) or (abs(w['top'] - prev['top']) > 30):
                        blocks.append(curr)
                        curr = [w]
                    else:
                        curr.append(w)
                blocks.append(curr)

                for b in blocks:
                    raw_txt = " ".join([w['text'] for w in b])
                    clean_txt = raw_txt.strip()
                    
                    # FILTRE ORPHELINS (Salles seules)
                    # Si le texte est juste "U3-Amphi" ou "U3-203/204", on jette
                    if re.match(r'^(U\d[-\w/]+|Amphi|Salle \w+)$', clean_txt, re.IGNORECASE):
                        continue
                    
                    if len(clean_txt) < 3: continue

                    # Filtre Groupe Texte
                    if IGNORE_GROUP in clean_txt and MY_GROUP not in clean_txt: continue

                    # Remplacement Profs
                    final_txt = clean_txt
                    for k, v in PROFS.items():
                        final_txt = final_txt.replace(f"({k})", f"({v})")

                    # Temps
                    b_x0 = min(w['x0'] for w in b)
                    b_x1 = max(w['x1'] for w in b)
                    h_s, m_s = x_to_time(b_x0)
                    h_e, m_e = x_to_time(b_x1)
                    
                    if h_s < 7: h_s = 7
                    if h_e > 21: h_e = 21
                    
                    start = day['date'].replace(hour=h_s, minute=m_s, tzinfo=TZ)
                    end = day['date'].replace(hour=h_e, minute=m_e, tzinfo=TZ)
                    
                    if (end - start).total_seconds() < 1800: continue # < 30 min

                    # Salle
                    loc = ""
                    lm = re.search(r'(U\d[-\w/]+|Amphi)', final_txt)
                    if lm: loc = lm.group(0)

                    # Exam
                    is_ex = False
                    mx, my = (b_x0+b_x1)/2, (min(w['top'] for w in b)+max(w['bottom'] for w in b))/2
                    for r in page.rects:
                        if is_exam(r) and r['x0']<mx<r['x1'] and r['top']<my<r['bottom']:
                            is_ex = True

                    candidates.append({
                        'name': f"{'EXAM: ' if is_ex else ''}{final_txt}",
                        'start': start,
                        'end': end,
                        'loc': loc,
                        'y': sum(w['top'] for w in b)/len(b), # Moyenne Y
                        'raw': clean_txt
                    })

            # 4. RESOLUTION DES CONFLITS (Le Highlander)
            # On trie par heure de début
            candidates.sort(key=lambda x: x['start'])
            
            final_events = []
            while candidates:
                curr = candidates.pop(0)
                
                # On cherche tous les événements qui se chevauchent avec 'curr'
                # Chevauchement significatif (> 50% de la durée)
                overlaps = [curr]
                others = []
                
                for o in candidates:
                    # Intersection
                    latest_start = max(curr['start'], o['start'])
                    earliest_end = min(curr['end'], o['end'])
                    delta = (earliest_end - latest_start).total_seconds()
                    
                    if delta > 900: # Plus de 15 min de chevauchement
                        overlaps.append(o)
                    else:
                        others.append(o)
                
                candidates = others # On continue avec le reste
                
                # S'il y a conflit, on doit en choisir UN SEUL
                if len(overlaps) == 1:
                    final_events.append(overlaps[0])
                else:
                    # Critères de choix :
                    # 1. Celui qui contient "GB"
                    winner = next((x for x in overlaps if MY_GROUP in x['raw']), None)
                    
                    # 2. Sinon, celui qui est le plus BAS (Y le plus grand)
                    if not winner:
                         winner = max(overlaps, key=lambda x: x['y'])
                    
                    final_events.append(winner)

            # Ajout final
            for ev in final_events:
                e = Event()
                e.name = ev['name']
                e.begin = ev['start']
                e.end = ev['end']
                e.location = ev['loc']
                cal.events.add(e)

    with open("edt.ics", "w") as f:
        f.write(cal.serialize())
    print("Génération terminée.")

if __name__ == "__main__":
    download_pdf()
    extract_schedule()
