"""
Curated machine-code -> clinic labo_param / service_tarification mapping.

These are the ONLY confident, human-verified matches. Any machine code NOT in
these maps is deliberately left unmatched (staged as 'pending') rather than
guessed - matching French free-text clinical params automatically is unsafe.

HOW THESE MAPPINGS ARE BUILT (standard method - use this for any new machine):
Do NOT scan the full labo_param table by name/abbreviation - it has 650 rows
with many duplicate names across unrelated panels. Instead, find the specific
exam's service_tarification_id first, then join to get ONLY that exam's own
scoped, unambiguous param list:

    SELECT st.id, st.name, st.is_composed,
           lp.id AS param_id, lp.name, lp.abbreviation, lp.um,
           ltp.display_order, tech.techniques
    FROM clinic_management.service_tarification st
    LEFT JOIN labo.labo_test_param ltp ON ltp.service_tarification_id = st.id
    LEFT JOIN labo.labo_param lp ON lp.id = ltp.param_id
    LEFT JOIN LATERAL (
        SELECT STRING_AGG(lt.name, ', ' ORDER BY lt.id) AS techniques
        FROM labo.labo_technique lt WHERE lt.service_tarification_id = st.id
    ) tech ON TRUE
    WHERE st.old_table = 'LABO' AND st.deleted_at IS NULL AND st.id = <exam_id>
    ORDER BY st.name, ltp.display_order NULLS LAST, lp.name;

Notes on this join:
 - is_composed=false / no labo_test_param rows means this exam has NO param
   breakdown at all - its result is stored WITHOUT a param_id, directly
   against appointment_tarification_id (service_tarification_id alone is the
   complete, correct match - see API_LABO_MACHINE_RESULT.md's
   "service_tarification_id (and no param_id)" path). Nothing missing here.
 - is_composed=true rows give the exact, disambiguated param list for that
   exam, in display order, often tagged with which analyzer/technique
   produces them - a strong cross-check, WHEN PRESENT. It is NOT always
   present even for exams genuinely run on that machine (I-Smart and
   Selectra both have real, currently-used exams with zero technique rows -
   the technique table is incomplete metadata, not authoritative).
 - WATCH FOR DUPLICATE EXAM NAMES for the same real-world test (e.g. English
   "SGOT/SGPT" vs French "TGO/TGP" both existing as separate exams; a
   composed "Transaminases"/"Bilirubine" panel existing ALONGSIDE standalone
   single-value exams with matching names). When this happens, do NOT trust
   the technique tag alone - check actual historical order volume instead:
       SELECT at.tarification_id, st.name, count(*)
       FROM clinic.appointment_tarification at
       JOIN clinic_management.service_tarification st ON st.id = at.tarification_id
       WHERE at.tarification_id IN (<candidate ids>)
       GROUP BY at.tarification_id, st.name;
   The exam actually used in day-to-day ordering (highest count) wins - e.g.
   Transaminases beat TGO/TGP 1290-to-6, Bilirubine (composed) beat its own
   standalone Dir/Ind/Tot entries 639-to-~70 each. labo_result itself only
   had 8 rows total clinic-wide as of 2026-07-19 (not yet used at volume),
   so it could NOT be used for this cross-check - appointment_tarification
   (what staff actually order) was the only reliable historical signal.

MAP entry format: code -> (param_id, service_tarification_id,
                            service_tarification_name, abbrev, name)
param_id is None for non-composed exams (service_tarification_id alone is
the complete match in that case). Every code carries its OWN
service_tarification_id/name - do NOT assume one exam per machine (Selectra
alone spans ~20 different exams; only XN-330's CBC panel happens to share a
single one, FNS).

XN-330 CBC map: built from service_tarification_id=421 ("FNS", the highest-
volume exam, 6984 occurrences), all 15 rows tagged technique "SYSMEX XS
500i". Verified with the user on 2026-07-16. MCH->TGMH(99307) was initially
held out pending verification; the FNS join confirmed TGMH sits at
display_order 6 (between VGM/MCV and CCMH/MCHC, the standard CBC panel
position for MCH) under the same technique as the rest of the panel, so it
was added as a confident match. Two FNS rows are deliberately NOT mapped:
id 99002 "NUMERATION SANGUINE" (NS) and id 99147 "EQUILIBRE LEUCOCYTAIRE" -
these are section headers in this LIS, not individual measured values.
"""

# code -> (param_id, service_tarification_id, service_tarification_name, abbrev, name)
XN330_MAP = {
    "WBC":   (81,    421, "FNS", "GB",       "Globules Blancs"),
    "RBC":   (80,    421, "FNS", "GR",       "Globules Rouges"),
    "HGB":   (98000, 421, "FNS", "hémoglob", "Hémoglobine"),
    "HCT":   (97000, 421, "FNS", "Hemcte.",  "Hématocrite"),
    "PLT":   (93,    421, "FNS", "pettes",   "Plaquettes"),
    "MCV":   (99478, 421, "FNS", "VGM",      "VGM"),
    "MCH":   (99307, 421, "FNS", "TGMH",     "TGMH"),
    "MCHC":  (10100, 421, "FNS", "CCMH",     "CCMH"),
    "NEUT%": (99134, 421, "FNS", "Neu",      "Neutrophiles"),
    "LYMPH%":(99137, 421, "FNS", "Lympho",   "Lymphocytes"),
    "MONO%": (99136, 421, "FNS", "Mono",     "Monocytes"),
    "EO%":   (99950, 421, "FNS", "eosino",   "Eosinophiles"),
    "BASO%": (99138, 421, "FNS", "Baso",     "Basophiles"),
}

