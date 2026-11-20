"""Block-aligned AES decryption through macOS CommonCrypto (elsewhere, the Linux build: the cryptography package), shared by archive readers."""
import ctypes, sys

_cc = None


def decrypt(key, data, iv=None):
    """AES ECB without an IV, CBC with a 16-byte IV; neither mode removes padding."""
    if len(key) not in (16, 24, 32) or len(data) % 16 or (iv is not None and len(iv) != 16):
        raise ValueError('invalid AES key, data or IV length')
    if sys.platform != 'darwin': return _decrypt_other(key, data, iv)
    global _cc
    if _cc is None:
        _cc = ctypes.CDLL('/usr/lib/libSystem.B.dylib').CCCrypt
        _cc.restype = ctypes.c_int32
        _cc.argtypes = [ctypes.c_uint32, ctypes.c_uint32, ctypes.c_uint32, ctypes.c_char_p, ctypes.c_size_t,
                       ctypes.c_void_p, ctypes.c_char_p, ctypes.c_size_t, ctypes.c_void_p, ctypes.c_size_t,
                       ctypes.POINTER(ctypes.c_size_t)]
    out, moved = ctypes.create_string_buffer(len(data)), ctypes.c_size_t()
    if _cc(1, 0, 2 if iv is None else 0, key, len(key), iv, data, len(data), out, len(data), ctypes.byref(moved)):
        raise ValueError('CCCrypt failed')
    return out.raw[:moved.value]


def _decrypt_other(key, data, iv):
    try: from cryptography.hazmat.primitives.ciphers import Cipher, algorithms, modes
    except ImportError as e: raise ValueError('AES off macOS needs the cryptography package (pip install cryptography)') from e
    d = Cipher(algorithms.AES(key), modes.ECB() if iv is None else modes.CBC(iv)).decryptor()
    return d.update(data) + d.finalize()
