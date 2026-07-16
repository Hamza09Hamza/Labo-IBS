#!/usr/bin/env python3
"""
ELITech query tool - read results from elitech.db (captured by elitech_daemon.py).

Usage:
    python elitech_query.py           # interactive prompt
    python elitech_query.py 2607044203  # look up sample directly
    python elitech_query.py list      # list recent samples
"""

import sqlite3
import os
import sys

DB_PATH = os.path.join(os.path.dirname(os.path.abspath(__file__)), 'elitech.db')


def connect():
    if not os.path.exists(DB_PATH):
        print(f"No database found at {DB_PATH}")
        print("Start elitech_daemon.py first and let the analyzer send some results.")
        sys.exit(1)
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    return conn


def list_samples(db, limit=20):
    rows = db.execute(
        "SELECT sample_id, analyzer_model, patient_name, received_at "
        "FROM samples ORDER BY received_at DESC LIMIT ?",
        (limit,),
    ).fetchall()
    if not rows:
        print("No samples captured yet.")
        return
    print(f"\n{'Sample':<15}{'Analyzer':<12}{'Patient':<25}{'Received'}")
    print("-" * 75)
    for r in rows:
        print(f"{r['sample_id']:<15}{r['analyzer_model']:<12}"
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
        print(f"Patient: {sample['patient_name']}  "
              f"ID: {sample['patient_id']}  "
              f"Analyzer: {sample['analyzer_model']}  "
              f"Received: {sample['received_at'][:19]}")
    print(f"{'Test':<20}{'Value':>12}  {'Unit':<10}{'Flag'}")
    print("-" * 50)
    for r in results:
        flag = f"[{r['flag']}]" if r['flag'] else ''
        print(f"{r['test']:<20}{r['value']:>12}  {r['unit']:<10}{flag}")
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

    # Interactive loop
    print("ELITech query tool. Type a sample ID, 'list', or 'quit'.")
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
