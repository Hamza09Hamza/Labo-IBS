"""
One-time (but safe to re-run) deploy script for a FRESH labo_bridge Postgres
database on a new server.

Run this ONCE right after pointing the app at a brand-new/empty database:
    python deploy_seed.py

What it does, in order:
  1. Creates the labo_bridge schema and all 5 tables if they don't already
     exist (samples, labo_bridge_results, pending_params, mappings,
     machine_config) - exact same structure as the current dev database.
  2. Seeds labo_bridge.mappings from mappings.py's MAPS dict - this is what
     makes the curated code->param matches visible/queryable directly in
     the database (pgAdmin, a coworker looking things up, disaster
     recovery) instead of only living in this Python file. mappings.py
     stays the actual source of truth the running app reads from -
     seeding this table does NOT change matching behavior, it only makes
     the same information durable/visible in the DB too.
  3. Seeds labo_bridge.machine_config with each machine's display settings
     (label, kind, protocol, port, color, photo, machine_id) - a snapshot
     of the current dev database's values as of 2026-07-21. Edit the
     MACHINE_CONFIG_SEED dict below if these change before you deploy.
  4. Seeds labo_bridge.pending_params with the currently-known backlog of
     unmapped test codes (PENDING_PARAMS_SEED, also a 2026-07-21 snapshot) -
     so whoever maps codes on the new server can start immediately on the
     SAME known codes, instead of waiting for each machine to resend them.

What this script deliberately does NOT touch:
  - labo_bridge.samples / labo_bridge_results - these are per-result runtime
    history and correctly start empty on a new deployment. Seeding fake
    sample/result data here would be wrong. pending_params IS seeded (see
    above) since it's a backlog of known CODES, not per-result history.

Safe to re-run: table creation uses IF NOT EXISTS, mappings/machine_config
seeding uses ON CONFLICT upserts (updates existing rows), pending_params
seeding uses ON CONFLICT DO NOTHING (never overwrites a row that's already
progressed - e.g. one a human already started reviewing on the target DB).
"""

import sys
import os

sys.path.insert(0, os.path.dirname(__file__))

from labo_bridge import pg, mappings as mappings_module


SCHEMA_SQL = """
CREATE SCHEMA IF NOT EXISTS labo_bridge;

CREATE TABLE IF NOT EXISTS labo_bridge.samples (
    id BIGINT GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    machine TEXT NOT NULL,
    sample_id TEXT NOT NULL,
    analyzer_model TEXT,
    patient_name TEXT,
    patient_id TEXT,
    source_ip TEXT,
    paillasse TEXT,
    received_at TIMESTAMP NOT NULL DEFAULT now(),
    UNIQUE (machine, sample_id)
);

CREATE TABLE IF NOT EXISTS labo_bridge.labo_bridge_results (
    id BIGINT GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    machine TEXT NOT NULL,
    sample_id TEXT NOT NULL,
    specimen_year INTEGER,
    specimen_month INTEGER,
    specimen_sequence INTEGER,
    paillasse TEXT,
    test_code TEXT NOT NULL,
    param_id BIGINT REFERENCES labo.labo_param(id),
    param_abbrev TEXT,
    param_name TEXT,
    result_value TEXT,
    unit TEXT,
    flag TEXT,
    received_at TIMESTAMP NOT NULL DEFAULT now(),
    service_tarification_id BIGINT REFERENCES clinic_management.service_tarification(id),
    service_tarification_name TEXT,
    paillasse_name TEXT,
    api_sent BOOLEAN NOT NULL DEFAULT FALSE,
    api_result_id INTEGER
);

CREATE TABLE IF NOT EXISTS labo_bridge.pending_params (
    id BIGINT GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    machine TEXT NOT NULL,
    test_code TEXT NOT NULL,
    test_name TEXT,
    example_value TEXT,
    example_unit TEXT,
    example_raw TEXT,
    seen_count INTEGER NOT NULL DEFAULT 1,
    first_seen_at TIMESTAMP NOT NULL DEFAULT now(),
    last_seen_at TIMESTAMP NOT NULL DEFAULT now(),
    UNIQUE (machine, test_code)
);

CREATE TABLE IF NOT EXISTS labo_bridge.mappings (
    id BIGINT GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    machine TEXT NOT NULL,
    test_code TEXT NOT NULL,
    param_id INTEGER,
    service_tarification_id INTEGER,
    service_tarification_name TEXT,
    abbrev TEXT,
    name TEXT,
    updated_at TIMESTAMP NOT NULL DEFAULT now(),
    UNIQUE (machine, test_code)
);

CREATE TABLE IF NOT EXISTS labo_bridge.machine_config (
    machine TEXT PRIMARY KEY,
    label TEXT NOT NULL,
    kind TEXT NOT NULL DEFAULT '',
    protocol TEXT NOT NULL DEFAULT '',
    port INTEGER NOT NULL,
    color TEXT NOT NULL DEFAULT '#0C8599',
    photo TEXT,
    photo_bg TEXT NOT NULL DEFAULT 'transparent',
    machine_id INTEGER,
    ip_address TEXT,
    updated_at TIMESTAMP NOT NULL DEFAULT now()
);
ALTER TABLE labo_bridge.machine_config ADD COLUMN IF NOT EXISTS ip_address TEXT;
"""

# Snapshot of machine_config as of 2026-07-21 (this dev database). Update
# this dict if label/kind/color/photo/machine_id change before deploying -
# it's a one-time seed, not kept in sync automatically like mappings.py is.
MACHINE_CONFIG_SEED = [
    # machine,      label,              kind,                            protocol,                port, color,     photo,                       machine_id
    ("xn330",      "Sysmex XN-330",     "Hematology Analyzer",           "ASTM E1394",             6001, "#7c0e73", "machines/xn330.png",       101),
    ("ismart",     "I-Smart 30 PRO",    "Electrolyte / ISE Analyzer",    "ASTM E1394-97",          6002, "#008f6b", "machines/ismart.png",      15),
    ("selectra",   "Selectra",          "Chemistry Analyzer",            "LIS2-A (ELITech)",       6003, "#F59E0B", "machines/selectra.png",    None),
    ("cyanvision", "CyanVision",        "Chemistry Analyzer",            "HL7 v2.3.1 / MLLP",      6004, "#EC4899", "machines/cyanvision.png",  None),
    ("xs500i",     "Sysmex XS-500i",    "Hematology Analyzer (primary)", "ASTM E1394 (via IPU)",   6005, "#bd0000", "machines/xs500i.png",      13),
    ("minividas",  "Mini VIDAS",        "Immuno Analyzer",               "MINIVIDAS",              6006, "#7C3AED", "machines/minividas.png",   None),
]


