#!/usr/bin/env python3
"""
Start listeners for all four analyzers at once, each on its own port, in a
single process. This is the normal way to run the lab bridge in production -
every machine stays connected to this same server simultaneously.

Ports (see labo_bridge/server.py MACHINES for the source of truth):
    xn330      -> 6001
    ismart     -> 6002
    selectra   -> 6003  (chemistry analyzer; runs the ELITech/LIS2-A software)
    cyanvision -> 6004

Every line printed is prefixed with the machine name, and every result
actually written to the local database (labo_bridge.db) is printed alongside
whether it was matched to a clinic labo_param or left pending for review.
"""
from labo_bridge import server

if __name__ == "__main__":
    server.run_all()
