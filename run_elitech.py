#!/usr/bin/env python3
"""Entry point for the ELITech chemistry analyzer. Thin shim over labo_bridge."""
from labo_bridge import server

if __name__ == "__main__":
    server.run("elitech")
