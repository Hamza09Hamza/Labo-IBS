#!/usr/bin/env python3
"""Entry point for the I-Smart 30 PRO. Thin shim over labo_bridge."""
from labo_bridge import server

if __name__ == "__main__":
    server.run("ismart")
