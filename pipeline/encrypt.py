"""公開用データの暗号化。合言葉 → PBKDF2-SHA256 → AES-256-GCM。

site/app.js の WebCrypto 実装と対になる。形式を変えるときは両方を同時に直す。
"""

import base64
import gzip
import os
import unicodedata

from cryptography.exceptions import InvalidTag
from cryptography.hazmat.primitives import hashes
from cryptography.hazmat.primitives.ciphers.aead import AESGCM
from cryptography.hazmat.primitives.kdf.pbkdf2 import PBKDF2HMAC

ITERATIONS = 600_000
AAD = "hibi-rhythm-v1"


def normalize(passphrase):
    """全角・半角や前後の空白の違いで復号に失敗しないよう揃える。"""
    return unicodedata.normalize("NFKC", passphrase).strip()


def derive_key(passphrase, salt, iterations):
    kdf = PBKDF2HMAC(algorithm=hashes.SHA256(), length=32, salt=salt, iterations=iterations)
    return kdf.derive(normalize(passphrase).encode("utf-8"))


def encode(raw):
    return base64.b64encode(raw).decode("ascii")


def encrypt(plaintext, passphrase, iterations=ITERATIONS):
    """bytes を暗号化し、JSON化できる封筒（辞書）を返す。"""
    salt, iv = os.urandom(16), os.urandom(12)
    key = derive_key(passphrase, salt, iterations)
    data = AESGCM(key).encrypt(iv, gzip.compress(plaintext, mtime=0), AAD.encode("ascii"))
    return {"v": 1, "kdf": "PBKDF2-SHA256", "iterations": iterations, "cipher": "AES-256-GCM",
            "compression": "gzip", "aad": AAD, "salt": encode(salt), "iv": encode(iv), "data": encode(data)}


def decrypt(envelope, passphrase):
    """封筒を復号する。合言葉が違えば ValueError。"""
    key = derive_key(passphrase, base64.b64decode(envelope["salt"]), envelope["iterations"])
    try:
        data = AESGCM(key).decrypt(base64.b64decode(envelope["iv"]), base64.b64decode(envelope["data"]),
                                   envelope["aad"].encode("ascii"))
    except InvalidTag:
        raise ValueError("合言葉が違うか、データが壊れています") from None
    return gzip.decompress(data)
