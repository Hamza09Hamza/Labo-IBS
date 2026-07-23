# Mini VIDAS parameter mapping status (as of 2026-07-23)

35 of the machine's parameter codes are mapped to the clinic database and will
flow through to the clinic API automatically. Everything below is NOT yet
mapped, split into two reasons.

## Not mapped — no matching exam exists in the clinic database at all

These would need a new exam/parameter created in the clinic system before they
could ever be mapped.

| Code  | Parameter               |
|-------|--------------------------|
| T3    | T3 (total)               |
| T4    | T4 (total)               |
| STA   | Stallertest              |
| ST    | Stallergy                |
| STO   | Stallertroph             |
| TES   | Testosterone             |
| CEAS  | CEA S                    |
| DD2   | D-Dimer Exclusion        |
| vWF   | vWF                      |
| PC    | Protein C                |
| CKMB  | CK-MB                    |
| DIG   | Digoxin                  |
| B2M   | β2 Microglobulin         |
| RBG   | Rubella IgG II           |
| RBM   | Rubella IgM              |
| VCAM  | EBV VCA IgM              |
| VCAG  | EBV VCA/EA IgG           |
| EBNA  | EBV EBNA IgG             |
| HPY   | H. Pylori IgG            |
| HBCM  | Anti-HBc IgM II          |
| HBCT  | Anti HBc Total II        |
| HBET  | Anti-HBe                 |
| CDAB  | C. difficile Toxin A/B   |
| TXC   | Toxo Competition         |
| TXGA  | Toxo IgG Avidity         |

## Not mapped — a clinic exam exists, but it's ambiguous

A row exists in the clinic database, but either there are duplicate rows we
can't tell apart, or one clinic exam would have to serve multiple distinct
VIDAS test methodologies. Needs a human decision, not a guess.

| Code  | Parameter          | Why it's held back                                                   |
|-------|--------------------|------------------------------------------------------------------------|
| MYO   | Myoglobin          | Two duplicate "Myoglobine" rows exist in different specialities — unclear which is the active one |
| TNIU  | Troponin I Ultra   | Clinic DB only has "Troponine"/"Troponine T hs" — Troponin I is a different protein than Troponin T |
| P24   | HIV P24 II         | Only one generic "HIV" row exists for 3 different VIDAS HIV test methods |
| HIV6  | HIV DUO Quick      | same as above |
| HIV5  | HIV DUO Ultra      | same as above |
| HAVM  | HAV IgM            | Two duplicate "HAV (IgM)" rows exist in different specialities |
| HAVT  | Anti-HAV Total     | same duplicate issue |

## Mapped (35 codes)

TSH, TSH3, FT3, FT4N, IgE, HCG, LH, FSH, PRL, E2II, AFP, 125 (CA 125), 199 (CA
19-9), 153 (CA 15-3), TPSA, FPSA, PBNP, FER, CORS, TXG (Toxo IgG), TXM (Toxo
IgM), CMVG, CMVM, CMVU, LYT, MSG, MPG, VZG, HBS, HBST, HBE, CHL, PCT, HCV, HIV
