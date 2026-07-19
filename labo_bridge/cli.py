"""
Command-line interface for labo_bridge.

  python -m labo_bridge run all [--quiet]        # every analyzer, one process, one port each
  python -m labo_bridge run <machine> [--quiet]  # just one analyzer
  python -m labo_bridge query [list | <sample_id>]
  python -m labo_bridge match [list | pending | map <machine> <code> <param_id>
                              | approve <id> | reject <id>]
"""

import sys

from . import server, storage, mappings


def cmd_run(args):
    if not args:
        print("usage: run all | run <machine> [--quiet]")
        print(f"machines: {sorted(server.MACHINES)}")
        return 1
    target = args[0]
    quiet = "--quiet" in args[1:]
    if target == "all":
        server.run_all(quiet=quiet)
    else:
        server.run(target, quiet=quiet)
    return 0


def cmd_query(args):
    db = storage.connect()
    if args and args[0] != "list":
        sid = args[0]
        sample = db.execute("SELECT * FROM samples WHERE sample_id=?", (sid,)).fetchone()
        rows = db.execute("SELECT * FROM results WHERE sample_id=? ORDER BY rowid",
                          (sid,)).fetchall()
        if not sample and not rows:
            print(f"No data for sample '{sid}'. Try: query list")
            return 0
        print(f"\n=== Sample {sid} ===")
        if sample:
            print(f"Machine: {sample['machine']}  Patient: {sample['patient_name'] or '(none)'}"
                  f"  Received: {sample['received_at'][:19]}")
        print(f"{'Test':<12}{'Value':>10}  {'Unit':<8}{'Flag'}")
        print("-" * 40)
        for r in rows:
            print(f"{r['test_code']:<12}{r['value']:>10}  {r['unit']:<8}{r['flag']}")
        print(f"\n{len(rows)} results.\n")
    else:
        rows = db.execute("SELECT machine, sample_id, patient_name, received_at "
                          "FROM samples ORDER BY received_at DESC LIMIT 30").fetchall()
        if not rows:
            print("No samples captured yet.")
            return 0
        print(f"\n{'Machine':<12}{'Sample':<16}{'Patient':<22}{'Received'}")
        print("-" * 70)
        for r in rows:
            print(f"{r['machine']:<12}{r['sample_id']:<16}"
                  f"{(r['patient_name'] or ''):<22}{r['received_at'][:19]}")
        print()
    db.close()
    return 0


def cmd_match(args):
    db = storage.connect()
    sub = args[0] if args else "list"

    if sub == "map" and len(args) >= 4:
        machine, code, param_id = args[1], args[2], int(args[3])
        print(f"To make this permanent, add to labo_bridge/mappings.py:")
        print(f'    "{code}": ({param_id}, <service_tarification_id>, '
              f'"<service_tarification_name>", "<abbrev>", "<name>"),   # in {machine.upper()}_MAP')
        # Re-stage any pending rows for this code so they pick up the mapping.
        db.execute("UPDATE result_matches SET matched_param_id=?, match_method='curated', "
                   "status='matched' WHERE machine=? AND test_code=? AND status='pending'",
                   (param_id, machine, code))
        db.commit()
        print(f"Updated existing pending rows for {machine}/{code} -> param {param_id}.")
    elif sub in ("approve", "reject") and len(args) >= 2:
        new_status = "approved" if sub == "approve" else "rejected"
        db.execute("UPDATE result_matches SET status=? WHERE id=?",
                   (new_status, int(args[1])))
        db.commit()
        print(f"Match {args[1]} -> {new_status}.")
    else:
        only_pending = (sub == "pending")
        where = "WHERE status='pending'" if only_pending else ""
        rows = db.execute(f"SELECT * FROM result_matches {where} "
                          "ORDER BY created_at DESC LIMIT 50").fetchall()
        if not rows:
            print("No staged matches." if not only_pending else "No pending matches.")
            return 0
        print(f"\n{'ID':<5}{'Machine':<11}{'Code':<10}{'Value':>8}  "
              f"{'ParamID':<9}{'DB name':<20}{'Status'}")
        print("-" * 78)
        for r in rows:
            print(f"{r['id']:<5}{r['machine']:<11}{r['test_code']:<10}"
                  f"{r['value']:>8}  {str(r['matched_param_id'] or '-'):<9}"
                  f"{(r['matched_name'] or '-'):<20}{r['status']}")
        print()
    db.close()
    return 0


def main(argv=None):
    argv = argv if argv is not None else sys.argv[1:]
    if not argv:
        print(__doc__)
        return 1
    cmd, rest = argv[0], argv[1:]
    handlers = {"run": cmd_run, "query": cmd_query, "match": cmd_match}
    if cmd not in handlers:
        print(__doc__)
        return 1
    return handlers[cmd](rest)


if __name__ == "__main__":
    sys.exit(main())
