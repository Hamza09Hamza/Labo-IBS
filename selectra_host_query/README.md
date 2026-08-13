# Selectra Host Query Bench

This is a small staging and trace page for one workflow only:

1. Stage patient demographics, an exact sample ID, and requested tests in the web page.
2. Have the Selectra send a Host Query for that ID.
3. Arm exact-ID replies from the page and inspect the real query/order response in the live protocol trace.

It uses its own SQLite file. It does **not** read or write the clinic database, publish results, or import the production Labo Bridge server.

## Normal production-server test

`run_all.py` starts this page automatically alongside the bridge:

- Web page: `http://<server-IP>:5052/`
- Selectra connection: the existing `<server-IP>:6003`
- Staging database: `selectra_host_query/data/host_query.db`

The page starts **disarmed after every service restart**. Stage an order, verify
the H/P/O/L preview, click **Arm exact-ID replies**, and only then enter or scan
that exact sample ID on the Selectra. Unknown IDs receive no order data.

## Continuous remote proof

When nobody is beside the analyzer to coordinate an exact sample ID, the page
also has an explicitly armed **continuous wildcard probe**. It is deliberately
separate from normal exact-ID replies:

1. Open `http://<server-IP>:5052/` and click **Arm continuous probe**.
2. Confirm that the page shows the `APPELLE MANEL/FODHIL` order notice and three
   randomly chosen, installed Selectra test abbreviations.
3. Every valid Selectra `Q` record, for any 1–12 character sample ID, receives
   that order notice and those three tests while the probe remains armed.
4. Click **Disarm continuous probe** to stop it. Restarting LaboBridge also
   returns it to the disarmed state.
5. Check the trace for `continuous_probe_triggered`, `frame_sent`,
   `transport_acknowledged`, and `continuous_probe_transport_acknowledged` for
   each query. A later `application_rejected` event means the Selectra returned
   `O-26=X` and did not accept the request.

The probe sends the required minimal `P|1` patient record so it cannot overwrite
or conflict with demographics already held by the analyzer. The 20-character
alert is placed in documented field O-17 (Ordering Physician), the tests are
appended with action `A`, and the three selected tests remain visible in the
page before the query arrives.

**Safety:** each query could belong to a loaded sample. Returning an order can
attach or start the selected tests on every queried sample. Arm this only for
the controlled diagnostic window and disarm it as soon as the test is over.

## Wire format used for replies

The Selectra's captured Host Query places `H`, `Q`, and `L` records together in
one ASTM frame. The host mirrors that structure: one `ENQ`, one complete ASTM
frame containing `H<CR>P<CR>O<CR>L`, one analyzer `ACK`, then `EOT`. This avoids
the previous behavior where four separately acknowledged frames were still four
incomplete application messages and were ignored by the analyzer.

Outbound order records identify `WINLAB` as the host and `PROM` as the analyzer,
use the analyzer's captured case-sensitive installed method abbreviations, and
terminate with `L|1|F`. Normal staged orders use action `N`; the wildcard probe
uses action `A`, sample type `Normal`, the order notice in O-17, and report type
`Q`.

An ASTM frame `ACK` proves only transport acceptance. The page records it as
`transport_acknowledged`, not as application delivery. The analyzer may later
return an O record with report type `X`; the bridge detects this as an explicit
application rejection and marks that order `rejected`.

## Standalone isolated listener

```bash
.venv/bin/python -u run_selectra_host_query.py
```

- Web page: `http://127.0.0.1:5052/`
- Selectra test endpoint: `<this-computer's-LAN-IP>:6103`
- Database: `selectra_host_query/data/host_query.db`

The default is **observation mode**. It accepts and acknowledges analyzer messages and shows them in the trace, but it will not transmit H/P/O/L order records to the analyzer. The “Simulate exact-ID query” button exercises the same matching and record-building code without network transmission.

## Bench procedure

1. Stage a clearly non-production sample such as `HQ-DEMO-001` in the page.
2. Add the exact Selectra assay/order codes to request.
3. First click **Simulate exact-ID query** and inspect the generated H/P/O/L records.
4. For standalone testing only, configure a bench Selectra Host Query/LIS connection to the computer's LAN IP and TCP port `6103`. When using `run_all.py`, leave the Selectra on its existing production listener port `6003`.
5. Scan or type exactly `HQ-DEMO-001` on the analyzer in the action that triggers Host Query.
6. Confirm that a `Q` record appears in the protocol trace and matches the staged sample ID exactly.

An unknown or ambiguous ID deliberately receives no order response.

## Enable a real response only after capture validation

Stop the observation process, then restart with:

```bash
.venv/bin/python -u run_selectra_host_query.py --arm-live-responses
```

This allows an exact matched query to receive H/P/O/L frames. Use it only on a bench/non-clinical analyzer session after validating the observed `Q` record and the required order record fields against the host-interface documentation for the exact Selectra model and software version.

The assay names shown by the page are suggestions recovered from existing result mappings. Result names are not guaranteed to be valid outbound order codes; validate them on the instrument before enabling live responses.

## Ports and options

```text
--web-port 5052           Browser UI
--instrument-port 6103    TCP endpoint configured as the analyzer's host/LIS
--data PATH               Isolated staging database
--arm-live-responses      Allow real order downloads after an exact query
```

The analyzer normally connects outward to the host endpoint above. This is separate from an instrument's other fixed protocol ports and from the local source-port setting used by some device integrations.
