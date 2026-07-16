"""
Curated machine-code -> clinic labo_param mapping.

These are the ONLY confident, human-verified matches. The value is the
labo_param.id in the clinic Postgres DB; abbrev/name are copied alongside for
readability and to catch DB drift. Any machine code NOT in these maps is
deliberately left unmatched (staged as 'pending') rather than guessed -
matching French free-text clinical params automatically is unsafe.

XN-330 CBC map verified with the user on 2026-07-16. MCH->TGMH(99307) was
intentionally HELD OUT (spelling/identity uncertainty) and stays pending.

To add a mapping after verifying it against the DB, add a line here (or use
`python -m labo_bridge match map <machine> <code> <param_id>`).
"""

# code -> (labo_param.id, abbreviation, name)
XN330_MAP = {
    "WBC":   (81,    "GB",       "Globules Blancs"),
    "RBC":   (80,    "GR",       "Globules Rouges"),
    "HGB":   (98000, "hémoglob", "Hémoglobine"),
    "HCT":   (97000, "Hemcte.",  "Hématocrite"),
    "PLT":   (93,    "pettes",   "Plaquettes"),
    "MCV":   (99478, "VGM",      "VGM"),
    "MCHC":  (10100, "CCMH",     "CCMH"),
    "NEUT%": (99134, "Neu",      "Neutrophiles"),
    "LYMPH%":(99137, "Lympho",   "Lymphocytes"),
    "MONO%": (99136, "Mono",     "Monocytes"),
    "EO%":   (99950, "eosino",   "Eosinophiles"),
    "BASO%": (99138, "Baso",     "Basophiles"),
}

# Chemistry/immuno machines: no verified mappings yet. Real captures needed
# first (only CyanVision has data so far, and it's QC/calibration runs).
ISMART_MAP = {}
ELITECH_MAP = {}
CYANVISION_MAP = {}

MAPS = {
    "xn330": XN330_MAP,
    "ismart": ISMART_MAP,
    "elitech": ELITECH_MAP,
    "cyanvision": CYANVISION_MAP,
}
