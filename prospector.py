"""
Prospecteur Google Maps — Version 3 — Workflow Claude Code Direct
Menu interactif complet — Exploitation maximale de l'API Places
"""

import requests, csv, time, sys, os, re, json, argparse
import concurrent.futures
from datetime import datetime
from urllib.parse import urlparse

# ─────────────────────────────────────────────────────────────
# COULEURS ANSI (fonctionnent dans PowerShell Windows 10+)
# ─────────────────────────────────────────────────────────────
R  = "\033[0m"       # reset
B  = "\033[1m"       # bold
DIM= "\033[2m"       # dim
RED= "\033[91m"      # rouge
GRN= "\033[92m"      # vert
YEL= "\033[93m"      # jaune
BLU= "\033[94m"      # bleu
MAG= "\033[95m"      # magenta
CYN= "\033[96m"      # cyan
WHT= "\033[97m"      # blanc

def c(color, text): return f"{color}{text}{R}"

# ─────────────────────────────────────────────────────────────
# NICHES — variantes de recherche pour maximiser la couverture
# ─────────────────────────────────────────────────────────────
# Pour chaque niche, on lance PLUSIEURS requêtes par ville →
# l'API retourne des business différents selon les mots-clés
# → double ou triple le nombre de candidats trouvés

NICHES = {
    "plombier":              ["plombier", "plomberie", "dépannage plombier"],
    "électricien":           ["électricien", "électricité générale", "installation électrique"],
    "chauffagiste":          ["chauffagiste", "installation chauffage", "dépannage chaudière"],
    "couvreur":              ["couvreur", "toiture", "couverture toiture réparation"],
    "maçon":                 ["maçon", "maçonnerie", "construction rénovation"],
    "serrurier":             ["serrurier", "serrurerie", "dépannage serrurerie"],
    "paysagiste":            ["paysagiste", "entretien jardin", "élagage arbre"],
    "menuisier":             ["menuisier", "menuiserie", "pose fenêtres volets"],
    "carreleur":             ["carreleur", "carrelage", "pose carrelage"],
    "peintre":               ["peintre en bâtiment", "peinture intérieure", "ravalement façade"],
    "charpentier":           ["charpentier", "charpente bois", "charpente rénovation"],
    "climatisation":         ["climatisation installation", "pompe à chaleur", "installation PAC"],
    "ramoneur":              ["ramonage cheminée", "ramoneur", "entretien cheminée"],
    "vitrier":               ["vitrier", "vitrerie miroiterie", "pose double vitrage"],
    "déménageur":            ["déménageur", "déménagement", "entreprise déménagement"],
    "plâtrier":              ["plâtrier", "plâtrerie", "isolation intérieure"],
    "terrassier":            ["terrassier", "terrassement", "VRD travaux"],
    "pisciniste":            ["pisciniste", "piscine installation", "entretien piscine"],
    "installateur solaire":  ["panneau solaire installation", "photovoltaïque installateur", "énergie solaire"],
    "nettoyage toiture":     ["nettoyage toiture", "démoussage toiture", "traitement toiture"],
}

NICHE_LABELS = list(NICHES.keys())

# ─────────────────────────────────────────────────────────────
# ZONES GÉOGRAPHIQUES — 450+ villes françaises
# ─────────────────────────────────────────────────────────────