# Snapshot of labo_bridge.pending_params as of 2026-07-21 (this dev
# database) - the currently-known backlog of unmapped test codes across
# all machines. Seeding this on a fresh deploy means whoever manages
# mappings on the new server can start working through the SAME known
# codes immediately, instead of waiting for each machine to resend them.
# Re-run gen_pending_seed (see scratchpad) to refresh this if you want an
# updated snapshot before deploying later.
PENDING_PARAMS_SEED = [
    # machine, test_code, test_name, example_value, example_unit, example_raw, seen_count
    ('cyanvision', 'GPT', 'GPT', '16', 'U/L', 'OBX|1|NM|1921|GPT|16|U/L|3 - 32|||||||20260719154411||||||||', 3),
    ('ismart', 'Ca2+', 'Ca2+', '-', 'mmol/L', 'R|4|^^^Ca2+^M|-|mmol/L|1.15^1.33^Ref. Range|PD^^||R|||||', 3),
    ('selectra', 'CRP IP v3', 'CRP IP v3', 'REJECT', 'mg/l', 'R|1|^^^CRP3^CRP IP v3|REJECT|mg/l|^0.0^10.0|^#||F', 1),
    ('xn330', 'Atypical_Lympho?', 'Atypical Lympho', '200', '', 'R|35|^^^^Atypical_Lympho?|200|||A||N||sysmex||20260716122602', 3),
    ('xn330', 'BASO#', 'Basophils (absolute)', '0.04', '10^3/uL', 'R|18|^^^^BASO#^1|0.04|10*3/uL||N||N||sysmex||20260716122602', 3),
    ('xn330', 'BASO#_RESEARCH', 'BASO#_RESEARCH', '0.04', '10*3/uL', 'R|81|^^^^BASO#_RESEARCH^1|0.04|10*3/uL||N||N||sysmex||20260716122602', 4),
    ('xn330', 'BASO%_RESEARCH', 'BASO%_RESEARCH', '0.1', '%', 'R|76|^^^^BASO%_RESEARCH^1|0.1|%||N||N||sysmex||20260716122602', 4),
    ('xn330', 'Blasts/Abn_Lympho?', 'Blasts/Abn Lympho', '300', '', 'R|33|^^^^Blasts/Abn_Lympho?|300|||A||N||sysmex||20260716122602', 3),
    ('xn330', 'DIST_PLT', 'DIST_PLT', 'PNG&R&20260720&R&2026_07_16_12_26_2607046407_PLT.PNG', '', 'R|53|^^^^DIST_PLT|PNG&R&20260720&R&2026_07_16_12_26_2607046407_PLT.PNG|||N||N||sysmex||20260716122602', 3),
    ('xn330', 'DIST_PLT(NORMAL)', 'DIST_PLT(NORMAL)', 'PNG&R&20260720&R&2026_07_16_12_26_2607046407_PLT_NormalRange.PNG', '', 'R|55|^^^^DIST_PLT(NORMAL)|PNG&R&20260720&R&2026_07_16_12_26_2607046407_PLT_NormalRange.PNG|||N||N||sysmex||20260716122602', 3),
    ('xn330', 'DIST_RBC', 'DIST_RBC', 'PNG&R&20260720&R&2026_07_16_12_26_2607046407_RBC.PNG', '', 'R|52|^^^^DIST_RBC|PNG&R&20260720&R&2026_07_16_12_26_2607046407_RBC.PNG|||N||N||sysmex||20260716122602', 3),
    ('xn330', 'DIST_RBC(NORMAL)', 'DIST_RBC(NORMAL)', 'PNG&R&20260720&R&2026_07_16_12_26_2607046407_RBC_NormalRange.PNG', '', 'R|54|^^^^DIST_RBC(NORMAL)|PNG&R&20260720&R&2026_07_16_12_26_2607046407_RBC_NormalRange.PNG|||N||N||sysmex||20260716122602', 3),
    ('xn330', 'DLT-WBCD', 'DLT-WBCD', '1.00', '', 'R|119|^^^^DLT-WBCD^1|1.00|||N||N||sysmex||20260716122602', 4),
    ('xn330', 'EO#', 'Eosinophils (absolute)', '0.20', '10^3/uL', 'R|17|^^^^EO#^1|0.20|10*3/uL||W||N||sysmex||20260716122602', 4),
    ('xn330', 'EO#_RESEARCH', 'EO#_RESEARCH', '0.20', '10*3/uL', 'R|80|^^^^EO#_RESEARCH^1|0.20|10*3/uL||W||N||sysmex||20260716122602', 4),
    ('xn330', 'EO%_RESEARCH', 'EO%_RESEARCH', '0.7', '%', 'R|75|^^^^EO%_RESEARCH^1|0.7|%||W||N||sysmex||20260716122602', 4),
    ('xn330', 'Erythrocytosis', 'Erythrocytosis', '', '', 'R|29|^^^^Erythrocytosis||||A||N||sysmex||20260716140644', 2),
    ('xn330', 'Fragments?', 'Fragments', '0', '', 'R|41|^^^^Fragments?|0|||||N||sysmex||20260716122602', 3),
    ('xn330', 'HF-BF1#', 'HF-BF1#', '0.000', '10*3/uL', 'R|133|^^^^HF-BF1#^1|0.000|10*3/uL||N||N||sysmex||20260716122602', 4),
    ('xn330', 'HF-BF1%', 'HF-BF1%', '0.0', '/100WBC', 'R|140|^^^^HF-BF1%^1|0.0|/100WBC||N||N||sysmex||20260716122602', 4),
    ('xn330', 'HF-BF2#', 'HF-BF2#', '0.000', '10*3/uL', 'R|134|^^^^HF-BF2#^1|0.000|10*3/uL||N||N||sysmex||20260716122602', 4),
    ('xn330', 'HF-BF2%', 'HF-BF2%', '0.0', '/100WBC', 'R|141|^^^^HF-BF2%^1|0.0|/100WBC||N||N||sysmex||20260716122602', 4),
    ('xn330', 'HFLC#', 'HFLC#', '0.60', '10*3/uL', 'R|62|^^^^HFLC#^1|0.60|10*3/uL||W||N||sysmex||20260716122602', 4),
    ('xn330', 'HFLC%', 'HFLC%', '2.0', '%', 'R|63|^^^^HFLC%^1|2.0|%||W||N||sysmex||20260716122602', 4),
    ('xn330', 'HGB-BLANK', 'HGB-BLANK', '5484', '', 'R|105|^^^^HGB-BLANK^1|5484|||N||N||sysmex||20260716122602', 4),
    ('xn330', 'HGB-SAMPLE', 'HGB-SAMPLE', '6784', '', 'R|106|^^^^HGB-SAMPLE^1|6784|||N||N||sysmex||20260716122602', 4),
    ('xn330', 'HGB_Defect?', 'HGB Defect', '80', '', 'R|40|^^^^HGB_Defect?|80|||||N||sysmex||20260716122602', 3),
    ('xn330', 'HGB_NONSI', 'HGB_NONSI', '13.0', 'g/dL', 'R|122|^^^^HGB_NONSI^1|13.0|g/dL||N||N||sysmex||20260716122602', 4),
    ('xn330', 'HGB_NONSI2', 'HGB_NONSI2', '13.00', 'g/dL', 'R|127|^^^^HGB_NONSI2^1|13.00|g/dL||N||N||sysmex||20260716122602', 4),
    ('xn330', 'HGB_SI', 'HGB_SI', '8.1', 'mmol/L', 'R|123|^^^^HGB_SI^1|8.1|mmol/L||N||N||sysmex||20260716122602', 4),
    ('xn330', 'HGB_SI2', 'HGB_SI2', '8.07', 'mmol/L', 'R|124|^^^^HGB_SI2^1|8.07|mmol/L||N||N||sysmex||20260716122602', 4),
    ('xn330', 'IG#', 'Immature Granulocytes (absolute)', '5.98', '10^3/uL', 'R|20|^^^^IG#^1|5.98|10*3/uL||W||N||sysmex||20260716122602', 4),
    ('xn330', 'IG#_RESEARCH', 'IG#_RESEARCH', '5.98', '10*3/uL', 'R|86|^^^^IG#_RESEARCH^1|5.98|10*3/uL||W||N||sysmex||20260716122602', 4),
    ('xn330', 'IG%', 'Immature Granulocytes (%)', '20.4', '%', 'R|19|^^^^IG%^1|20.4|%||W||N||sysmex||20260716122602', 4),
    ('xn330', 'IG%_RESEARCH', 'IG%_RESEARCH', '20.4', '%', 'R|85|^^^^IG%_RESEARCH^1|20.4|%||W||N||sysmex||20260716122602', 4),
    ('xn330', 'IG_Present', 'IG_Present', '', '', 'R|32|^^^^IG_Present||||A||N||sysmex||20260716122602', 1),
    ('xn330', 'IRBC-WDF#', 'IRBC-WDF#', '0', '', 'R|118|^^^^IRBC-WDF#^1|0|||N||N||sysmex||20260716122602', 4),
    ('xn330', 'Iron_Deficiency?', 'Iron Deficiency', '80', '', 'R|39|^^^^Iron_Deficiency?|80|||||N||sysmex||20260716122602', 3),
    ('xn330', 'L-MCV', 'L-MCV', '0.0', 'fL', 'R|111|^^^^L-MCV^1|0.0|fL||N||N||sysmex||20260716122602', 4),
    ('xn330', 'L-RBC', 'L-RBC', '0.00', '10*6/uL', 'R|110|^^^^L-RBC^1|0.00|10*6/uL||N||N||sysmex||20260716122602', 4),
    ('xn330', 'Left_Shift?', 'Left Shift', '250', '', 'R|34|^^^^Left_Shift?|250|||A||N||sysmex||20260716122602', 3),
    ('xn330', 'Leukocytosis', 'Leukocytosis', '', '', 'R|31|^^^^Leukocytosis||||A||N||sysmex||20260716122602', 1),
    ('xn330', 'LY-BF1#', 'LY-BF1#', '0.000', '10*3/uL', 'R|128|^^^^LY-BF1#^1|0.000|10*3/uL||N||N||sysmex||20260716122602', 4),
    ('xn330', 'LY-BF1%', 'LY-BF1%', '0.0', '%', 'R|135|^^^^LY-BF1%^1|0.0|%||N||N||sysmex||20260716122602', 4),
    ('xn330', 'LY-BF2#', 'LY-BF2#', '3.798', '10*3/uL', 'R|129|^^^^LY-BF2#^1|3.798|10*3/uL||N||N||sysmex||20260716122602', 4),
    ('xn330', 'LY-BF2%', 'LY-BF2%', '12.9', '%', 'R|136|^^^^LY-BF2%^1|12.9|%||N||N||sysmex||20260716122602', 4),
    ('xn330', 'LY-WX', 'LY-WX', '577', '', 'R|99|^^^^LY-WX^1|577|||N||N||sysmex||20260716122602', 4),
    ('xn330', 'LY-WY', 'LY-WY', '863', '', 'R|100|^^^^LY-WY^1|863|||N||N||sysmex||20260716122602', 4),
    ('xn330', 'LY-WZ', 'LY-WZ', '483', '', 'R|101|^^^^LY-WZ^1|483|||N||N||sysmex||20260716122602', 4),
    ('xn330', 'LY-X', 'LY-X', '81.4', 'ch', 'R|90|^^^^LY-X^1|81.4|ch||N||N||sysmex||20260716122602', 4),
    ('xn330', 'LY-Y', 'LY-Y', '69.5', 'ch', 'R|91|^^^^LY-Y^1|69.5|ch||N||N||sysmex||20260716122602', 4),
    ('xn330', 'LY-Z', 'LY-Z', '60.1', 'ch', 'R|92|^^^^LY-Z^1|60.1|ch||N||N||sysmex||20260716122602', 4),
    ('xn330', 'LYMP#&E&', 'LYMP#&E&', '3.20', '10*3/uL', 'R|60|^^^^LYMP#&E&^1|3.20|10*3/uL||N||N||sysmex||20260716122602', 4),
    ('xn330', 'LYMP%&E&', 'LYMP%&E&', '10.9', '%', 'R|61|^^^^LYMP%&E&^1|10.9|%||N||N||sysmex||20260716122602', 4),
    ('xn330', 'LYMPH#', 'Lymphocytes (absolute)', '3.80', '10^3/uL', 'R|15|^^^^LYMPH#^1|3.80|10*3/uL||W||N||sysmex||20260716122602', 4),
    ('xn330', 'LYMPH#_RESEARCH', 'LYMPH#_RESEARCH', '3.80', '10*3/uL', 'R|77|^^^^LYMPH#_RESEARCH^1|3.80|10*3/uL||W||N||sysmex||20260716122602', 4),
    ('xn330', 'LYMPH%_RESEARCH', 'LYMPH%_RESEARCH', '12.9', '%', 'R|72|^^^^LYMPH%_RESEARCH^1|12.9|%||W||N||sysmex||20260716122602', 4),
    ('xn330', 'MACROR', 'Macrocytic RBC ratio', '3.9', '%', 'R|24|^^^^MACROR^1|3.9|%||N||N||sysmex||20260716122602', 4),
    ('xn330', 'MICROR', 'Microcytic RBC ratio', '2.7', '%', 'R|23|^^^^MICROR^1|2.7|%||N||N||sysmex||20260716122602', 4),
    ('xn330', 'MO-BF1#', 'MO-BF1#', '0.000', '10*3/uL', 'R|130|^^^^MO-BF1#^1|0.000|10*3/uL||N||N||sysmex||20260716122602', 4),
    ('xn330', 'MO-BF1%', 'MO-BF1%', '0.0', '%', 'R|137|^^^^MO-BF1%^1|0.0|%||N||N||sysmex||20260716122602', 4),
    ('xn330', 'MO-BF2#', 'MO-BF2#', '0.000', '10*3/uL', 'R|131|^^^^MO-BF2#^1|0.000|10*3/uL||N||N||sysmex||20260716122602', 4),
    ('xn330', 'MO-BF2%', 'MO-BF2%', '0.0', '%', 'R|138|^^^^MO-BF2%^1|0.0|%||N||N||sysmex||20260716122602', 4),
    ('xn330', 'MO-BF3#', 'MO-BF3#', '1.876', '10*3/uL', 'R|132|^^^^MO-BF3#^1|1.876|10*3/uL||N||N||sysmex||20260716122602', 4),
    ('xn330', 'MO-BF3%', 'MO-BF3%', '6.4', '%', 'R|139|^^^^MO-BF3%^1|6.4|%||N||N||sysmex||20260716122602', 4),
    ('xn330', 'MO-WX', 'MO-WX', '237', '', 'R|102|^^^^MO-WX^1|237|||N||N||sysmex||20260716122602', 4),
    ('xn330', 'MO-WY', 'MO-WY', '581', '', 'R|103|^^^^MO-WY^1|581|||N||N||sysmex||20260716122602', 4),
    ('xn330', 'MO-WZ', 'MO-WZ', '610', '', 'R|104|^^^^MO-WZ^1|610|||N||N||sysmex||20260716122602', 4),
    ('xn330', 'MO-X', 'MO-X', '118.1', 'ch', 'R|93|^^^^MO-X^1|118.1|ch||N||N||sysmex||20260716122602', 4),
    ('xn330', 'MO-Y', 'MO-Y', '98.0', 'ch', 'R|94|^^^^MO-Y^1|98.0|ch||N||N||sysmex||20260716122602', 4),
    ('xn330', 'MO-Z', 'MO-Z', '67.2', 'ch', 'R|95|^^^^MO-Z^1|67.2|ch||N||N||sysmex||20260716122602', 4),
    ('xn330', 'MONO#', 'Monocytes (absolute)', '1.88', '10^3/uL', 'R|16|^^^^MONO#^1|1.88|10*3/uL||W||N||sysmex||20260716122602', 4),
    ('xn330', 'MONO#_RESEARCH', 'MONO#_RESEARCH', '1.88', '10*3/uL', 'R|78|^^^^MONO#_RESEARCH^1|1.88|10*3/uL||W||N||sysmex||20260716122602', 4),
    ('xn330', 'MONO%_RESEARCH', 'MONO%_RESEARCH', '6.4', '%', 'R|73|^^^^MONO%_RESEARCH^1|6.4|%||W||N||sysmex||20260716122602', 4),
    ('xn330', 'Monocytosis', 'Monocytosis', '', '', 'R|30|^^^^Monocytosis||||A||N||sysmex||20260716122602', 1),
    ('xn330', 'MPV', 'Mean Platelet Volume', '10.2', 'fL', 'R|26|^^^^MPV^1|10.2|fL||N||N||sysmex||20260716122602', 4),
    ('xn330', 'NE-FSC', 'NE-FSC', '86.3', 'ch', 'R|89|^^^^NE-FSC^1|86.3|ch||W||N||sysmex||20260716122602', 4),
    ('xn330', 'NE-SFL', 'NE-SFL', '46.2', 'ch', 'R|88|^^^^NE-SFL^1|46.2|ch||W||N||sysmex||20260716122602', 4),
    ('xn330', 'NE-SSC', 'NE-SSC', '145.6', 'ch', 'R|87|^^^^NE-SSC^1|145.6|ch||W||N||sysmex||20260716122602', 4),
    ('xn330', 'NE-WX', 'NE-WX', '378', '', 'R|96|^^^^NE-WX^1|378|||N||N||sysmex||20260716122602', 4),
    ('xn330', 'NE-WY', 'NE-WY', '1430', '', 'R|97|^^^^NE-WY^1|1430|||N||N||sysmex||20260716122602', 4),
    ('xn330', 'NE-WZ', 'NE-WZ', '672', '', 'R|98|^^^^NE-WZ^1|672|||N||N||sysmex||20260716122602', 4),
    ('xn330', 'NEUT#', 'Neutrophils (absolute)', '23.43', '10^3/uL', 'R|14|^^^^NEUT#^1|23.43|10*3/uL||W||N||sysmex||20260716122602', 4),
    ('xn330', 'NEUT#&E&', 'NEUT#&E&', '17.45', '10*3/uL', 'R|58|^^^^NEUT#&E&^1|17.45|10*3/uL||W||N||sysmex||20260716122602', 4),
    ('xn330', 'NEUT#_RESEARCH', 'NEUT#_RESEARCH', '23.43', '10*3/uL', 'R|79|^^^^NEUT#_RESEARCH^1|23.43|10*3/uL||W||N||sysmex||20260716122602', 4),
    ('xn330', 'NEUT%&E&', 'NEUT%&E&', '59.5', '%', 'R|59|^^^^NEUT%&E&^1|59.5|%||W||N||sysmex||20260716122602', 4),
    ('xn330', 'NEUT%_RESEARCH', 'NEUT%_RESEARCH', '79.9', '%', 'R|74|^^^^NEUT%_RESEARCH^1|79.9|%||W||N||sysmex||20260716122602', 4),
    ('xn330', 'Neutrophilia', 'Neutrophilia', '', '', 'R|29|^^^^Neutrophilia||||A||N||sysmex||20260716122602', 1),
    ('xn330', 'NRBC#', 'NRBC#', '0.03', '10*3/uL', 'R|64|^^^^NRBC#^1|0.03|10*3/uL||N||N||sysmex||20260716122602', 4),
    ('xn330', 'NRBC%', 'NRBC%', '0.1', '/100WBC', 'R|65|^^^^NRBC%^1|0.1|/100WBC||N||N||sysmex||20260716122602', 4),
    ('xn330', 'NRBC?', 'NRBC', '0', '', 'R|36|^^^^NRBC?|0|||||N||sysmex||20260716122602', 3),
    ('xn330', 'P-LCR', 'Platelet Large Cell Ratio', '25.7', '%', 'R|27|^^^^P-LCR^1|25.7|%||N||N||sysmex||20260716122602', 4),
    ('xn330', 'P-LCR_RESEARCH', 'P-LCR_RESEARCH', '25.7', '%', 'R|83|^^^^P-LCR_RESEARCH^1|25.7|%||N||N||sysmex||20260716122602', 4),
    ('xn330', 'P-MFV', 'P-MFV', '8.5', 'fL', 'R|112|^^^^P-MFV^1|8.5|fL||N||N||sysmex||20260716122602', 4),
    ('xn330', 'PCT', 'Plateletcrit', '0.50', '%', 'R|28|^^^^PCT^1|0.50|%||H||N||sysmex||20260716122602', 4),
    ('xn330', 'PCT_RESEARCH', 'PCT_RESEARCH', '0.50', '%', 'R|84|^^^^PCT_RESEARCH^1|0.50|%||H||N||sysmex||20260716122602', 4),
    ('xn330', 'PDW', 'Platelet Distribution Width', '11.2', 'fL', 'R|25|^^^^PDW^1|11.2|fL||N||N||sysmex||20260716122602', 4),
    ('xn330', 'PDW_RESEARCH', 'PDW_RESEARCH', '11.2', 'fL', 'R|82|^^^^PDW_RESEARCH^1|11.2|fL||N||N||sysmex||20260716122602', 4),
    ('xn330', 'PLT-I', 'PLT-I', '486', '10*3/uL', 'R|68|^^^^PLT-I^1|486|10*3/uL||N||N||sysmex||20260716122602', 4),
    ('xn330', 'PLT_Clumps?', 'PLT Clumps', '0', '', 'R|42|^^^^PLT_Clumps?|0|||||N||sysmex||20260716122602', 3),
    ('xn330', 'Positive_Count', 'Positive_Count', '', '', 'R|45|^^^^Positive_Count||||A||N||sysmex||20260716122602', 3),
    ('xn330', 'Positive_Diff', 'Positive_Diff', '', '', 'R|43|^^^^Positive_Diff||||A||N||sysmex||20260716122602', 1),
    ('xn330', 'Positive_Morph', 'Positive_Morph', '', '', 'R|44|^^^^Positive_Morph||||A||N||sysmex||20260716122602', 1),
    ('xn330', 'R-MFV', 'R-MFV', '84.8', 'fL', 'R|107|^^^^R-MFV^1|84.8|fL||N||N||sysmex||20260716122602', 4),
    ('xn330', 'RBC_Agglutination?', 'RBC Agglutination', '70', '', 'R|37|^^^^RBC_Agglutination?|70|||||N||sysmex||20260716122602', 3),
    ('xn330', 'RDW-CV', 'Red cell Distribution Width (CV)', '13.6', '%', 'R|22|^^^^RDW-CV^1|13.6|%||N||N||sysmex||20260716122602', 4),
    ('xn330', 'RDW-CV_RESEARCH', 'RDW-CV_RESEARCH', '13.6', '%', 'R|67|^^^^RDW-CV_RESEARCH^1|13.6|%||N||N||sysmex||20260716122602', 4),
    ('xn330', 'RDW-SD', 'Red cell Distribution Width (SD)', '42.2', 'fL', 'R|21|^^^^RDW-SD^1|42.2|fL||N||N||sysmex||20260716122602', 4),
    ('xn330', 'RDW-SD_RESEARCH', 'RDW-SD_RESEARCH', '42.2', 'fL', 'R|66|^^^^RDW-SD_RESEARCH^1|42.2|fL||N||N||sysmex||20260716122602', 4),
    ('xn330', 'S-MCV', 'S-MCV', '0.0', 'fL', 'R|109|^^^^S-MCV^1|0.0|fL||N||N||sysmex||20260716122602', 4),
    ('xn330', 'S-RBC', 'S-RBC', '0.00', '10*6/uL', 'R|108|^^^^S-RBC^1|0.00|10*6/uL||N||N||sysmex||20260716122602', 4),
    ('xn330', 'SCAT_WDF', 'SCAT_WDF', 'PNG&R&20260720&R&2026_07_16_12_26_2607046407_WDF.PNG', '', 'R|46|^^^^SCAT_WDF|PNG&R&20260720&R&2026_07_16_12_26_2607046407_WDF.PNG|||N||N||sysmex||20260716122602', 3),
    ('xn330', 'SCAT_WDF-CBC', 'SCAT_WDF-CBC', 'PNG&R&20260720&R&2026_07_16_12_26_2607046407_WDF_CBC.PNG', '', 'R|47|^^^^SCAT_WDF-CBC|PNG&R&20260720&R&2026_07_16_12_26_2607046407_WDF_CBC.PNG|||N||N||sysmex||20260716122602', 3),
    ('xn330', 'SCAT_WDF-CBC(FSCW-FSC)', 'SCAT_WDF-CBC(FSCW-FSC)', 'PNG&R&20260720&R&2026_07_16_12_26_2607046407_WDF-CBC_FSCW-FSC.PNG', '', 'R|51|^^^^SCAT_WDF-CBC(FSCW-FSC)|PNG&R&20260720&R&2026_07_16_12_26_2607046407_WDF-CBC_FSCW-FSC.PNG|||N||N||sysmex||20260716122602', 3),
    ('xn330', 'SCAT_WDF(FSC-SFL)', 'SCAT_WDF(FSC-SFL)', 'PNG&R&20260720&R&2026_07_16_12_26_2607046407_WDF_FSC-SFL.PNG', '', 'R|49|^^^^SCAT_WDF(FSC-SFL)|PNG&R&20260720&R&2026_07_16_12_26_2607046407_WDF_FSC-SFL.PNG|||N||N||sysmex||20260716122602', 3),
    ('xn330', 'SCAT_WDF(FSCW-FSC)', 'SCAT_WDF(FSCW-FSC)', 'PNG&R&20260720&R&2026_07_16_12_26_2607046407_WDF_FSCW-FSC.PNG', '', 'R|50|^^^^SCAT_WDF(FSCW-FSC)|PNG&R&20260720&R&2026_07_16_12_26_2607046407_WDF_FSCW-FSC.PNG|||N||N||sysmex||20260716122602', 3),
    ('xn330', 'SCAT_WDF(SSC-FSC)', 'SCAT_WDF(SSC-FSC)', 'PNG&R&20260720&R&2026_07_16_12_26_2607046407_WDF_SSC-FSC.PNG', '', 'R|48|^^^^SCAT_WDF(SSC-FSC)|PNG&R&20260720&R&2026_07_16_12_26_2607046407_WDF_SSC-FSC.PNG|||N||N||sysmex||20260716122602', 3),
    ('xn330', 'TNC', 'TNC', '29.35', '10*3/uL', 'R|71|^^^^TNC^1|29.35|10*3/uL||N||N||sysmex||20260716122602', 4),
    ('xn330', 'TNC-C', 'TNC-C', '29.39', '10*3/uL', 'R|69|^^^^TNC-C^1|29.39|10*3/uL||N||N||sysmex||20260716122602', 4),
    ('xn330', 'TNC-D', 'TNC-D', '29.35', '10*3/uL', 'R|70|^^^^TNC-D^1|29.35|10*3/uL||N||N||sysmex||20260716122602', 4),
    ('xn330', 'TNC-D2', 'TNC-D2', '29.345', '10*3/uL', 'R|121|^^^^TNC-D2^1|29.345|10*3/uL||N||N||sysmex||20260716122602', 4),
    ('xn330', 'Turbidity/HGB_Interference?', 'Turbidity/HGB Interference', '90', '', 'R|38|^^^^Turbidity/HGB_Interference?|90|||||N||sysmex||20260716122602', 3),
    ('xn330', 'WBC-C', 'WBC-C', '29.39', '10*3/uL', 'R|56|^^^^WBC-C^1|29.39|10*3/uL||N||N||sysmex||20260716122602', 4),
    ('xn330', 'WBC-D', 'WBC-D', '29.35', '10*3/uL', 'R|57|^^^^WBC-D^1|29.35|10*3/uL||N||N||sysmex||20260716122602', 4),
    ('xn330', 'WBC-D2', 'WBC-D2', '29.345', '10*3/uL', 'R|120|^^^^WBC-D2^1|29.345|10*3/uL||N||N||sysmex||20260716122602', 4),
    ('xn330', 'WDF-WX', 'WDF-WX', '454', '', 'R|116|^^^^WDF-WX^1|454|||N||N||sysmex||20260716122602', 4),
    ('xn330', 'WDF-WY', 'WDF-WY', '1772', '', 'R|117|^^^^WDF-WY^1|1772|||N||N||sysmex||20260716122602', 4),
    ('xn330', 'WDF-X', 'WDF-X', '145.6', 'ch', 'R|113|^^^^WDF-X^1|145.6|ch||N||N||sysmex||20260716122602', 4),
    ('xn330', 'WDF-Y', 'WDF-Y', '46.2', 'ch', 'R|114|^^^^WDF-Y^1|46.2|ch||N||N||sysmex||20260716122602', 4),
    ('xn330', 'WDF-Z', 'WDF-Z', '86.3', 'ch', 'R|115|^^^^WDF-Z^1|86.3|ch||N||N||sysmex||20260716122602', 4),
    ('xn330', 'WDF_PLOT_COUNT', 'WDF_PLOT_COUNT', '23999', '', 'R|126|^^^^WDF_PLOT_COUNT^1|23999|||N||N||sysmex||20260716122602', 4),
    ('xn330', 'WDF_TOTAL_COUNT', 'WDF_TOTAL_COUNT', '28427', '', 'R|125|^^^^WDF_TOTAL_COUNT^1|28427|||N||N||sysmex||20260716122602', 4),
    ('xs500i', 'Abn_Lympho?', 'Abn Lympho', '10', '', 'R|30|^^^^Abn_Lympho?|10|||||||||20260721100958', 25),
    ('xs500i', 'Anemia', 'Anemia', '', '', 'R|26|^^^^Anemia||||A||||||20260720153957', 8),
    ('xs500i', 'Atypical_Lympho?', 'Atypical Lympho', '0', '', 'R|29|^^^^Atypical_Lympho?|0|||||||||20260721100958', 25),
    ('xs500i', 'BASO#', 'Basophils (absolute)', '0.05', '10^3/uL', 'R|18|^^^^BASO#^1|0.05|10*3/uL||W||||||20260721100958', 25),
    ('xs500i', 'Blasts?', 'Blasts', '0', '', 'R|25|^^^^Blasts?|0|||||||||20260721100958', 25),
    ('xs500i', 'DIST_PLT', 'DIST_PLT', 'PNG&R&20260719&R&2026_07_21_10_09_2607063907_PLT.PNG', '', 'R|40|^^^^DIST_PLT|PNG&R&20260719&R&2026_07_21_10_09_2607063907_PLT.PNG|||N||||||20260721100958', 25),
    ('xs500i', 'DIST_RBC', 'DIST_RBC', 'PNG&R&20260719&R&2026_07_21_10_09_2607063907_RBC.PNG', '', 'R|39|^^^^DIST_RBC|PNG&R&20260719&R&2026_07_21_10_09_2607063907_RBC.PNG|||N||||||20260721100958', 25),
    ('xs500i', 'EO#', 'Eosinophils (absolute)', '0.07', '10^3/uL', 'R|17|^^^^EO#^1|0.07|10*3/uL||W||||||20260721100958', 25),
    ('xs500i', 'Fragments?', 'Fragments', '0', '', 'R|35|^^^^Fragments?|0|||||||||20260721100958', 25),
    ('xs500i', 'HGB_Defect?', 'HGB Defect', '80', '', 'R|34|^^^^HGB_Defect?|80|||||||||20260721100958', 25),
    ('xs500i', 'Immature_Gran?', 'Immature Gran', '20', '', 'R|26|^^^^Immature_Gran?|20|||||||||20260721100958', 25),
    ('xs500i', 'Iron_Deficiency?', 'Iron Deficiency', '80', '', 'R|33|^^^^Iron_Deficiency?|80|||||||||20260721100958', 25),
    ('xs500i', 'Left_Shift?', 'Left Shift', '0', '', 'R|27|^^^^Left_Shift?|0|||||||||20260721100958', 25),
    ('xs500i', 'LYMPH#', 'Lymphocytes (absolute)', '2.19', '10^3/uL', 'R|15|^^^^LYMPH#^1|2.19|10*3/uL||W||||||20260721100958', 25),
    ('xs500i', 'Lymphopenia', 'Lymphopenia', '', '', 'R|25|^^^^Lymphopenia||||A||||||20260719162000', 3),
    ('xs500i', 'MONO#', 'Monocytes (absolute)', '0.37', '10^3/uL', 'R|16|^^^^MONO#^1|0.37|10*3/uL||W||||||20260721100958', 25),
    ('xs500i', 'Monocytosis', 'Monocytosis', '', '', 'R|27|^^^^Monocytosis||||A||||||20260720075407', 5),
    ('xs500i', 'MPV', 'Mean Platelet Volume', '9.9', 'fL', 'R|22|^^^^MPV^1|9.9|fL||N||||||20260721100958', 25),
    ('xs500i', 'NEUT#', 'Neutrophils (absolute)', '3.87', '10^3/uL', 'R|14|^^^^NEUT#^1|3.87|10*3/uL||W||||||20260721100958', 25),
    ('xs500i', 'Neutrophilia', 'Neutrophilia', '', '', 'R|26|^^^^Neutrophilia||||A||||||20260720075407', 5),
    ('xs500i', 'NRBC?', 'NRBC', '170', '', 'R|28|^^^^NRBC?|170|||A||||||20260721100958', 25),
    ('xs500i', 'P-LCR', 'Platelet Large Cell Ratio', '25.2', '%', 'R|23|^^^^P-LCR^1|25.2|%||N||||||20260721100958', 25),
    ('xs500i', 'PCT', 'Plateletcrit', '0.30', '%', 'R|24|^^^^PCT^1|0.30|%||N||||||20260721100958', 25),
    ('xs500i', 'PDW', 'Platelet Distribution Width', '12.5', 'fL', 'R|21|^^^^PDW^1|12.5|fL||N||||||20260721100958', 25),
    ('xs500i', 'PLT_Abn_Distribution', 'PLT_Abn_Distribution', '', '', 'R|27|^^^^PLT_Abn_Distribution||||A||||||20260720153957', 1),
    ('xs500i', 'PLT_Clumps?', 'PLT Clumps', '0', '', 'R|36|^^^^PLT_Clumps?|0|||||||||20260721100958', 25),
    ('xs500i', 'Positive_Count', 'Positive_Count', '', '', 'R|41|^^^^Positive_Count||||A||||||20260720153957', 8),
    ('xs500i', 'Positive_Diff', 'Positive_Diff', '', '', 'R|41|^^^^Positive_Diff||||A||||||20260720075407', 8),
    ('xs500i', 'Positive_Morph', 'Positive_Morph', '', '', 'R|37|^^^^Positive_Morph||||A||||||20260721100958', 13),
    ('xs500i', 'RBC_Agglutination?', 'RBC Agglutination', '70', '', 'R|31|^^^^RBC_Agglutination?|70|||||||||20260721100958', 25),
    ('xs500i', 'RDW-CV', 'Red cell Distribution Width (CV)', '14.0', '%', 'R|20|^^^^RDW-CV^1|14.0|%||N||||||20260721100958', 25),
    ('xs500i', 'RDW-SD', 'Red cell Distribution Width (SD)', '45.1', 'fL', 'R|19|^^^^RDW-SD^1|45.1|fL||N||||||20260721100958', 25),
    ('xs500i', 'SCAT_DIFF', 'SCAT_DIFF', 'PNG&R&20260719&R&2026_07_21_10_09_2607063907_DIFF.PNG', '', 'R|38|^^^^SCAT_DIFF|PNG&R&20260719&R&2026_07_21_10_09_2607063907_DIFF.PNG|||N||||||20260721100958', 25),
    ('xs500i', 'Turbidity/HGB_Interference?', 'Turbidity/HGB Interference', '80', '', 'R|32|^^^^Turbidity/HGB_Interference?|80|||||||||20260721100958', 25),
    ('xs500i', 'WBC_Abn_Scattergram', 'WBC_Abn_Scattergram', '', '', 'R|25|^^^^WBC_Abn_Scattergram||||A||||||20260720153957', 7),
]


