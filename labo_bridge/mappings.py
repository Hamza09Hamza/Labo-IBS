"""
Curated machine-code -> clinic labo_param mapping.

These are the ONLY confident, human-verified matches. The value is the
labo_param.id in the clinic Postgres DB; abbrev/name are copied alongside for
readability and to catch DB drift. Any machine code NOT in these maps is
deliberately left unmatched (staged as 'pending') rather than guessed -
matching French free-text clinical params automatically is unsafe.

HOW THESE MAPPINGS ARE BUILT (standard method - use this for any new machine):
Do NOT scan the full labo_param table by name/abbreviation - it has 650 rows
with many duplicate names across unrelated panels (e.g. ~15 rows named
"Leucocytes" spanning blood/CSF/urine/parasitology). Instead, find the
specific exam's service_tarification_id first (e.g. via the frequency query
against clinic.appointment_tarification/service_tarification), then join to
get ONLY that exam's own scoped, unambiguous param list:

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
   breakdown at all (e.g. "Créatinémie", id 392) - its result is stored
   without a param_id (against appointment_tarification_id directly on
   labo_result). Nothing to map for machines here.
 - is_composed=true rows give the exact, disambiguated param list for that
   exam, in display order, often tagged with which analyzer/technique
   produces them (e.g. "(SYSMEX XS 500i)") - a strong cross-check that the
   machine sending results actually corresponds to that exam.

XN-330 CBC map: built from service_tarification_id=421 ("FNS", the highest-
volume exam, 6984 occurrences), all 15 rows tagged technique "SYSMEX XS
500i". Verified with the user on 2026-07-16. MCH->TGMH(99307) was initially
held out pending verification; the FNS join confirmed TGMH sits at
display_order 6 (between VGM/MCV and CCMH/MCHC, the standard CBC panel
position for MCH) under the same technique as the rest of the panel, so it
was added as a confident match on 2026-07-16.
Two FNS rows are deliberately NOT mapped to any XN-330 code: id 99002
"NUMERATION SANGUINE" (NS) and id 99147 "EQUILIBRE LEUCOCYTAIRE" - these are
section headers in this LIS, not individual measured values.

To add a mapping after verifying it against the DB, add a line here (or use
`python -m labo_bridge match map <machine> <code> <param_id>`).
"""

# All 13 XN-330 CBC codes below belong to the SAME exam: service_tarification
# id=421, name="FNS" (verified via the join in the docstring above - every
# param row came back tagged with this one service_tarification_id).
XN330_SERVICE_TARIFICATION_ID = 421
XN330_SERVICE_TARIFICATION_NAME = "FNS"

# code -> (labo_param.id, abbreviation, name)
XN330_MAP = {
    "WBC":   (81,    "GB",       "Globules Blancs"),
    "RBC":   (80,    "GR",       "Globules Rouges"),
    "HGB":   (98000, "hémoglob", "Hémoglobine"),
    "HCT":   (97000, "Hemcte.",  "Hématocrite"),
    "PLT":   (93,    "pettes",   "Plaquettes"),
    "MCV":   (99478, "VGM",      "VGM"),
    "MCH":   (99307, "TGMH",     "TGMH"),
    "MCHC":  (10100, "CCMH",     "CCMH"),
    "NEUT%": (99134, "Neu",      "Neutrophiles"),
    "LYMPH%":(99137, "Lympho",   "Lymphocytes"),
    "MONO%": (99136, "Mono",     "Monocytes"),
    "EO%":   (99950, "eosino",   "Eosinophiles"),
    "BASO%": (99138, "Baso",     "Basophiles"),
}

# Chemistry/immuno machines: no verified mappings yet. Real captures needed
# first (only CyanVision has data so far, and it's QC/calibration runs).
# When building these, use the service_tarification join method documented
# above - look up the relevant exam(s) (see the frequency query) rather than
# scanning labo_param directly.
# "selectra" is the chemistry analyzer's real machine name (it runs the
# ELITech software/LIS2-A protocol stack, but the machine itself is a Selectra).
ISMART_MAP = {}
SELECTRA_MAP = {}
CYANVISION_MAP = {}

MAPS = {
    "xn330": XN330_MAP,
    "ismart": ISMART_MAP,
    "selectra": SELECTRA_MAP,
    "cyanvision": CYANVISION_MAP,
}

# machine -> (service_tarification_id, service_tarification_name).
# Only meaningful when every code in that machine's MAP belongs to a single
# exam (true for XN-330/FNS). If a future machine's mapping spans multiple
# exams, don't force it in here - extend the per-code tuples in its MAP
# instead rather than assuming one exam per machine.
SERVICE_TARIFICATION = {
    "xn330": (XN330_SERVICE_TARIFICATION_ID, XN330_SERVICE_TARIFICATION_NAME),
}