ZONES = {
    "🇫🇷 France entière (450 villes)": [
        # Île-de-France
        "Paris 1er","Paris 11e","Paris 15e","Paris 18e","Paris 20e",
        "Boulogne-Billancourt","Saint-Denis","Montreuil","Argenteuil","Versailles",
        "Nanterre","Créteil","Vitry-sur-Seine","Asnières-sur-Seine","Colombes",
        "Aubervilliers","Aulnay-sous-Bois","Courbevoie","Champigny-sur-Marne","Saint-Maur-des-Fossés",
        "Rueil-Malmaison","Drancy","Noisy-le-Grand","Ivry-sur-Seine","Clichy",
        "Évry","Meaux","Melun","Mantes-la-Jolie","Cergy","Pontoise",
        "Poissy","Rambouillet","Palaiseau","Gif-sur-Yvette","Massy",
        # Auvergne-Rhône-Alpes
        "Lyon","Grenoble","Saint-Étienne","Clermont-Ferrand","Valence",
        "Annecy","Chambéry","Bourg-en-Bresse","Villeurbanne","Vienne",
        "Romans-sur-Isère","Bourgoin-Jallieu","Annonay","Oyonnax","Roanne",
        "Thonon-les-Bains","Aix-les-Bains","Albertville","Moulins","Montluçon",
        "Vichy","Issoire","Riom","Aurillac","Brioude",
        # Nouvelle-Aquitaine
        "Bordeaux","Pau","Bayonne","Périgueux","Agen",
        "Angoulême","La Rochelle","Poitiers","Niort","Rochefort",
        "Saintes","Libourne","Mérignac","Pessac","Bruges",
        "Mont-de-Marsan","Dax","Arcachon","Bergerac","Sarlat",
        "Brive-la-Gaillarde","Tulle","Guéret","La Souterraine","Ussel",
        # Occitanie
        "Toulouse","Montpellier","Nîmes","Perpignan","Béziers",
        "Narbonne","Carcassonne","Albi","Castres","Rodez",
        "Tarbes","Auch","Cahors","Mende","Millau",
        "Sète","Lunel","Montauban","Muret","Balma",
        # Hauts-de-France
        "Lille","Valenciennes","Dunkerque","Calais","Boulogne-sur-Mer",
        "Lens","Arras","Amiens","Beauvais","Compiègne",
        "Saint-Quentin","Laon","Roubaix","Tourcoing","Maubeuge",
        "Douai","Béthune","Cambrai","Abbeville","Péronne",
        # Normandie
        "Rouen","Caen","Le Havre","Cherbourg-en-Cotentin","Évreux",
        "Alençon","Flers","Lisieux","Bayeux","Saint-Lô",
        "Vire","Avranches","Argentan","Bernay","Louviers",
        "Dieppe","Fécamp","Elbeuf","Vernon","Gisors",
        # Grand Est
        "Strasbourg","Mulhouse","Reims","Metz","Nancy",
        "Colmar","Épinal","Troyes","Thionville","Forbach",
        "Longwy","Saint-Dizier","Chaumont","Bar-le-Duc","Verdun",
        "Haguenau","Saverne","Sélestat","Wissembourg","Sarreguemines",
        # Bretagne
        "Rennes","Brest","Quimper","Lorient","Vannes",
        "Saint-Malo","Saint-Nazaire","Saint-Brieuc","Concarneau","Dinan",
        "Morlaix","Lannion","Guingamp","Pontivy","Vitré",
        # Pays de la Loire
        "Nantes","Angers","Le Mans","Saint-Nazaire","La Roche-sur-Yon",
        "Cholet","Laval","Saumur","Châteaubriant","Mayenne",
        "Fontenay-le-Comte","Les Sables-d'Olonne","Saint-Jean-de-Monts","Ancenis","Challans",
        # Centre-Val de Loire
        "Orléans","Tours","Blois","Bourges","Chartres",
        "Châteauroux","Vendôme","Gien","Montargis","Vierzon",
        "Issoudun","Dreux","Chinon","Amboise","Romorantin",
        "Pithiviers","Châteaudun","Lamotte-Beuvron","Nogent-le-Rotrou","Saint-Amand-Montrond",
        # Bourgogne-Franche-Comté
        "Dijon","Besançon","Belfort","Mâcon","Chalon-sur-Saône",
        "Nevers","Auxerre","Sens","Avallon","Montceau-les-Mines",
        "Le Creusot","Autun","Lons-le-Saunier","Vesoul","Pontarlier",
        # Provence-Alpes-Côte d'Azur
        "Marseille","Nice","Toulon","Aix-en-Provence","Cannes",
        "Antibes","Avignon","Fréjus","La Seyne-sur-Mer","Hyères",
        "Arles","Martigues","Salon-de-Provence","Istres","Gap",
        "Draguignan","Grasse","Menton","Monaco","Aubagne",
        # Alsace (communes)
        "Obernai","Illkirch","Lingolsheim","Ostwald","Schiltigheim",
        # Occitanie (communes)
        "Auch","Foix","Pamiers","Oloron-Sainte-Marie","Lourdes",
        # Corse
        "Ajaccio","Bastia","Porto-Vecchio","Corte","Calvi",
        # DOM-TOM (optionnel)
        # "Fort-de-France","Pointe-à-Pitre","Saint-Denis de La Réunion",
    ],

    "📍 Région Centre (22 villes)": [
        "Orléans","Tours","Blois","Bourges","Chartres","Châteauroux",
        "Vendôme","Gien","Montargis","Vierzon","Issoudun","Dreux",
        "Chinon","Amboise","Saumur","Romorantin","Pithiviers",
        "Châteaudun","Nogent-le-Rotrou","Saint-Amand-Montrond",
        "Lamotte-Beuvron","La Ferté-Saint-Aubin",
    ],

    "🏙️ Grandes métropoles (15 villes)": [
        "Paris","Lyon","Marseille","Toulouse","Nice","Nantes",
        "Montpellier","Strasbourg","Bordeaux","Lille","Rennes",
        "Grenoble","Saint-Étienne","Dijon","Angers",
    ],

    "🏘️ Villes moyennes (60 villes)": [
        "Orléans","Tours","Dijon","Angers","Nîmes","Clermont-Ferrand",
        "Saint-Étienne","Le Havre","Toulon","Brest","Limoges","Amiens",
        "Perpignan","Metz","Besançon","Caen","Nancy","Reims",
        "Valenciennes","Pau","Bayonne","Troyes","Rouen","Mulhouse",
        "Poitiers","La Rochelle","Avignon","Cannes","Antibes","Chambéry",
        "Valence","Agen","Niort","Angoulême","Béziers","Colmar",
        "Belfort","Auxerre","Chartres","Bourges","Laval","Évreux",
        "Blois","Châteauroux","Cherbourg","Lorient","Vannes","Saint-Malo",
        "Quimper","Boulogne-sur-Mer","Calais","Dunkerque","Arras","Lens",
        "Beauvais","Compiègne","Meaux","Melun","Évry","Cergy",
    ],

    "✏️  Villes personnalisées": [],   # rempli interactivement
}

ZONE_LABELS = list(ZONES.keys())

# ─────────────────────────────────────────────────────────────
# FILTRES & API
# ─────────────────────────────────────────────────────────────

DETAILS_FIELDS = [
    "name","formatted_address","formatted_phone_number",
    "international_phone_number","website","rating",
    "user_ratings_total","business_status","opening_hours",
    "photos","url","place_id","types",
]