def create_schema(conn):
    with conn.cursor() as cur:
        cur.execute(SCHEMA_SQL)
    print("[deploy_seed] schema + tables ensured (created if missing, left alone if already present)")


def seed_mappings(conn):
    count = 0
    with conn.cursor() as cur:
        for machine, machine_map in mappings_module.MAPS.items():
            for test_code, (param_id, st_id, st_name, abbrev, name) in machine_map.items():
                cur.execute(
                    """
                    INSERT INTO labo_bridge.mappings
                        (machine, test_code, param_id, service_tarification_id,
                         service_tarification_name, abbrev, name)
                    VALUES (%s,%s,%s,%s,%s,%s,%s)
                    ON CONFLICT (machine, test_code) DO UPDATE SET
                        param_id = EXCLUDED.param_id,
                        service_tarification_id = EXCLUDED.service_tarification_id,
                        service_tarification_name = EXCLUDED.service_tarification_name,
                        abbrev = EXCLUDED.abbrev,
                        name = EXCLUDED.name,
                        updated_at = now()
                    """,
                    (machine, test_code, param_id, st_id, st_name, abbrev, name),
                )
                count += 1
    print(f"[deploy_seed] labo_bridge.mappings seeded from mappings.py ({count} entries across "
          f"{len(mappings_module.MAPS)} machines)")