# I-Smart 30 PRO (ISE electrolyte panel). Verified with the user on
# 2026-07-19 via real captured data (Na+/K+/Cl-/Ca2+ all actually
# transmitted). Ca2+ deliberately excluded - the nurse confirmed the clinic
# does not need/use that value from this machine (likely appeared only
# because the user was experimenting with the machine's test panel config).
# Test codes below are exactly what ismart.py's decode_record() extracts
# (the ^-delimited field's second-to-last part), confirmed against real
# capture: "^^^Na+^M" -> "Na+", etc.
ISMART_MAP = {
    "Na+": (99446, 452, "Ionogramme sanguin", "NA",  "Natrémie"),
    "K+":  (99422, 452, "Ionogramme sanguin", "K",   "Kaliémie"),
    "Cl-": (99925, 452, "Ionogramme sanguin", "Cl-", "Chloremie"),
}

# Selectra chemistry analyzer (runs the ELITech/LIS2-A software stack).
# Test codes below are exactly what selectra.py's decode_record() extracts:
# the LAST ^-delimited part of the test field (e.g. "^^^Uree^Uree uv sl" ->
# "Uree uv sl", NOT "Uree"). This is the machine's own method-name string,
# confirmed against real captures for the first 7 rows below (2026-07-15/16).
# The remaining rows come from the machine's on-screen test menu (seen
# 2026-07-19) but have NOT yet been confirmed against a real transmitted
# record - the exact string it sends could differ slightly (spacing,
# capitalization). Flagged "provisional" below; fix the key once a real
# capture comes through if a provisional code doesn't match.
SELECTRA_MAP = {
    # --- confirmed against real captured ASTM/LIS2-A bytes ---
    "Uree uv sl":      (None,  538, "Urémie",                 "Uree",  "Urémie"),
    "Cholesterol":     (None,  373, "Cholestérol",            "Chol",  "Cholestérol"),
    "SGOT":            (99952, 528, "Transaminases",          "SGOT",  "SGOT"),
    "SGPT":            (99953, 528, "Transaminases",          "SGPT",  "SGPT"),
    "Phosphatase Alc": (None,  481, "Phosphatases alcalines", "PAL",   "Phosphatases alcalines"),
    "Creatinine":      (None,  392, "Créatinémie",            "Crea",  "Créatinémie"),
    "GGT":             (None,  429, "Gamma GT",               "GGT",   "Gamma GT"),

    # --- provisional: from the machine's test menu, not yet capture-confirmed ---
    "Glucose pap sl":  (None,  433, "Glycémie",                 "Gluc",  "Glycémie"),
    "Acide Urique":    (None,  337, "Acide urique",             "AcUr",  "Acide urique"),
    "Calcium":         (99736, 366, "Calcémie",                 "Ca",    "Calcémie"),
    "Phosphore":       (None,  483, "Phosphorémie",             "Phos",  "Phosphorémie"),
    "Triglycerides":   (None,  530, "Triglycérides",            "TG",    "Triglycérides"),
    "Cholesterol HDL": (None,  374, "Cholestérol HDL",          "HDL",   "Cholestérol HDL"),
    "LDH-L SL":        (None,  456, "LDH",                      "LDH",   "LDH"),
    "Proteines U":     (99561, 488, "Protéines des 24 heures",  "Prot U","Taux (Protéines des 24h)"),
    "CRP IP V3":       (None,  393, "CRP",                      "CRP",   "CRP"),
    "BILI TOTAL BIO":  (99954, 358, "Bilirubine",               "BiliT", "Bilirubine Totale"),
    "BILI DIRECT BIO": (99955, 358, "Bilirubine",               "BiliD", "Bilirubine Directe"),
    "CK NAK":          (None,  376, "CK - NAC",                 "CK",    "CK - NAC"),
    "CK-NAC":          (None,  376, "CK - NAC",                 "CK",    "CK - NAC"),  # alt spelling, same target

    # Medium confidence (thin order-volume margin, or a plausible-but-not-
    # certain choice between two standalone exams with the same name) -
    # flagged to the user on 2026-07-19, included per their go-ahead:
    "Proteine totale": (None,  489, "Protide totaux",           "PT",    "Protide totaux"),   # 18 vs 8 orders vs "Taux de protides"(508)
    "Albumine":        (None,  343, "Albuminémie",              "Alb",   "Albuminémie"),        # standalone exam, not the Albuminémie sub-param nested in Calcémie(366)
}

# Chemistry/immuno machines with no verified mappings yet.
# CyanVision: only QC/calibration ("Drift") messages captured so far, no
# real patient result - nothing to map until real patient data is seen.
CYANVISION_MAP = {}

MAPS = {
    "xn330": XN330_MAP,
    "ismart": ISMART_MAP,
    "selectra": SELECTRA_MAP,
    "cyanvision": CYANVISION_MAP,
}