BAD_DOMAINS = [
    "facebook.com","instagram.com","linkedin.com","pagesjaunes.fr",
    "pagesblanches.fr","google.com","maps.google.com","yelp.com",
    "tripadvisor.com","lafourchette.com","thefork.com",
    "annuaire.com","europages.fr","kompass.com","mappy.com","planity.com",
]
LOW_END_BUILDERS = [
    "wix.com","wixsite.com","weebly.com","e-monsite.com","jimdo.com",
    "jimdofree.com","over-blog.com","overblog.com","sitebuilder.com",
    "webnode.fr","godaddy.com","strikingly.com","yolasite.com","simplesite.com",
]

PLACES_SEARCH_URL  = "https://maps.googleapis.com/maps/api/place/textsearch/json"
PLACES_DETAILS_URL = "https://maps.googleapis.com/maps/api/place/details/json"

# ─────────────────────────────────────────────────────────────
# SCORING
# ─────────────────────────────────────────────────────────────

def classify_website(website, check_live=False):
    if not website:
        return "aucun", 42
    domain = urlparse(website).netloc.lower().lstrip("www.")
    if any(b in domain for b in BAD_DOMAINS):
        if any(s in domain for s in ["facebook","instagram","linkedin"]):
            return "reseau_social", 36
        return "annuaire", 34
    if any(b in domain for b in LOW_END_BUILDERS):
        return "constructeur", 22
    if check_live:
        try:
            r = requests.head(website, timeout=6, allow_redirects=True,
                              headers={"User-Agent": "Mozilla/5.0"})
            if r.status_code >= 400:
                return "site_casse", 38
        except Exception:
            return "site_casse", 38
    return "site_reel", 2

def website_label_fr(label, website):
    d = urlparse(website).netloc.lower() if website else ""
    return {
        "aucun":        "Non",
        "reseau_social":"Réseaux sociaux uniquement",
        "annuaire":     "Annuaire/Pages Jaunes uniquement",
        "constructeur": f"Constructeur bas de gamme ({d})",
        "site_reel":    "Oui",
        "site_casse":   "Site inaccessible (HTTP error)",
    }.get(label, "?")

def score_prospect(details, min_reviews, min_photos, min_rating,
                   check_live=False, strict_no_website=True):
    score, raisons = 0, []
    status  = details.get("business_status","")
    phone   = details.get("formatted_phone_number","")
    reviews = details.get("user_ratings_total",0) or 0
    rating  = float(details.get("rating",0) or 0)
    website = details.get("website","") or ""
    photos  = details.get("photos",[]) or []
    n_photos= len(photos)

    if status == "CLOSED_PERMANENTLY":
        return -1, "EXCLU", ["Business fermé définitivement"]
    if not phone:
        return -1, "EXCLU", ["Aucun numéro de téléphone"]
    if n_photos < min_photos:
        return -1, "EXCLU", [f"{n_photos} photo(s) API — minimum {min_photos} requis"]
    if reviews < min_reviews:
        return -1, "EXCLU", [f"{reviews} avis — minimum {min_reviews} requis"]
    if rating > 0 and rating < min_rating:
        return -1, "EXCLU", [f"Note {rating}/5 trop basse (min {min_rating})"]

    # ── Filtre site web ────────────────────────────────────────
    web_label, web_bonus = classify_website(website, check_live)

    if strict_no_website:
        # Mode strict : SEUL "aucun" passe. Tout le reste est éliminé.
        # Facebook, Pages Jaunes, Wix, site pro = EXCLU.
        # "site_casse" passe seulement si --check-websites est actif (site véritablement mort).
        allowed = {"aucun", "site_casse"} if check_live else {"aucun"}
        if web_label not in allowed:
            labels = {
                "reseau_social": "Réseaux sociaux uniquement (Facebook/Instagram)",
                "annuaire":      "Présent sur Pages Jaunes/annuaire",
                "constructeur":  f"Site web existant ({urlparse(website).netloc.lower()})",
                "site_reel":     f"Site web existant ({urlparse(website).netloc.lower()})",
            }
            return -1, "EXCLU", [f"A un site web — {labels.get(web_label, website)}"]

    # Photos
    if n_photos >= 10:
        score += 28; raisons.append(f"{n_photos}+ photos API (excellent)")
    elif n_photos >= 7:
        score += 22; raisons.append(f"{n_photos} photos API (bon)")
    elif n_photos >= min_photos:
        score += 12; raisons.append(f"{n_photos} photos API (suffisant)")

    # Avis
    if reviews >= 200:
        score += 27; raisons.append(f"{reviews} avis (exceptionnel)")
    elif reviews >= 75:
        score += 22; raisons.append(f"{reviews} avis (très bon)")
    elif reviews >= 35:
        score += 15; raisons.append(f"{reviews} avis (bon)")
    else:
        score += 8;  raisons.append(f"{reviews} avis (correct)")

    # Site web
    score += web_bonus
    raisons.append({
        "aucun":        "Aucun site web → opportunité maximale",
        "reseau_social":"Réseaux sociaux uniquement",
        "annuaire":     "Annuaire uniquement (pas de vrai site)",
        "constructeur": "Site constructeur bas de gamme",
        "site_reel":    "Site web existant (à vérifier)",
        "site_casse":   "Site inaccessible (HTTP error)",
    }.get(web_label, ""))

    # Note
    if 4.0 <= rating <= 4.6:
        score += 10; raisons.append(f"Note {rating}/5 (idéale)")
    elif rating > 4.6:
        score += 6;  raisons.append(f"Note {rating}/5 (excellente)")
    elif rating >= min_rating:
        score += 3;  raisons.append(f"Note {rating}/5")

    if details.get("opening_hours"):
        score += 3; raisons.append("Horaires renseignés")

    types = details.get("types",[])
    pro = {"plumber","electrician","roofing_contractor","painter","general_contractor",
           "locksmith","landscaper","carpenter"}
    if any(t in pro for t in types):
        score += 2

    if score >= 72:   label = "CHAUD"
    elif score >= 48: label = "TIÈDE"
    elif score >= 28: label = "FROID"
    else:             label = "EXCLU"

    return score, label, [r for r in raisons if r]


