"""Encryption for secrets that an administrator enters through the UI (e.g. the SMTP password).

The key comes from SETTINGS_ENCRYPTION_KEY (one Fernet key, or several comma-separated for rotation: the first encrypts, all decrypt).
It is never stored in the database or the repository. Without a key, secrets cannot be stored at all: there is no silent plaintext fallback.
Generate one with:  python -c "from cryptography.fernet import Fernet; print(Fernet.generate_key().decode())"
"""
import os
from cryptography.fernet import Fernet, MultiFernet, InvalidToken

class SecretBoxError(Exception): pass

def _keys():
    return [k.strip() for k in os.getenv('SETTINGS_ENCRYPTION_KEY', '').split(',') if k.strip()]

def available(): return bool(_keys())

def _box():
    ks = _keys()
    if not ks: raise SecretBoxError('SETTINGS_ENCRYPTION_KEY is not configured on the server, so secrets cannot be stored.')
    try: return MultiFernet([Fernet(k.encode()) for k in ks])
    except Exception: raise SecretBoxError('SETTINGS_ENCRYPTION_KEY is not a valid Fernet key.')

def encrypt(plain): return _box().encrypt(plain.encode('utf-8')).decode('ascii')

def decrypt(token):
    try: return _box().decrypt(token.encode('ascii')).decode('utf-8')
    except InvalidToken: raise SecretBoxError('Stored secret cannot be decrypted with the configured SETTINGS_ENCRYPTION_KEY (key changed?). Re-enter the credential.')
