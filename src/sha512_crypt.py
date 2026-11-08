"""SHA-512 crypt using hashlib, for portable Windows hosts without OpenSSL.

Algorithm and public-domain reference vectors:
https://www.akkadia.org/drepper/SHA-crypt.txt
"""
import hashlib
import re
import secrets

ALPHABET = './0123456789ABCDEFGHIJKLMNOPQRSTUVWXYZabcdefghijklmnopqrstuvwxyz'


def hash_password(password, salt=None, rounds=100000):
    salt = salt if salt is not None else ''.join(secrets.choice(ALPHABET) for _ in range(16))
    if not re.fullmatch(r'[./A-Za-z0-9]{1,16}', salt) or not 1000 <= rounds <= 1000000:
        raise ValueError('Invalid SHA-512 crypt salt or rounds')
    password = password.encode('utf-8')
    salt_bytes = salt.encode('ascii')
    digest = lambda value: hashlib.sha512(value).digest()
    repeat = lambda value, length: (value * ((length + 63) // 64))[:length]
    alternate = digest(password + salt_bytes + password)
    context = hashlib.sha512(password + salt_bytes + repeat(alternate, len(password)))
    bits = len(password)
    while bits:
        context.update(alternate if bits & 1 else password)
        bits >>= 1
    result = context.digest()
    p = repeat(digest(password * len(password)), len(password))
    s = repeat(digest(salt_bytes * (16 + result[0])), len(salt_bytes))
    for index in range(rounds):
        value = p if index & 1 else result
        if index % 3:
            value += s
        if index % 7:
            value += p
        value += result if index & 1 else p
        result = digest(value)
    groups = [(0,21,42),(22,43,1),(44,2,23),(3,24,45),(25,46,4),(47,5,26),
              (6,27,48),(28,49,7),(50,8,29),(9,30,51),(31,52,10),(53,11,32),
              (12,33,54),(34,55,13),(56,14,35),(15,36,57),(37,58,16),(59,17,38),
              (18,39,60),(40,61,19),(62,20,41)]
    encoded = ''
    for high, middle, low in groups:
        value = result[high] << 16 | result[middle] << 8 | result[low]
        for _ in range(4):
            encoded += ALPHABET[value & 63]
            value >>= 6
    value = result[63]
    encoded += ALPHABET[value & 63] + ALPHABET[value >> 6]
    return '$6$rounds=' + str(rounds) + '$' + salt + '$' + encoded