# ─────────────────────────────────────────────────────────────
# APPELS API
# ─────────────────────────────────────────────────────────────

def search_places(query, api_key):
    results, params, pages = [], {"query":query,"language":"fr","region":"fr","key":api_key}, 0
    while pages < 3:
        try:
            resp = requests.get(PLACES_SEARCH_URL, params=params, timeout=12)
        except Exception:
            break
        data   = resp.json()
        status = data.get("status")
        if status == "REQUEST_DENIED":
            print(f"\n{c(RED,'✗')} Clé API refusée : {data.get('error_message','')}"); sys.exit(1)
        if status == "OVER_QUERY_LIMIT":
            time.sleep(3); continue
        if status not in ("OK","ZERO_RESULTS"): break
        results.extend(data.get("results",[]))
        pages += 1
        token = data.get("next_page_token")
        if not token or len(results) >= 60: break
        time.sleep(2.2)
        params = {"pagetoken": token, "key": api_key}
    return results

def get_place_details(place_id, api_key):
    try:
        resp = requests.get(PLACES_DETAILS_URL, params={
            "place_id": place_id, "fields": ",".join(DETAILS_FIELDS),
            "language": "fr", "key": api_key
        }, timeout=12)
        data = resp.json()
        return data.get("result", {}) if data.get("status") == "OK" else {}
    except Exception:
        return {}

def details_worker(args):
    place_id, api_key, niche, ville, check_live, min_reviews, min_photos, min_rating, strict_no_website, delay = args
    time.sleep(delay)
    details = get_place_details(place_id, api_key)
    if not details: return None
    score, label, raisons = score_prospect(
        details, min_reviews, min_photos, min_rating, check_live, strict_no_website
    )
    if label == "EXCLU": return None
    photos  = details.get("photos",[]) or []
    opening = details.get("opening_hours",{}) or {}
    horaires= " | ".join((opening.get("weekday_text") or [])[:3])
    website = details.get("website","") or ""
    wl, _   = classify_website(website)
    return {
        "priorite":       label,
        "score":          score,
        "nom_entreprise": details.get("name",""),
        "adresse":        details.get("formatted_address",""),
        "telephone":      details.get("formatted_phone_number",""),
        "telephone_intl": details.get("international_phone_number",""),
        "site_web":       website,
        "statut_web":     website_label_fr(wl, website),
        "note_google":    details.get("rating",""),
        "nombre_avis":    details.get("user_ratings_total",0),
        "nb_photos_api":  len(photos),
        "niche":          niche,
        "ville":          ville,
        "raisons":        " / ".join(raisons),
        "horaires_apercu":horaires,
        "lien_google_maps":details.get("url",""),
        "place_id":       place_id,
        "statut_appel":   "",
        "date_appel":     "",
        "notes":          "",
        "date_extraction":datetime.now().strftime("%Y-%m-%d"),
    }


# ─────────────────────────────────────────────────────────────
# LOGIQUE PRINCIPALE
# ─────────────────────────────────────────────────────────────

