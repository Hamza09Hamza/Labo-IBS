#!/usr/bin/env bash
set -euo pipefail

# Full TCP/UDP capture for macOS - the same purpose as
# capture_analyzer_windows.ps1's -AllTcpUdp mode, but for a Mac instead of
# the Windows bridge server. tcpdump sees every protocol on every port on
# the chosen interface, so nothing needs to be bound like
# capture_listener.py's per-port listeners do - useful when an analyzer's
# port/protocol isn't known yet, or to confirm whether traffic is reaching
# this machine at all.
#
# macOS's tcpdump has no Linux-style "any" pseudo-interface (confirmed via
# `tcpdump -D` on this machine - only real interfaces are listed), so a
# real interface must be picked; this defaults to the one that currently
# holds an IPv4 address rather than guessing en0.
#
# Usage:
#   ./capture_analyzer_macos.sh                        # capture everything, auto-picked interface
#   ./capture_analyzer_macos.sh 172.16.2.254            # only traffic to/from this IP
#   ./capture_analyzer_macos.sh 172.16.2.254 en0        # ...on a specific interface
#
# Needs sudo - tcpdump requires raw socket access on macOS.
#
# Produces, under captures/macos_<timestamp>/:
#   capture.pcap          - Wireshark-readable, the authoritative capture
#   capture_readable.txt  - hex+ASCII dump for reading without Wireshark

TARGET_IP="${1:-}"
INTERFACE="${2:-}"

if [[ -z "$INTERFACE" ]]; then
    # Pick the first interface that currently has an IPv4 address, since an
    # idle/unconfigured interface (e.g. a disconnected Ethernet adapter)
    # would silently capture nothing.
    for candidate in $(ifconfig -l); do
        if ipconfig getifaddr "$candidate" >/dev/null 2>&1; then
            INTERFACE="$candidate"
            break
        fi
    done
    if [[ -z "$INTERFACE" ]]; then
        echo "No interface with an IPv4 address was found - pass one explicitly," >&2
        echo "e.g. ./capture_analyzer_macos.sh '' en0" >&2
        exit 1
    fi
fi

THIS_IP="$(ipconfig getifaddr "$INTERFACE" 2>/dev/null || echo "(none)")"
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
OUT_DIR="$SCRIPT_DIR/captures/macos_$(date +%Y%m%d_%H%M%S)"
mkdir -p "$OUT_DIR"
PCAP="$OUT_DIR/capture.pcap"
TXT="$OUT_DIR/capture_readable.txt"

FILTER="tcp or udp"
if [[ -n "$TARGET_IP" ]]; then
    FILTER="host $TARGET_IP and ($FILTER)"
fi

echo "Interface : $INTERFACE (this machine's IP here: $THIS_IP)"
echo "Filter    : $FILTER"
echo "Output    : $OUT_DIR/"
echo
echo "This machine can only see traffic that actually reaches this"
echo "interface - on a switched network that means traffic to/from this"
echo "Mac itself, not a conversation between two other devices elsewhere"
echo "on the network (same limitation the Windows PktMon tool documents)."
echo
echo "Starting capture - needs sudo. Press Ctrl+C to stop."
echo

cleanup() {
    echo
    echo "Stopping..."
    if [[ -n "${TCPDUMP_PID:-}" ]]; then
        sudo kill "$TCPDUMP_PID" 2>/dev/null || true
        wait "$TCPDUMP_PID" 2>/dev/null || true
    fi
    if [[ -s "$PCAP" ]]; then
        echo "Writing readable dump..."
        tcpdump -r "$PCAP" -A -tttt > "$TXT" 2>/dev/null || true
        echo "Done."
        echo "  pcap (Wireshark) -> $PCAP"
        echo "  readable text    -> $TXT"
    else
        echo "No packets were captured."
    fi
}
trap cleanup INT TERM

sudo tcpdump -i "$INTERFACE" -w "$PCAP" -U $FILTER &
TCPDUMP_PID=$!
wait "$TCPDUMP_PID"
cleanup
