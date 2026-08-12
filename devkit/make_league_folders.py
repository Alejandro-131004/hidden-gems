#!/usr/bin/env python3
"""
make_league_folders.py — create one folder per league inside data/, named

    "<n> - [<CODE>] - (<League>)"     e.g.  "1 - [GER] - (2. Bundesliga)"

and write a COUNTRY_CODES.txt reference (CODE: Country).

Order follows the DRIBBLIFY priority map top-to-bottom, left-to-right:
green tier first, then yellow, then red; within a country the leagues run
left to right. Numbering is continuous across the whole list (1..N).

Run:
    python make_league_folders.py            # create the folders
    python make_league_folders.py --dry-run  # just print what it would make
    python make_league_folders.py --tiers green,yellow
    python make_league_folders.py --out "C:/path/to/data"
"""

import sys
from pathlib import Path

# ---------------------------------------------------------------------------
# The map, in PDF order. Each row:
#   (tier, Portuguese name, English name, FIFA code, [leagues left->right])
# Country names are translated from the Portuguese PDF; codes are the football
# (FIFA) 3-letter codes, so Germany = GER (ISO would be DEU).
# ---------------------------------------------------------------------------
LEAGUE_MAP = [
    # ---- GREEN (priority 1) -------------------------------------------------
    ("green", "Alemanha",   "Germany",     "GER", ["2. Bundesliga", "3. Liga"]),
    ("green", "Argentina",  "Argentina",   "ARG", ["Primera Nacional"]),
    ("green", "Brasil",     "Brazil",      "BRA", ["Série B", "Série C"]),
    ("green", "Colômbia",   "Colombia",    "COL", ["Liga BetPlay", "Torneo BetPlay"]),
    ("green", "Dinamarca",  "Denmark",     "DEN", ["Superliga", "1st Division"]),
    ("green", "Espanha",    "Spain",       "ESP", ["Primera Division RFEF", "Segunda Division RFEF"]),
    ("green", "França",     "France",      "FRA", ["Ligue 2", "Ligue 3", "National 1"]),
    ("green", "Grécia",     "Greece",      "GRE", ["Stoiximan Super League", "Super League 2"]),
    ("green", "Holanda",    "Netherlands", "NED", ["Eredivisie", "Eerste Divisie"]),
    ("green", "Noruega",    "Norway",      "NOR", ["Eliteserien", "Obos Ligaen"]),
    ("green", "Portugal",   "Portugal",    "POR", ["Liga 3", "Campeonato de Portugal"]),
    ("green", "Suécia",     "Sweden",      "SWE", ["Allsvenskan", "Superettan"]),

    # ---- YELLOW (priority 2) ------------------------------------------------
    ("yellow", "Áustria",       "Austria",       "AUT", ["Bundesliga", "2. Liga"]),
    ("yellow", "Bélgica",       "Belgium",       "BEL", ["Pro League", "Challenger Pro League"]),
    ("yellow", "Bulgária",      "Bulgaria",      "BUL", ["First League"]),
    ("yellow", "Chéquia",       "Czechia",       "CZE", ["Chance Liga"]),
    ("yellow", "Chile",         "Chile",         "CHI", ["Primera División"]),
    ("yellow", "China",         "China",         "CHN", ["CSL"]),
    ("yellow", "Chipre",        "Cyprus",        "CYP", ["1. Division"]),
    ("yellow", "Coreia do Sul", "South Korea",   "KOR", ["K League 1"]),
    ("yellow", "Croácia",       "Croatia",       "CRO", ["Superleague", "First NL"]),
    ("yellow", "Escócia",       "Scotland",      "SCO", ["Premiership", "Championship"]),
    ("yellow", "Eslováquia",    "Slovakia",      "SVK", ["Niké Liga"]),
    ("yellow", "Eslovénia",     "Slovenia",      "SVN", ["1. SNL"]),
    ("yellow", "EUA",           "USA",           "USA", ["Major League Soccer", "USL Championship"]),
    ("yellow", "Hungria",       "Hungary",       "HUN", ["NB I"]),
    ("yellow", "Israel",        "Israel",        "ISR", ["Ligat ha'Al"]),
    ("yellow", "Itália",        "Italy",         "ITA", ["Serie B", "Serie C"]),
    ("yellow", "Japão",         "Japan",         "JPN", ["J1 League", "J2 League"]),
    ("yellow", "México",        "Mexico",        "MEX", ["Liga MX", "Liga de Expansión MX"]),
    ("yellow", "Polónia",       "Poland",        "POL", ["Ekstraklasa", "I Liga"]),
    ("yellow", "Roménia",       "Romania",       "ROU", ["Superliga"]),
    ("yellow", "Sérvia",        "Serbia",        "SRB", ["Super Liga"]),
    ("yellow", "Suíça",         "Switzerland",   "SUI", ["Super League"]),
    ("yellow", "Turquia",       "Turkey",        "TUR", ["Süper Lig", "1. Lig"]),
    ("yellow", "Ucrânia",       "Ukraine",       "UKR", ["VBET League"]),
    ("yellow", "Uruguai",       "Uruguay",       "URU", ["Primera División"]),

    # ---- RED (priority 3) ---------------------------------------------------
    # PDF cell for South Africa reads just "1" (truncated); it's the Premier Division.
    ("red", "África do Sul", "South Africa",           "RSA", ["Premier Division"]),
    ("red", "Albânia",       "Albania",                "ALB", ["Abissnet Superiore"]),
    ("red", "Andorra",       "Andorra",                "AND", ["1a Divisió"]),
    ("red", "Argélia",       "Algeria",                "ALG", ["Ligue 1"]),
    ("red", "Arménia",       "Armenia",                "ARM", ["IDBank Premier League"]),
    ("red", "Azerbaijão",    "Azerbaijan",             "AZE", ["Premyer Liqa"]),
    ("red", "Bolívia",       "Bolivia",                "BOL", ["LFPB"]),
    ("red", "Bósnia",        "Bosnia and Herzegovina", "BIH", ["Premijer Liga"]),
    ("red", "Costa Rica",    "Costa Rica",             "CRC", ["Primera División"]),
    ("red", "Equador",       "Ecuador",                "ECU", ["Liga Pro"]),
    ("red", "Estónia",       "Estonia",                "EST", ["A.LeCoq Premium Liiga"]),
    ("red", "Finlândia",     "Finland",                "FIN", ["Veikkausliiga"]),
    ("red", "Geórgia",       "Georgia",                "GEO", ["Erovnuli Liga"]),
    ("red", "Letónia",       "Latvia",                 "LVA", ["Virsliga"]),
    ("red", "Lituânia",      "Lithuania",              "LTU", ["A Lyga"]),
    ("red", "Marrocos",      "Morocco",                "MAR", ["Botola Pro"]),
    ("red", "Montenegro",    "Montenegro",             "MNE", ["First League"]),
    ("red", "Paraguai",      "Paraguay",               "PAR", ["Division Profesional"]),
    ("red", "Peru",          "Peru",                   "PER", ["Primera División"]),
    ("red", "Qatar",         "Qatar",                  "QAT", ["Qatar Stars League"]),
    ("red", "Tunísia",       "Tunisia",                "TUN", ["Ligue 1"]),
    ("red", "Uzbequistão",   "Uzbekistan",             "UZB", ["Super League"]),
    ("red", "Venezuela",     "Venezuela",              "VEN", ["Primera División"]),
]

