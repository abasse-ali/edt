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
            print(f"--- Traitement Page {page_num + 1} ---")
            
            # Extraction brute
            raw_words = page.extract_words(x_tolerance=3, y_tolerance=3)
            
            # --- ETAPE 1 : Calibrage Temporel (X-Axis) ---
            hours_anchors = {}
            for w in raw_words:
                txt = w['text'].strip()
                # On cherche les "8h", "9h" qui sont tout en haut de la page
                if w['top'] < 150 and re.match(r'^(7|8|9|10|11|12|13|14|15|16|17|18|19|20)h$', txt):
                    try:
                        h = int(txt.replace('h', ''))
                        hours_anchors[h] = w['x0']
                    except: pass
            
            if len(hours_anchors) < 2:
                print("Pas de grille horaire fiable. Page ignorée.")
                continue

            min_h = min(hours_anchors.keys())
            max_h = max(hours_anchors.keys())
            px_start = hours_anchors[min_h]
            # Calcul précis de la largeur d'une heure
            # On fait une moyenne si possible pour éviter les distorsions
            dist_total = hours_anchors[max_h] - hours_anchors[min_h]
            px_per_hour = dist_total / (max_h - min_h)

            def x_to_time(x):
                # Projection linéaire
                offset = (x - px_start) / px_per_hour
                time_float = min_h + offset
                total = int(time_float * 60)
                # Arrondi 15 min
                rem = total % 15
                if rem < 8: total -= rem
                else: total += (15 - rem)
                return int(total // 60), int(total % 60)

            # --- ETAPE 2 : Détection des Jours (Y-Axis) ---
            days = []
            day_names = ["Lundi", "Mardi", "Mercredi", "Jeudi", "Vendredi"]
            week_date = None
            
            # Recherche de la date (ex: 12/janv)
            for w in raw_words:
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

            # Découpage vertical
            headers = sorted([w for w in raw_words if w['text'] in day_names and w['x0'] < 150], key=lambda w: w['top'])
            for i, w in enumerate(headers):
                idx = day_names.index(w['text'])
                y_top = w['top']
                y_bottom = headers[i+1]['top'] if i < len(headers)-1 else page.height
                days.append({
                    'date': week_date + timedelta(days=idx),
                    'y_top': y_top, 'y_bottom': y_bottom
                })

            # --- ETAPE 3 : Filtrage et Nettoyage des Mots ---
            content_words = []
            for w in raw_words:
                # On vire tout ce qui est avant 8h (marge gauche)
                if w['x0'] < px_start - 10: continue
                
                txt = w['text'].strip()
                # On supprime TOUTES les mentions d'heures (8h, 10h...) qui polluent
                if re.match(r'^\d{1,2}h$', txt): continue
                # On supprime les numéros de page
                if txt == "Page" or (re.match(r'^\d+$', txt) and w['top'] > page.height - 50): continue
                
                content_words.append(w)

            # --- ETAPE 4 : Clustering (Construction des blocs) ---
            events_candidates = []
            
            for day in days:
                # Mots de la journée
                d_words = [w for w in content_words if day['y_top'] <= w['top'] + w['height']/2 < day['y_bottom']]
                d_words.sort(key=lambda w: w['x0'])
                
                if not d_words: continue

                # Algorithme de "Boîtes"
                # Si deux mots sont proches, ils vont dans la même boîte
                blocks = []
                if d_words:
                    curr = [d_words[0]]
                    for w in d_words[1:]:
                        prev = curr[-1]
                        # Critères de séparation :
                        # 1. Ecart horizontal > 40px (un trou de ~30min)
                        # 2. Ecart vertical > 30px (changement de ligne net)
                        # MAIS on est tolérant si le mot est juste en dessous (alignement vertical)
                        dx = w['x0'] - prev['x1']
                        dy = abs(w['top'] - prev['top'])
                        
                        if dx > 40 or dy > 30:
                            blocks.append(curr)
                            curr = [w]
                        else:
                            curr.append(w)
                    blocks.append(curr)

                # Transformation Blocs -> Events Candidats
                for b in blocks:
                    # Construction du texte
                    raw_txt = " ".join([w['text'] for w in b]).strip()
                    
                    # Filtres anti-bruit
                    if len(raw_txt) < 3: continue
                    # Si c'est juste un nom de salle (ex: "U3-Amphi" tout seul), c'est du bruit
                    if re.match(r'^(U\d[-\w/]+|Amphi)$', raw_txt, re.IGNORECASE): continue
                    
                    # Filtre Groupe GC
                    if IGNORE_GROUP in raw_txt and MY_GROUP not in raw_txt: continue
                    
                    # Remplacement Profs
                    final_txt = raw_txt
                    for k, v in PROFS.items():
                        final_txt = final_txt.replace(f"({k})", f"({v})")
                        
                    # Calcul Temps
                    bx0 = min(w['x0'] for w in b)
                    bx1 = max(w['x1'] for w in b)
                    hs, ms = x_to_time(bx0)
                    he, me = x_to_time(bx1)
                    
                    # Bornage
                    if hs < 7: hs = 7
                    if he > 21: he = 21
                    
                    start_dt = day['date'].replace(hour=hs, minute=ms, tzinfo=TZ)
                    end_dt = day['date'].replace(hour=he, minute=me, tzinfo=TZ)
                    
                    # Durée minimale 30 min
                    if (end_dt - start_dt).total_seconds() < 1800: continue
                    
                    # Salle
                    loc = ""
                    lm = re.search(r'(U\d[-\w/]+|Amphi)', final_txt)
                    if lm: loc = lm.group(0)

                    # Exam check
                    is_ex = False
                    mx, my = (bx0+bx1)/2, (min(w['top'] for w in b) + max(w['bottom'] for w in b))/2
                    for r in page.rects:
                        if is_exam(r) and r['x0'] < mx < r['x1'] and r['top'] < my < r['bottom']:
                            is_ex = True
                    
                    events_candidates.append({
                        'name': f"{'EXAM: ' if is_ex else ''}{final_txt}",
                        'start': start_dt,
                        'end': end_dt,
                        'loc': loc,
                        'raw': raw_txt,
                        'y_center': my
                    })

            # --- ETAPE 5 : NETTOYAGE ET DEDUPLICATION (Le Grand Ménage) ---
            # On trie par heure de début
            events_candidates.sort(key=lambda x: x['start'])
            
            final_events = []
            while events_candidates:
                current = events_candidates.pop(0)
                
                # On cherche les conflits (Events qui se chevauchent sur >15min)
                overlaps = [current]
                others = []
                for other in events_candidates:
                    # Formule d'intersection de plages horaires
                    latest_start = max(current['start'], other['start'])
                    earliest_end = min(current['end'], other['end'])
                    overlap_duration = (earliest_end - latest_start).total_seconds()
                    
                    if overlap_duration > 900: # Plus de 15 min en commun -> Conflit
                        overlaps.append(other)
                    else:
                        others.append(other)
                
                events_candidates = others # On continue avec ceux qui restent
                
                # RESOLUTION DU CONFLIT
                if len(overlaps) == 1:
                    final_events.append(overlaps[0])
                else:
                    # On a plusieurs blocs pour le même créneau. Lequel garder ?
                    
                    # Stratégie 1 : Si un contient "GB", c'est le vainqueur absolu
                    winner = next((x for x in overlaps if MY_GROUP in x['raw']), None)
                    
                    # Stratégie 2 : Si pas de GB, on prend le plus complet (texte le plus long)
                    # Cela élimine souvent les "U3-Amphi" isolés qui se superposent au vrai cours
                    if not winner:
                        winner = max(overlaps, key=lambda x: len(x['raw']))
                        
                    # Stratégie 3 (Bonus) : Si égalité, on prend celui du bas (souvent le bon groupe)
                    # (Mais la longueur du texte règle souvent le pb avant)
                    
                    final_events.append(winner)

            # Ajout au calendrier
            for ev in final_events:
                e = Event()
                e.name = ev['name']
                e.begin = ev['start']
                e.end = ev['end']
                e.location = ev['loc']
                cal.events.add(e)

    with open("edt.ics", "w") as f:
        f.write(cal.serialize())
    print("Calendrier généré et nettoyé.")

if __name__ == "__main__":
    download_pdf()
    extract_schedule()