def clear_stale_pending(conn):
    """
    Delete any pending_params row whose (machine, test_code) already has a
    curated mapping in mappings.py. Exists because a code can be seen and
    written to pending BEFORE its mapping is added (or before a running
    service restarts to pick up a just-added mapping) - the UI's own
    "add mapping" flow clears its own pending row when that happens, but
    that only covers mappings added through the UI. Mappings added by
    editing mappings.py directly and deploying (git pull + this script)
    never went through that path, so their old pending rows were only
    ever cleared by hand (found repeatedly: cyanvision/GPT twice,
    minividas/FER once, 2026-07-22/23) - this makes every deploy self-heal
    that class of staleness instead of relying on someone noticing it in
    the UI and asking about it.
    """
    with conn.cursor() as cur:
        cur.execute("SELECT machine, test_code FROM labo_bridge.pending_params")
        pending = cur.fetchall()
        stale = [(m, c) for m, c in pending if c in mappings_module.MAPS.get(m, {})]
        for machine, test_code in stale:
            cur.execute(
                "DELETE FROM labo_bridge.pending_params WHERE machine = %s AND test_code = %s",
                (machine, test_code),
            )
    print(f"[deploy_seed] cleared {len(stale)} stale pending_params row(s) "
          f"already covered by a curated mapping")