def run_prospection(cfg):
    api_key     = cfg["api_key"]
    niches_sel  = cfg["niches"]       # list of niche keys
    villes      = cfg["villes"]
    output_file = cfg["output"]
    top_n       = cfg["top_n"]        # 0 = tout exporter
    check_live  = cfg["check_live"]
    min_reviews = cfg["min_reviews"]
    min_photos  = cfg["min_photos"]
    min_rating  = cfg["min_rating"]
    workers     = cfg["workers"]
    multi_query = cfg["multi_query"]  # True = plusieurs variantes par niche

    # Construire la liste des (query, niche_label, ville)
    query_list = []
    for niche_key in niches_sel:
        variants = NICHES[niche_key] if multi_query else [niche_key]
        for ville in villes:
            for variant in variants:
                query_list.append((f"{variant} {ville}", niche_key, ville))

    total_q = len(query_list)
    print(f"\n{c(CYN,'═'*65)}")
    print(f"  {c(B+WHT,'PROSPECTION EN COURS')}")
    print(c(CYN,'═'*65))
    print(f"  Requêtes    : {c(YEL, str(total_q))} ({len(niches_sel)} niches × {len(villes)} villes"
          + (f" × {len(NICHES[niches_sel[0]])} variantes" if multi_query else "") + ")")
    print(f"  Filtres     : ≥{min_reviews} avis · ≥{min_rating}★ · ≥{min_photos} photos")
    print(f"  Threads     : {workers}\n")

    # Phase 1 : Text Search + pré-filtre
    candidates   = []
    seen_ids     = set()
    total_found  = 0

    for i, (query, niche_key, ville) in enumerate(query_list, 1):
        pct  = i / total_q * 100
        bar  = "█" * int(pct / 5) + "░" * (20 - int(pct / 5))
        print(f"\r  [{bar}] {pct:5.1f}%  {query[:55]:<55}  {c(GRN, str(len(candidates)))} candidats", end="", flush=True)

        raw = search_places(query, api_key)
        total_found += len(raw)
        for place in raw:
            pid = place.get("place_id")
            if not pid or pid in seen_ids: continue
            # Pré-filtre rapide sur données search (sans appel Details)
            r = place.get("user_ratings_total", 0) or 0
            rt = float(place.get("rating", 0) or 0)
            if r < min_reviews: continue
            if rt > 0 and rt < min_rating: continue
            seen_ids.add(pid)
            candidates.append((pid, niche_key, ville))

    print(f"\r  {c(GRN,'✓')} Phase 1 terminée : {total_found} business trouvés → "
          f"{c(YEL, str(len(candidates)))} candidats retenus pour analyse détaillée\n")

    if not candidates:
        print(f"  {c(RED,'Aucun candidat')} avec ces critères. Essayez de baisser les seuils.")
        return

    # Phase 2 : Details en parallèle
    print(f"  {c(CYN,'→')} Appels Details ({len(candidates)} × ~0.017$) sur {workers} threads...")
    strict_no_website = cfg.get("strict_no_website", True)
    args_list = [
        (pid, api_key, niche, ville, check_live, min_reviews, min_photos, min_rating,
         strict_no_website, 0.05 + (i % workers) * 0.08)
        for i, (pid, niche, ville) in enumerate(candidates)
    ]

    all_prospects = []
    with concurrent.futures.ThreadPoolExecutor(max_workers=workers) as ex:
        futures = {ex.submit(details_worker, a): a for a in args_list}
        done = 0
        for fut in concurrent.futures.as_completed(futures):
            done += 1
            res = fut.result()
            if res: all_prospects.append(res)
            if done % 15 == 0 or done == len(args_list):
                print(f"\r  Details : {done}/{len(args_list)} · "
                      f"{c(GRN, str(len(all_prospects)))} qualifiés", end="", flush=True)

    print(f"\n  {c(GRN,'✓')} Analyse terminée\n")

    # Tri
    order = {"CHAUD": 0, "TIÈDE": 1, "FROID": 2}
    all_prospects.sort(key=lambda x: (order.get(x["priorite"],3), -x["score"]))

    output = all_prospects[:top_n] if top_n > 0 else all_prospects

    if not output:
        print(f"  {c(RED,'Aucun prospect qualifié.')} Essayez d'élargir les critères.")
        return

    # Écriture CSV
    with open(output_file, "w", newline="", encoding="utf-8-sig") as f:
        writer = csv.DictWriter(f, fieldnames=list(output[0].keys()), delimiter=";")
        writer.writeheader()
        writer.writerows(output)

    # Résumé
    chauds = sum(1 for p in output if p["priorite"] == "CHAUD")
    tièdes = sum(1 for p in output if p["priorite"] == "TIÈDE")
    froids = sum(1 for p in output if p["priorite"] == "FROID")
    sans_site = sum(1 for p in output if p["statut_web"] == "Non")
    avg_ph = sum(p["nb_photos_api"] for p in output) / len(output)
    avg_rv = sum(p["nombre_avis"] for p in output) / len(output)

    print(c(CYN, "═"*65))
    print(f"  {c(B+WHT,'RÉSULTATS')}" + (f" — TOP {top_n}" if top_n else ""))
    print(c(CYN, "═"*65))
    print(f"  Total trouvés       : {total_found}")
    print(f"  Candidats analysés  : {len(candidates)}")
    print(f"  Prospects exportés  : {c(YEL, str(len(output)))}" +
          (f"  ({len(all_prospects)-top_n} supprimés, mode top-N)" if top_n and len(all_prospects)>top_n else ""))
    print(f"  {c(RED,'●')} CHAUD : {chauds}   {c(YEL,'●')} TIÈDE : {tièdes}   {c(BLU,'●')} FROID : {froids}")
    print(f"  Sans site web       : {sans_site}")
    print(f"  Moy. photos API     : {avg_ph:.1f}/10  |  Moy. avis : {avg_rv:.0f}")
    print(f"\n  {c(GRN,'✓')} Fichier : {c(B, output_file)}")
    print(c(CYN, "═"*65))

    print(f"\n  {c(B,'TOP PROSPECTS')} :")
    for i, p in enumerate(output[:10], 1):
        col    = RED if p["priorite"]=="CHAUD" else (YEL if p["priorite"]=="TIÈDE" else BLU)
        prio   = c(col+B, f"{p['priorite']:<5}")
        nom    = p["nom_entreprise"][:38]
        print(f"  {i:2d}. {prio} {p['score']:3d}pts  {nom:<38}  "
              f"{p['nombre_avis']:4d} avis  {p['nb_photos_api']:2d} photos  "
              f"{p['statut_web'][:22]}")
    print()


# ─────────────────────────────────────────────────────────────
# MENU INTERACTIF
# ─────────────────────────────────────────────────────────────

def clr():
    os.system("cls" if os.name == "nt" else "clear")

