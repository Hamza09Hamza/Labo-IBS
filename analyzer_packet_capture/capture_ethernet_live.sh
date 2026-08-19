#!/usr/bin/env bash
set -euo pipefail

# Live capture for the direct Ethernet link to another machine on
# 192.168.1.0/24 - no interface name needed. It auto-detects the right
# interface by finding whichever one currently holds an IP in that subnet,
# which is safe here specifically because the plan is: plug in Ethernet,
# turn off Wi-Fi, so the Ethernet adapter becomes the only interface with
# an IP in 192.168.1.0/24 (Wi-Fi is normally a different subnet, and will
# have no IP at all once turned off).
#
# PASSIVE ONLY: this only ever reads a copy of packets already on the wire
# (tcpdump -i / -r). It never binds, listens on, or closes any port on
# either machine, so it cannot interfere with or interrupt whatever
# connection the analyzer makes - unlike capture_listener.py, which
# actually binds ports and would compete with a real listener.
#
# Two tcpdump processes run at once (BPF on macOS supports multiple
# independent readers of the same interface, so this is safe):
#   1. A raw writer capturing EVERY packet, no filter at all, straight to
#      capture.pcap - the complete, untouched, authoritative record.
#   2. A live human-readable view printed to the terminal as packets
#      arrive, with source/dest MAC (-e - see note below) and full
#      hex+ASCII payload (-X), with this Mac's own confirmed background
#      noise (NetBIOS udp/137+138, mDNS udp/5353 - traced to this
#      machine's own IP/MAC in an earlier capture) hidden so it doesn't
#      bury anything real. That noise is still in capture.pcap untouched.
#
# -e (MAC addresses) matters because a device can only ever ARP-reply
# "is-at" for its own address - that's how ARP works - so a MAC address
# seen here can be tied back to "this Mac's adapter" or "the other
# machine's NIC" with certainty, not inference.
#
# When you stop it (Ctrl+C), this additionally splits the authoritative
# capture.pcap into two fully-decoded files - tcp.txt and udp.txt - so TCP
# and UDP can each be reviewed on their own instead of interleaved.
#
# Timing matters more than filtering for catching something like a
# power-on/link-up announcement from the other machine: start this script
# FIRST (as soon as it says "listening" below), THEN power-cycle or
# trigger the other machine - otherwise its first message can happen
# before this is listening.
#
# Usage:
#   ./capture_ethernet_live.sh                  # target 192.168.1.6
#   ./capture_ethernet_live.sh 192.168.1.50      # different target IP
#
# Needs sudo (tcpdump needs raw socket access on macOS). Press Ctrl+C to stop.

TARGET_IP="${1:-192.168.1.6}"
SUBNET_PREFIX="192.168.1."

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
OUT_DIR="$SCRIPT_DIR/captures"
mkdir -p "$OUT_DIR"
STAMP="$(date +%Y%m%d_%H%M%S)"
PCAP="$OUT_DIR/ethernet_live_${STAMP}.pcap"
LIVE_TXT="$OUT_DIR/ethernet_live_${STAMP}_live.txt"
TCP_TXT="$OUT_DIR/ethernet_live_${STAMP}_tcp.txt"
UDP_TXT="$OUT_DIR/ethernet_live_${STAMP}_udp.txt"

echo "Looking for the Ethernet interface (an interface with an IP starting with $SUBNET_PREFIX)..."
INTERFACE=""
THIS_IP=""
for candidate in $(ifconfig -l); do
    ip="$(ipconfig getifaddr "$candidate" 2>/dev/null || true)"
    if [[ "$ip" == "$SUBNET_PREFIX"* ]]; then
        INTERFACE="$candidate"
        THIS_IP="$ip"
        break
    fi
done

if [[ -z "$INTERFACE" ]]; then
    echo >&2
    echo "No interface with an IP in ${SUBNET_PREFIX}0/24 was found yet." >&2
    echo "Plug in the Ethernet cable and turn off Wi-Fi, then re-run this script." >&2
    echo >&2
    echo "Interfaces currently seen:" >&2
    for candidate in $(ifconfig -l); do
        ip="$(ipconfig getifaddr "$candidate" 2>/dev/null || echo "(no IP)")"
        echo "  $candidate: $ip" >&2
    done
    exit 1
fi

# Confirmed self-noise from a prior capture on this Mac - every single
# packet on these had this machine's own IP/MAC as source, never the other
# host's. Hidden from the live view only - capture.pcap still has it.
KNOWN_SELF_NOISE="udp port 137 or udp port 138 or udp port 5353"

echo "Interface : $INTERFACE (this machine: $THIS_IP)"
echo "Target    : $TARGET_IP <-> $THIS_IP - capturing EVERY packet, no filter"
echo "Raw pcap  : $PCAP  (unfiltered, authoritative - open in Wireshark if needed)"
echo "Live view : $LIVE_TXT  (self-noise on udp/137,138,5353 hidden)"
echo "After stop: separate fully-decoded tcp.txt and udp.txt get written too"
echo
echo "This is read-only - it never binds/closes any port on either machine,"
echo "so it cannot interrupt the analyzer's connection."
echo
echo "Start the other machine's workflow/power-cycle AFTER the line below"
echo "that says 'listening', so its first message isn't missed."
echo
echo "Starting live capture - needs sudo twice (raw writer + live view)."
echo "Press Ctrl+C to stop."
echo

cleanup() {
    echo
    echo "Stopping..."
    if [[ -n "${WRITER_PID:-}" ]]; then
        sudo kill "$WRITER_PID" 2>/dev/null || true
        wait "$WRITER_PID" 2>/dev/null || true
    fi

    if [[ -s "$PCAP" ]]; then
        echo "Splitting the raw capture into decoded TCP-only and UDP-only files..."
        tcpdump -r "$PCAP" -n -tttt -e -X tcp > "$TCP_TXT" 2>/dev/null || true
        tcpdump -r "$PCAP" -n -tttt -e -X udp > "$UDP_TXT" 2>/dev/null || true
        echo "Done."
        echo "  raw pcap (everything, Wireshark) -> $PCAP"
        echo "  live view (noise hidden)         -> $LIVE_TXT"
        echo "  TCP only, decoded                -> $TCP_TXT"
        echo "  UDP only, decoded                -> $UDP_TXT"
    else
        echo "No packets were captured."
    fi
}
trap cleanup INT TERM

sudo tcpdump -i "$INTERFACE" -w "$PCAP" -U >/dev/null 2>&1 &
WRITER_PID=$!

sudo tcpdump -i "$INTERFACE" -n -tttt -e -X -l "not ($KNOWN_SELF_NOISE)" | tee "$LIVE_TXT"
cleanup
