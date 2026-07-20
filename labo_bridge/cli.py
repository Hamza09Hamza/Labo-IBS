"""
Command-line interface for labo_bridge.

  python -m labo_bridge run all [--quiet]        # every analyzer, one process, one port each
  python -m labo_bridge run <machine> [--quiet]  # just one analyzer

For browsing captured samples/results/mappings, use the admin web UI instead
(python -m labo_bridge.admin, or run_all.py which starts both together) -
it reads/writes the same Postgres tables this querying used to read from
local SQLite (retired).
"""

import sys

from . import server


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


def main(argv=None):
    argv = argv if argv is not None else sys.argv[1:]
    if not argv:
        print(__doc__)
        return 1
    cmd, rest = argv[0], argv[1:]
    handlers = {"run": cmd_run}
    if cmd not in handlers:
        print(__doc__)
        return 1
    return handlers[cmd](rest)


if __name__ == "__main__":
    sys.exit(main())
