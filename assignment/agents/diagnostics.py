"""Opt-in encrypted diagnostics. Plaintext cases never enter public logs or artifacts."""
import base64
import json
import os
import time
import uuid
from pathlib import Path

from cryptography.hazmat.primitives import hashes, serialization
from cryptography.hazmat.primitives.asymmetric import padding
from cryptography.hazmat.primitives.ciphers.aead import AESGCM


def directory():
    return Path(os.getenv("DIAGNOSTIC_DIR", "/tmp/tds-diagnostics"))


def emit(event, data):
    path = os.getenv("DIAGNOSTIC_PUBLIC_KEY_FILE")
    if not path:
        return
    try:
        public = serialization.load_pem_public_key(Path(path).read_bytes())
        key, nonce = AESGCM.generate_key(256), os.urandom(12)
        payload = json.dumps({"event": event, "time": time.time(), "data": data}, ensure_ascii=False).encode()
        encrypted = AESGCM(key).encrypt(nonce, payload, b"tds-diagnostics-v1")
        b64 = lambda b: base64.b64encode(b).decode()
        envelope = {"version": 1, "key": b64(public.encrypt(key, padding.OAEP(mgf=padding.MGF1(hashes.SHA256()), algorithm=hashes.SHA256(), label=None))), "nonce": b64(nonce), "ciphertext": b64(encrypted)}
        root = directory()
        root.mkdir(parents=True, exist_ok=True)
        (root/(str(time.time_ns())+"-"+uuid.uuid4().hex+".json")).write_text(json.dumps(envelope))
        files = sorted(root.glob("*.json"))
        for old in files[:-200]:
            old.unlink(missing_ok=True)
    except Exception:
        # Diagnostic availability cannot alter the protocol outcome.
        pass
