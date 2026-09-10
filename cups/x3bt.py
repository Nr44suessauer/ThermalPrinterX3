#!/usr/bin/python3
# -*- coding: utf-8 -*-
"""
CUPS backend "x3bt" for the Snap & Tag thermal printer X3.

Called by CUPS for the queue "X3Thermo" (device URI x3bt://<MAC>):
  - without arguments: print the device list for discovery
  - with arguments: read the job data (frames) from stdin and send them to
    the printer over Bluetooth/RFCOMM (channel 1).

Standard library only (no sudo/Pillow needed).
"""

import socket
import sys
import time

DEFAULT_MAC = ""                 # empty = the address must be part of the device URI
CHANNEL = 1
RETRIES = 3


def list_devices() -> None:
    # format: <class> <scheme://uri> "<description>"
    print('direct x3bt:// "X3 Thermo (Snap & Tag Bluetooth)"')


def parse_mac(uri: str) -> str:
    addr = uri.split("://", 1)[-1].split("/")[0].strip()
    if ":" in addr and len(addr) >= 8:
        return addr.upper()
    return DEFAULT_MAC


def send_frames(mac: str, data: bytes) -> int:
    last = None
    for i in range(RETRIES):
        try:
            s = socket.socket(socket.AF_BLUETOOTH, socket.SOCK_STREAM,
                              socket.BTPROTO_RFCOMM)
            s.settimeout(15)
            s.connect((mac, CHANNEL))
            s.sendall(data)
            time.sleep(0.8)          # let the buffer drain
            s.close()
            return 0
        except OSError as e:
            last = e
            time.sleep(1.5)
    sys.stderr.write(f"x3bt: connection to {mac} failed: {last}\n")
    return 1


def main() -> int:
    argv = sys.argv
    if len(argv) < 2:
        list_devices()
        return 0

    mac = parse_mac(argv[0])
    if not mac:
        sys.stderr.write("x3bt: no MAC address - use device URI x3bt://<MAC>\n")
        return 2
    data = sys.stdin.buffer.read()
    if not data:
        return 0
    return send_frames(mac, data)


if __name__ == "__main__":
    sys.exit(main())
