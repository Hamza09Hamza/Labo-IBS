#!/usr/bin/env python3
"""Entry point for CyanVision (HL7/MLLP). Thin shim over labo_bridge."""
from labo_bridge import server

if __name__ == "__main__":
    server.run("cyanvision")
