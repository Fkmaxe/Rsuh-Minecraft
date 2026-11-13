"""Minecraft status ping without third-party dependencies."""
import json
import socket
import struct


def varint(n):
    result = bytearray()
    while True:
        value = n & 127
        n >>= 7
        result.append(value | (128 if n else 0))
        if not n:
            return bytes(result)


def read_varint(sock):
    value = 0
    for shift in range(0,35,7):
        data = sock.recv(1)
        if not data:
            raise EOFError('Connexion fermée')
        value |= (data[0]&127) << shift
        if not data[0]&128:
            return value
    raise ValueError('VarInt invalide')


def ping(host, port):
    with socket.create_connection((host,port),timeout=5) as sock:
        address = host.encode()
        handshake = b'\x00'+varint(774)+varint(len(address))+address+struct.pack('>H',port)+b'\x01'
        sock.sendall(varint(len(handshake))+handshake+b'\x01\x00')
        read_varint(sock)
        if read_varint(sock) != 0:
            raise ValueError('Réponse Minecraft invalide')
        remaining = read_varint(sock)
        if remaining > 1_000_000:
            raise ValueError('Réponse trop grande')
        chunks = []
        while remaining:
            chunk = sock.recv(remaining)
            if not chunk:
                raise EOFError('Réponse tronquée')
            chunks.append(chunk)
            remaining -= len(chunk)
        return json.loads(b''.join(chunks))