def seed_machine_config(conn):
    with conn.cursor() as cur:
        for machine, label, kind, protocol, port, color, photo, machine_id in MACHINE_CONFIG_SEED:
            cur.execute(
                """
                INSERT INTO labo_bridge.machine_config
                    (machine, label, kind, protocol, port, color, photo, photo_bg, machine_id)
                VALUES (%s,%s,%s,%s,%s,%s,%s,'transparent',%s)
                ON CONFLICT (machine) DO UPDATE SET
                    label = EXCLUDED.label, kind = EXCLUDED.kind,
                    protocol = EXCLUDED.protocol, port = EXCLUDED.port,
                    color = EXCLUDED.color, photo = EXCLUDED.photo,
                    machine_id = EXCLUDED.machine_id, updated_at = now()
                """,
                (machine, label, kind, protocol, port, color, photo, machine_id),
            )
    print(f"[deploy_seed] labo_bridge.machine_config seeded ({len(MACHINE_CONFIG_SEED)} machines)")


def seed_pending_params(conn):
    count = 0
    with conn.cursor() as cur:
        for machine, test_code, test_name, example_value, example_unit, example_raw, seen_count in PENDING_PARAMS_SEED:
            cur.execute(
                """
                INSERT INTO labo_bridge.pending_params
                    (machine, test_code, test_name, example_value, example_unit,
                     example_raw, seen_count)
                VALUES (%s,%s,%s,%s,%s,%s,%s)
                ON CONFLICT (machine, test_code) DO NOTHING
                """,
                (machine, test_code, test_name, example_value, example_unit, example_raw, seen_count),
            )
            count += 1
    print(f"[deploy_seed] labo_bridge.pending_params seeded ({count} known unmapped codes carried over)")


def main():
    conn = pg._get_conn()
    if conn is None:
        print("[deploy_seed] ERROR: could not connect to Postgres - check labo_bridge/config.py's "
              "connection settings before running this on the server.")
        sys.exit(1)

    create_schema(conn)
    seed_mappings(conn)
    seed_machine_config(conn)
    seed_pending_params(conn)
    clear_stale_pending(conn)
    print("[deploy_seed] done.")


if __name__ == "__main__":
    main()
