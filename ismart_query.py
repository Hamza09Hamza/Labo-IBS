#!/usr/bin/env python3
"""
I-Smart 30 PRO query tool - read results from ismart.db (captured by ismart_daemon.py).

Usage:
    python ismart_query.py           # interactive prompt
    python ismart_query.py A250901055-S108   # look up sample directly
    python ismart_query.py list      # list recent samples
"""

import sqlite3
import os
import sys

DB_PATH = os.path.join(os.path.dirname(os.path.abspath(__file__)), 'ismart.db')


def connect():
    if not os.path.exists(DB_PATH):
        print(f"No database found at {DB_PATH}")
        print("Start ismart_daemon.py first and let the analyzer send some results.")
        sys.exit(1)
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    return conn


def list_samples(db, limit=20):
    rows = db.execute(
        "SELECT sample_id, specimen_type, patient_name, received_at "
        "FROM samples ORDER BY received_at DESC LIMIT ?",
        (limit,),
    ).fetchall()
    if not rows:
        print("No samples captured yet.")
        return
    print(f"\n{'Sample':<20}{'Specimen':<12}{'Patient':<25}{'Received'}")
    print("-" * 80)
    for r in rows:
        print(f"{r['sample_id']:<20}{r['specimen_type']:<12}"
              f"{r['patient_name']:<25}{r['received_at'][:19]}")
    print()


def show_sample(db, sample_id):
    sample = db.execute(
        "SELECT * FROM samples WHERE sample_id = ?", (sample_id,)
    ).fetchone()

    results = db.execute(
        "SELECT * FROM results WHERE sample_id = ? ORDER BY rowid", (sample_id,)
    ).fetchall()

    if not sample and not results:
        print(f"No data for sample '{sample_id}'. "
              f"Type 'list' to see available samples.")
        return

    print(f"\n=== Sample {sample_id} ===")
    if sample:
        print(f"Patient: {sample['patient_name'] or '(none)'}  "
              f"Specimen: {sample['specimen_type']}  "
              f"Received: {sample['received_at'][:19]}")
    print(f"{'Test':<15}{'Value':>10}  {'Unit':<10}{'Ref Range':<20}{'Flag'}")
    print("-" * 65)
    for r in results:
        flag = r['flag'].strip('^') if r['flag'] else ''
        flag_str = f"[{flag}]" if flag else ''
        print(f"{r['test']:<15}{r['value']:>10}  {r['unit']:<10}{r['ref_range']:<20}{flag_str}")
    print(f"\n{len(results)} results.\n")


def main():
    db = connect()
    args = sys.argv[1:]

    if args:
        arg = args[0]
        if arg.lower() == 'list':
            list_samples(db)
        else:
            show_sample(db, arg)
        db.close()
        return

    print("I-Smart 30 PRO query tool. Type a sample ID, 'list', or 'quit'.")
    try:
        while True:
            cmd = input("\nsample> ").strip()
            if not cmd:
                continue
            if cmd.lower() in ('quit', 'exit', 'q'):
                break
            if cmd.lower() == 'list':
                list_samples(db)
            else:
                show_sample(db, cmd)
    except (KeyboardInterrupt, EOFError):
        print()
    finally:
        db.close()


if __name__ == '__main__':
    main()