def ask(prompt, default=None, validator=None):
    """Input avec valeur par défaut et validation optionnelle."""
    hint = f" [{c(DIM, str(default))}]" if default is not None else ""
    while True:
        val = input(f"  {prompt}{hint} : ").strip()
        if not val and default is not None:
            return default
        if validator:
            ok, msg = validator(val)
            if not ok:
                print(f"  {c(RED,'✗')} {msg}")
                continue
        return val

def multiselect(items, title, min_sel=1, current=None):
    """Menu de sélection multiple par numéros (ex: 1,2,5 ou 1-5 ou 'tout')."""
    print(f"\n  {c(B+CYN, title)}")
    hint = 'Entrez les numéros séparés par virgule, plages (ex: 1-5), ou "tout"'
    print(f"  {c(DIM, hint)}\n")
    for i, item in enumerate(items, 1):
        sel_mark = c(GRN," ✓") if (current and item in current) else "  "
        print(f"  {c(DIM, str(i).rjust(3))}.{sel_mark} {item}")
    print()
    raw = input("  > ").strip().lower()
    if raw in ("tout", "all", "*"):
        return list(items)
    selected = []
    for part in raw.replace(" ","").split(","):
        if "-" in part:
            try:
                a, b = part.split("-")
                for n in range(int(a), int(b)+1):
                    if 1 <= n <= len(items):
                        selected.append(items[n-1])
            except Exception:
                pass
        elif part.isdigit():
            n = int(part)
            if 1 <= n <= len(items):
                selected.append(items[n-1])
    selected = list(dict.fromkeys(selected))  # déduplique en gardant l'ordre
    if len(selected) < min_sel:
        print(f"  {c(RED,'✗')} Sélectionnez au moins {min_sel} élément(s). Recommencez.")
        return multiselect(items, title, min_sel, current)
    return selected

def choose_one(items, title, default=0):
    """Menu de choix unique."""
    print(f"\n  {c(B+CYN, title)}\n")
    for i, item in enumerate(items, 1):
        marker = c(GRN," ►") if i-1 == default else "  "
        print(f"  {c(DIM, str(i).rjust(2))}.{marker} {item}")
    print()
    raw = input(f"  Votre choix [{default+1}] : ").strip()
    if not raw: return default
    if raw.isdigit() and 1 <= int(raw) <= len(items):
        return int(raw) - 1
    return default

def fmt_bool(b): return c(GRN,"Oui") if b else c(RED,"Non")
def fmt_priority(label):
    return {"CHAUD": c(RED+B,"🔴 CHAUD"), "TIÈDE": c(YEL,"🟡 TIÈDE"), "FROID": c(BLU,"🔵 FROID")}.get(label, label)

def header(title="PROSPECTEUR — WORKFLOW CLAUDE CODE"):
    clr()
    w = 65
    print(c(CYN, "╔" + "═"*(w-2) + "╗"))
    print(c(CYN,"║") + c(B+WHT, f"  {title:<{w-4}}") + c(CYN,"║"))
    print(c(CYN, "╚" + "═"*(w-2) + "╝"))

def summary_line(label, value, width=28):
    return f"  {c(DIM, label.ljust(width))} {value}"

def print_config(cfg):
    n_niches = len(cfg.get("niches",[]))
    n_villes = len(cfg.get("villes",[]))
    niches_sel = cfg.get("niches",[])
    variants = sum(len(NICHES[k]) for k in niches_sel) if cfg.get("multi_query") else n_niches
    total_q = n_niches * n_villes * (len(NICHES[niches_sel[0]]) if cfg.get("multi_query") and niches_sel else 1)
    cost_search  = total_q * 3 * 0.032
    cost_details = total_q * 15 * 0.017
    print(f"\n{c(CYN,'─'*65)}")
    print(f"  {c(B,'Configuration actuelle')}")
    print(c(CYN,'─'*65))
    print(summary_line("Niches", f"{c(YEL, str(n_niches))} ({', '.join(niches_sel[:4])}{'…' if n_niches>4 else ''})"))
    print(summary_line("Zone géographique", f"{c(YEL, str(n_villes))} villes"))
    print(summary_line("Requêtes par niche×ville", c(YEL, str(len(NICHES[niches_sel[0]]) if cfg.get('multi_query') and niches_sel else 1) + " variantes")))
    print(summary_line("Total requêtes estimé", c(YEL, str(total_q))))
    strict = cfg.get("strict_no_website", True)
    site_filter = c(GRN, "AUCUN site (strict)") if strict else c(YEL, "Tous types acceptés")
    print(summary_line("Filtre site web", site_filter))
    print(summary_line("Autres filtres",
        f"≥{cfg.get('min_reviews',20)} avis · ≥{cfg.get('min_rating',3.8)}★ · ≥{cfg.get('min_photos',3)} photos"))
    print(summary_line("Mode", c(MAG, "TOP " + str(cfg['top_n'])) if cfg.get('top_n') else c(GRN, "Tout exporter")))
    print(summary_line("Vérif. sites web", fmt_bool(cfg.get("check_live", False))))
    print(summary_line("Fichier de sortie", c(GRN, cfg.get("output","prospects.csv"))))
    print(summary_line("Coût API estimé (max)", c(YEL, f"~{cost_search+cost_details:.2f}$")))
    print(summary_line("Coût réel attendu", c(GRN, f"~{(cost_search+cost_details)*0.35:.2f}$")))
    print(c(CYN,'─'*65))