SEASON = "2025-2026"     # league folders are created inside data/<SEASON>

ILLEGAL = '<>:"/\\|?*'   # characters Windows won't allow in a folder name


def safe(name):
    for ch in ILLEGAL:
        name = name.replace(ch, "-")
    return name.strip().rstrip(".")   # Windows also dislikes a trailing dot


def season_dir():
    """This script lives in devkit/. The data folder is one level out:
    devkit/ -> repo root -> data/<SEASON>. That folder must already exist —
    this script never creates data/ or the season folder."""
    return Path(__file__).resolve().parent.parent / "data" / SEASON


def main():
    argv = sys.argv[1:]
    dry = "--dry-run" in argv
    tiers = ["green", "yellow", "red"]
    out = None
    i = 0
    while i < len(argv):
        if argv[i] == "--tiers" and i + 1 < len(argv):
            tiers = [t.strip() for t in argv[i + 1].split(",")]; i += 2
        elif argv[i] == "--out" and i + 1 < len(argv):
            out = Path(argv[i + 1]); i += 2
        elif argv[i] == "--dry-run":
            i += 1
        else:
            i += 1

    data_dir = out or season_dir()
    if not data_dir.is_dir():
        print(f"target folder does not exist: {data_dir}")
        print("Create data/<SEASON> yourself first — this script will not create")
        print("parent folders (so it can't accidentally make devkit/data).")
        return

    print(f"data folder : {data_dir}")
    print(f"tiers       : {', '.join(tiers)}")
    print(f"mode        : {'DRY RUN (nothing created)' if dry else 'creating folders'}")
    print()

    rows = [r for r in LEAGUE_MAP if r[0] in tiers]

    n = 0
    codes = {}          # code -> (english, portuguese), first seen order
    code_order = []
    for tier, pt, en, code, leagues in rows:
        if code not in codes:
            codes[code] = (en, pt)
            code_order.append(code)
        for league in leagues:
            n += 1
            folder = safe(f"{n} - [{code}] - ({league})")
            print(f"  {folder}")
            if not dry:
                fdir = data_dir / folder
                fdir.mkdir(exist_ok=True)
                # SIZE.txt holds the expected row count (the Wyscout result
                # counter). Created empty; you fill in the number. Never
                # overwritten, so re-running won't wipe counts you've entered.
                size = fdir / "SIZE.txt"
                if not size.exists():
                    size.write_text("", encoding="utf-8")

    # reference file
    lines = [
        "Country codes used in the league folder names.",
        "Format:  CODE: English (Portuguese as in the PDF)",
        "",
    ]
    for code in code_order:
        en, pt = codes[code]
        lines.append(f"{code}: {en} ({pt})")
    ref_text = "\n".join(lines) + "\n"

    if not dry:
        (data_dir / "COUNTRY_CODES.txt").write_text(ref_text, encoding="utf-8")

    print()
    print(f"{n} league folders across {len(code_order)} countries"
          f"{' (dry run)' if dry else ' created'}")
    print(f"reference   : {data_dir / 'COUNTRY_CODES.txt'}"
          f"{' (dry run)' if dry else ''}")


if __name__ == "__main__":
    main()
