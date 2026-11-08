"""Read-only evidence for a running Windows RDP listener."""
import ipaddress
import socket


def rdp_responds(address, port=33096, timeout=5):
    host = str(ipaddress.ip_address(address))
    # TPKT + X.224 connection request with RDP negotiation. A completed TCP
    # handshake alone could be an unrelated service on the configured port.
    request = bytes.fromhex('030000130ee000000000000100080003000000')
    try:
        with socket.create_connection((host, port), timeout=timeout) as connection:
            connection.settimeout(timeout)
            connection.sendall(request)
            response = connection.recv(64)
    except (OSError, TimeoutError):
        return False
    return len(response) >= 11 and response[:2] == b'\x03\x00' and response[5] == 0xd0