def interactive_menu():
    """Menu interactif principal — configure et lance la prospection."""

    # ── 1. Clé API ─────────────────────────────────────────────
    header()
    print(f"\n  {c(B,'Étape 1/6 — Clé API Google Places')}\n")
    api_key = os.environ.get("GOOGLE_PLACES_API_KEY","")
    if api_key:
        print(f"  {c(GRN,'✓')} Clé trouvée dans GOOGLE_PLACES_API_KEY\n")
        print(f"  {c(DIM, '  ...'+api_key[-8:])}")
        change = input(f"\n  Utiliser cette clé ? [O/n] : ").strip().lower()
        if change in ("n","non","no"):
            api_key = ""
    if not api_key:
        print(f"  Obtenez votre clé : {c(CYN,'https://console.cloud.google.com/apis/credentials')}")
        print(f"  (activez 'Places API' dans APIs & Services > Library)\n")
        api_key = input("  Entrez votre clé API : ").strip()
        if not api_key:
            print(f"  {c(RED,'Clé manquante. Arrêt.')}"); sys.exit(1)

    cfg = {
        "api_key":     api_key,
        "niches":      ["plombier","électricien"],
        "villes":      ZONES["🏘️ Villes moyennes (60 villes)"],
        "output":      f"prospects_{datetime.now().strftime('%Y%m%d_%H%M')}.csv",
        "top_n":       20,
        "check_live":  False,
        "min_reviews": 20,
        "min_rating":  3.8,
        "min_photos":       3,
        "workers":          8,
        "multi_query":      True,
        "strict_no_website": True,   # AUCUN site web — élimine Facebook, Wix, Pages Jaunes
    }

    while True:
        header()
        print_config(cfg)

        print(f"\n  {c(B,'Que souhaitez-vous configurer ?')}\n")
        print(f"  {c(YEL,'1.')} Niches a prospecter")
        print(f"  {c(YEL,'2.')} Zone geographique")
        print(f"  {c(YEL,'3.')} Filtres qualite              {c(DIM,'(avis, photos, note min)')}")
        print(f"  {c(YEL,'4.')} Mode de sortie               {c(DIM,'(top-N ou tout exporter)')}")
        print(f"  {c(YEL,'5.')} Options avancees             {c(DIM,'(verif sites, threads, variantes)')}")
        print(f"  {c(YEL,'6.')} Fichier de sortie")
        print()
        print(f"  {c(GRN+B,'0. LANCER LA PROSPECTION')}")
        print(f"  {c(RED,'q.')} Quitter")
        print()
        choix = input("  Votre choix : ").strip().lower()

        if choix == "1":
            header("NICHES A PROSPECTER")
            cfg["niches"] = multiselect(NICHE_LABELS, "Selectionnez les niches", min_sel=1, current=cfg["niches"])

        elif choix == "2":
            header("ZONE GEOGRAPHIQUE")
            idx = choose_one(ZONE_LABELS, "Choisissez une zone predefinee", default=0)
            chosen_zone = ZONE_LABELS[idx]
            if "personnalis" in chosen_zone:
                print("\n  Entrez les villes separees par virgule :")
                raw_villes = input("  > ").strip()
                cfg["villes"] = [v.strip() for v in raw_villes.split(",") if v.strip()]
            else:
                cfg["villes"] = list(ZONES[chosen_zone])
            nb = len(cfg["villes"])
            print(f"\n  {c(GRN,'OK')} {nb} villes selectionnees")
            input("  [Entree pour continuer]")

        elif choix == "3":
            header("FILTRES QUALITE")
            print(f"\n  {c(DIM,'Ces seuils eliminent les prospects non-exploitables pour le workflow')}\n")

            print(f"  {c(B,'Filtre site web')} {c(DIM,'(critique)')}")
            cur_strict = cfg.get("strict_no_website", True)
            print(f"  Actuellement : {c(GRN,'AUCUN site (strict)') if cur_strict else c(YEL,'Tous types acceptes')}")
            print(f"  {c(DIM,'Mode strict  : exclut Facebook, Pages Jaunes, Wix, tout site — seulement NO WEBSITE')}")
            print(f"  {c(DIM,'Mode souple  : accepte aussi reseaux sociaux + annuaires (Pages Jaunes, etc.)')}")
            sw = input("  Mode strict (pas de site du tout) ? [O/n] : ").strip().lower()
            cfg["strict_no_website"] = sw not in ("n","non","no")

            print(f"\n  {c(B,'Avis Google minimum')}")
            val = input(f"  Avis min [{cfg['min_reviews']}] : ").strip()
            if val.isdigit(): cfg["min_reviews"] = int(val)
            print(f"\n  {c(B,'Photos API minimum (0-10)')}")
            val = input(f"  Photos min [{cfg['min_photos']}] : ").strip()
            if val.isdigit(): cfg["min_photos"] = int(val)
            print(f"\n  {c(B,'Note Google minimum')}")
            val = input(f"  Note min [{cfg['min_rating']}] : ").strip()
            try: cfg["min_rating"] = float(val)
            except Exception: pass
            input(f"\n  {c(GRN,'OK')} Filtres mis a jour. [Entree]")

        elif choix == "4":
            header("MODE DE SORTIE")
            opts = [
                "Top-N : scanner beaucoup, exporter les N meilleurs",
                "Tout exporter : tous les prospects qualifies dans le CSV",
            ]
            idx = choose_one(opts, "Mode de sortie")
            if idx == 0:
                val = input(f"\n  Combien de prospects dans le top ? [{cfg['top_n']}] : ").strip()
                if val.isdigit(): cfg["top_n"] = int(val)
            else:
                cfg["top_n"] = 0

        elif choix == "5":
            header("OPTIONS AVANCEES")
            print(f"\n  {c(B,'Variantes de requete par niche')}")
            print(f"  {c(DIM,'Oui -> plusieurs termes par niche (~3x plus de resultats, ~3x plus de requetes)')}")
            mq = input("  Activer les variantes ? [O/n] : ").strip().lower()
            cfg["multi_query"] = mq not in ("n","non","no")
            print(f"\n  {c(B,'Verification HTTP des sites web')}")
            print(f"  {c(DIM,'Detecte les sites castes (404/timeout), ajoute ~5s par prospect')}")
            cl = input("  Activer ? [o/N] : ").strip().lower()
            cfg["check_live"] = cl in ("o","oui","y","yes")
            print(f"\n  {c(B,'Threads paralleles')}")
            val = input(f"  Nombre de threads [{cfg['workers']}] : ").strip()
            if val.isdigit(): cfg["workers"] = int(val)
            input(f"\n  {c(GRN,'OK')} Options enregistrees. [Entree]")

        elif choix == "6":
            header("FICHIER DE SORTIE")
            print("\n  Nom du fichier CSV :")
            val = input(f"  [{cfg['output']}] : ").strip()
            if val: cfg["output"] = val

        elif choix in ("0", ""):
            header("CONFIRMATION")
            print_config(cfg)
            n_niches = len(cfg["niches"])
            n_villes = len(cfg["villes"])
            variants = len(NICHES[cfg["niches"][0]]) if cfg.get("multi_query") and cfg["niches"] else 1
            total_q  = n_niches * n_villes * variants
            cost     = total_q * 3 * 0.032 + total_q * 12 * 0.017
            duree    = max(1, total_q // 30)
            print(f"\n  Cout API max : {c(YEL, f'~{cost:.2f}$')}  |  Duree estimee : {c(YEL, f'~{duree} min')}")
            print()
            confirm = input(f"  {c(GRN+B,'Lancer ? [O/n]')} : ").strip().lower()
            if confirm in ("", "o", "oui", "y", "yes"):
                print()
                run_prospection(cfg)
                input(f"\n  {c(GRN,'OK')} Termine. [Entree pour revenir au menu]")

        elif choix in ("q", "quit", "exit"):
            print(f"\n  {c(DIM,'Au revoir.')}\n")
            import sys; sys.exit(0)


# ─────────────────────────────────────────────────────────────
# POINT D'ENTREE
# ─────────────────────────────────────────────────────────────

def main():
    import argparse as _ap
    parser = _ap.ArgumentParser(add_help=False)
    parser.add_argument("--key")
    parser.add_argument("--niche")
    parser.add_argument("--villes")
    parser.add_argument("--output",  default=None)
    parser.add_argument("--mode",    choices=["standard","scan"], default="standard")
    parser.add_argument("--top",     type=int, default=20)
    parser.add_argument("--region",  choices=["france","centre","grandes","moyennes"])
    parser.add_argument("--check-websites", action="store_true")
    parser.add_argument("--workers", type=int, default=8)
    parser.add_argument("--min-reviews", type=int, default=20)
    parser.add_argument("--min-photos",  type=int, default=3)
    parser.add_argument("--min-rating",  type=float, default=3.8)
    parser.add_argument("--allow-website", action="store_true",
                        help="Accepter les prospects avec Facebook/Annuaires (desactive strict_no_website)")
    args, _ = parser.parse_known_args()

    # Sans arguments substantiels -> menu interactif
    if not any([args.key, args.niche, args.villes, args.region]):
        interactive_menu()
        return

    # Mode CLI direct
    api_key = args.key or os.environ.get("GOOGLE_PLACES_API_KEY","")
    if not api_key:
        api_key = input("Cle API Google Places : ").strip()
        if not api_key: sys.exit(1)

    niches_sel = [n.strip() for n in args.niche.split(",")] if args.niche else ["plombier"]
    niches_sel = [n for n in niches_sel if n in NICHES] or ["plombier"]

    zone_keys = list(ZONES.keys())
    region_map = {"france": zone_keys[0], "centre": zone_keys[1],
                  "grandes": zone_keys[2], "moyennes": zone_keys[3]}
    if args.region and args.region in region_map:
        villes = list(ZONES[region_map[args.region]])
    elif args.villes:
        villes = [v.strip() for v in args.villes.split(",") if v.strip()]
    else:
        villes = list(ZONES[zone_keys[3]])

    ts = datetime.now().strftime("%Y%m%d_%H%M")
    cfg = {
        "api_key":           api_key,
        "niches":            niches_sel,
        "villes":            villes,
        "output":            args.output or f"prospects_{ts}.csv",
        "top_n":             args.top if args.mode == "scan" else 0,
        "check_live":        args.check_websites,
        "min_reviews":       args.min_reviews,
        "min_rating":        args.min_rating,
        "min_photos":        args.min_photos,
        "workers":           args.workers,
        "multi_query":       True,
        "strict_no_website": not args.allow_website,
    }
    run_prospection(cfg)


if __name__ == "__main__":
    main()
