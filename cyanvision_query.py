#!/usr/bin/env python3
"""
CYAN CYANVISION query tool - read results from cyanvision.db
(captured by cyanvision_daemon.py).

Usage:
    python cyanvision_query.py           # interactive prompt
    python cyanvision_query.py <id>      # look up sample/control-id directly
    python cyanvision_query.py list      # list recent samples
"""

import sqlite3
import os
import sys

DB_PATH = os.path.join(os.path.dirname(os.path.abspath(__file__)), 'cyanvision.db')


def connect():
    if not os.path.exists(DB_PATH):
        print(f"No database found at {DB_PATH}")
        print("Start cyanvision_daemon.py first and let the analyzer send some results.")
        sys.exit(1)
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    return conn


def list_samples(db, limit=20):
    rows = db.execute(
        "SELECT sample_id, message_type, patient_name, received_at "
        "FROM samples ORDER BY received_at DESC LIMIT ?",
        (limit,),
    ).fetchall()
    if not rows:
        print("No samples captured yet.")
        return
    print(f"\n{'Sample/ID':<20}{'Type':<12}{'Patient':<25}{'Received'}")
    print("-" * 80)
    for r in rows:
        print(f"{r['sample_id']:<20}{r['message_type']:<12}"
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
        print(f"No data for '{sample_id}'. Type 'list' to see available entries.")
        return

    print(f"\n=== {sample_id} ===")
    if sample:
        print(f"Patient: {sample['patient_name'] or '(none)'}  "
              f"Type: {sample['message_type']}  "
              f"Received: {sample['received_at'][:19]}")
    print(f"{'Test':<15}{'Value':>10}  {'Unit':<10}{'Ref Range':<15}{'Flag'}")
    print("-" * 60)
    for r in results:
        print(f"{r['test']:<15}{r['value']:>10}  {r['unit']:<10}{r['ref_range']:<15}{r['flag'].strip()}")
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

    print("CYANVISION query tool. Type a sample/ID, 'list', or 'quit'.")
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
