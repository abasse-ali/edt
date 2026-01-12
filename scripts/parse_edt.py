import requests
import pdfplumber
import re
from ics import Calendar, Event
from datetime import datetime, timedelta
import pytz

# Configuration
PDF_URL = "https://stri.fr/Gestion_STRI/TAV/L3/EDT_STRI1A_L3IRT_TAV.pdf"
ICAL_PATH = "public/edt_stri.ics" # Dossier pour GitHub Pages
TIMEZONE = pytz.timezone("Europe/Paris")

# [span_1](start_span)Liste des profs[span_1](end_span)
PROFS = {
    "AA": "André AOUN", "AnAn": "Andréi ANDRÉI", "JGT": "Jean-Guy TARTARIN",
    "MM": "MUSTAPHA MOJAHID", "OM": "Olfa MECHI", "PT": "Patrice TORGUET",
    "RK": "Rahim KACIMI", "BA": "B. Amel (Supposé)" # Ajoute les autres ici
}

def download_pdf():
    response = requests.get(PDF_URL)
    with open("edt.pdf", "wb") as f:
        f.write(response.content)

def parse_time(date_str, hour_idx, duration_blocks):
    # [span_2](start_span)La grille commence à 7h45[span_2](end_span)
    # Chaque "carreau" (ligne de grille) = 15 min
    base_time = datetime.strptime(date_str, "%d/%m/%Y")
    start_hour = 7
    start_min = 45
    
    minutes_offset = hour_idx * 15 # offset en blocs de 15min
    
    start_dt = base_time.replace(hour=start_hour, minute=start_min) + timedelta(minutes=minutes_offset)
    end_dt = start_dt + timedelta(minutes=duration_blocks * 15)
    
    return TIMEZONE.localize(start_dt), TIMEZONE.localize(end_dt)

def extract_events_from_pdf():
    cal = Calendar()
    
    with pdfplumber.open("edt.pdf") as pdf:
        for page in pdf.pages:
            # 1. Repérer les lignes horizontales (lignes de temps)
            # pdfplumber permet de trouver les lignes graphiques
            rects = page.rects # Les cases de couleur ou bordures
            words = page.extract_words(keep_blank_chars=True)
            
            # Logique simplifiée pour l'exemple : 
            # On cherche la date (ex: "12/janv") pour déterminer le Lundi de la semaine
            [span_3](start_span)# Exemple 12/Janv
            week_start = None
            for w in words:
                if "/" in w['text'] and ("janv" in w['text'].lower() or "févr" in w['text'].lower()):
                    # Conversion basique "12/janv" -> Date objet (attention à l'année)
                    day, month_str = w['text'].split('/')
                    month_map = {"janv": 1, "févr": 2, "mars": 3} # à compléter
                    month = month_map.get(month_str.lower().strip(), 1)
                    year = 2026 # À automatiser selon l'année en cours
                    try:
                        week_start = datetime(year, month, int(day))
                    except:
                        continue
                    break
            
            if not week_start:
                continue

            # Définition approximative des colonnes (X axis) basé sur un A4 standard paysage
            # Tu devras ajuster ces valeurs avec des print(word['x0'])
            cols = {
                "Lundi": (50, 150),
                "Mardi": (150, 250),
                "Mercredi": (250, 350),
                "Jeudi": (350, 450),
                "Vendredi": (450, 550)
            }

            # On itère sur les mots pour trouver les cours
            # Ceci est une simplification. Pour un PDF complexe comme celui-ci,
            # il vaut mieux itérer sur les "rects" (les cases colorées/encadrées)
            
            # EXEMPLE DE LOGIQUE DE FILTRAGE[span_3](end_span)
            for text_obj in words:
                text = text_obj['text']
                
                # Filtrage Groupe: Si "/" présent, garder seulement si GB ou pas de groupe
                if "/" in text:
                    if "GB" in text:
                        pass # On garde
                    elif "GC" in text or "GA" in text:
                        [span_4](start_span)continue # On ignore[span_4](end_span)
                
                # Règle "Première rangée ignorée" (Orange)
                # Si tu détectes deux textes au même moment (overlap), 
                # ignore celui qui a le 'top' le plus petit (plus haut sur la page)
                
                # Création de l'événement (Pseudo-code, requiert l'extraction précise des coord)
                # e = Event()
                # e.name = f"{Matiere} ({Prof})"
                # e.begin = start_time
                # e.end = end_time
                # cal.events.add(e)
                pass 

    # Sauvegarde
    with open(ICAL_PATH, 'w') as f:
        f.writelines(cal.serialize_iter())

if __name__ == "__main__":
    download_pdf()
    extract_events_from_pdf()
    # Placeholder pour tester la génération sans parsing complexe
    c = Calendar()
    e = Event()
    e.name = "Test Automatique - TCP/IP"
    e.begin = "2026-01-13 07:45:00"
    e.duration = {"hours": 2}
    c.events.add(e)
    with open(ICAL_PATH, 'w') as f:
        f.writelines(c.serialize_iter())
