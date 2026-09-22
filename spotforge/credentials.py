"""fal credentials: explicit environment > process session > dedicated Mac Keychain.

No shell commands, credential files, provider validation calls, or key fragments
in status/error responses. The native bridge uses Security.framework directly.
"""
import ctypes
import os
import sys
import threading
from contextlib import contextmanager


class CredentialError(Exception):
    """Only authored, secret-free text may be exposed to the local API."""


class MacKeychain:
    SERVICE = "com.spotforge.fal"
    ACCOUNT = "api-key"
    NOT_FOUND = -25300

    def __init__(self, service=SERVICE, account=ACCOUNT):
        self.service, self.account = service, account
        self.cf = ctypes.CDLL("/System/Library/Frameworks/CoreFoundation.framework/CoreFoundation")
        self.sec = ctypes.CDLL("/System/Library/Frameworks/Security.framework/Security")
        pointer, integer = ctypes.c_void_p, ctypes.c_long
        for library, name, args, result in [
            (self.cf, "CFStringCreateWithCString", [pointer, ctypes.c_char_p, ctypes.c_uint32], pointer),
            (self.cf, "CFDataCreate", [pointer, pointer, integer], pointer),
            (self.cf, "CFDictionaryCreateMutable", [pointer, integer, pointer, pointer], pointer),
            (self.cf, "CFDictionarySetValue", [pointer, pointer, pointer], None),
            (self.cf, "CFRelease", [pointer], None),
            (self.cf, "CFDataGetLength", [pointer], integer),
            (self.cf, "CFDataGetBytePtr", [pointer], pointer),
            (self.sec, "SecItemCopyMatching", [pointer, ctypes.POINTER(pointer)], ctypes.c_int32),
            (self.sec, "SecItemAdd", [pointer, ctypes.POINTER(pointer)], ctypes.c_int32),
            (self.sec, "SecItemUpdate", [pointer, pointer], ctypes.c_int32),
            (self.sec, "SecItemDelete", [pointer], ctypes.c_int32),
        ]:
            fn = getattr(library, name)
            fn.argtypes, fn.restype = args, result

    def _constant(self, name):
        library = self.cf if name.startswith("kCF") else self.sec
        return ctypes.c_void_p.in_dll(library, name).value

    @contextmanager
    def _query(self, *, data=None, read=None, attributes_only=False):
        # Null callbacks deliberately avoid implicit retain/release; owned CF
        # strings/data stay alive until after the dictionary has been released.
        dictionary = self.cf.CFDictionaryCreateMutable(None, 0, None, None)
        owned = []
        try:
            def put(name, value):
                self.cf.CFDictionarySetValue(dictionary, self._constant(name), value)

            if not attributes_only:
                put("kSecClass", self._constant("kSecClassGenericPassword"))
                for name, value in (("kSecAttrService", self.service), ("kSecAttrAccount", self.account)):
                    ref = self.cf.CFStringCreateWithCString(None, value.encode("utf-8"), 0x08000100)
                    owned.append(ref)
                    put(name, ref)
                # Background status/generation must never trigger an unexpected
                # Keychain UI prompt. Locked/denied access gets a friendly error.
                put("kSecUseAuthenticationUI", self._constant("kSecUseAuthenticationUIFail"))
            if data is not None:
                encoded = data.encode("utf-8")
                buffer = ctypes.create_string_buffer(encoded)
                ref = self.cf.CFDataCreate(None, buffer, len(encoded))
                owned.append(ref)
                put("kSecValueData", ref)
            if read is not None:
                put("kSecMatchLimit", self._constant("kSecMatchLimitOne"))
                put("kSecReturnData" if read else "kSecReturnAttributes", self._constant("kCFBooleanTrue"))
            yield dictionary
        finally:
            self.cf.CFRelease(dictionary)
            for ref in owned:
                self.cf.CFRelease(ref)

    @staticmethod
    def _check(status):
        if status:
            raise CredentialError("macOS-Schlüsselbund nicht zugänglich. Entsperren/Zugriff erlauben oder nur für diese Sitzung speichern.")

    def _read(self, secret):
        result = ctypes.c_void_p()
        with self._query(read=secret) as query:
            status = self.sec.SecItemCopyMatching(query, ctypes.byref(result))
        try:
            if status == self.NOT_FOUND:
                return None
            self._check(status)
            if not secret:
                return True
            size = self.cf.CFDataGetLength(result)
            if size > 4096:
                raise CredentialError("Gespeicherter fal-Schlüssel hat ein ungültiges Format.")
            return ctypes.string_at(self.cf.CFDataGetBytePtr(result), size).decode("utf-8")
        finally:
            if result.value:
                self.cf.CFRelease(result)

    def contains(self):
        return bool(self._read(False))

    def get(self):
        return self._read(True)

    def set(self, key):
        with self._query() as query, self._query(data=key, attributes_only=True) as attributes:
            status = self.sec.SecItemUpdate(query, attributes)
        if status == self.NOT_FOUND:
            with self._query(data=key) as query:
                status = self.sec.SecItemAdd(query, None)
        self._check(status)

    def delete(self):
        with self._query() as query:
            status = self.sec.SecItemDelete(query)
        if status != self.NOT_FOUND:
            self._check(status)


def native_backend():
    if sys.platform != "darwin":
        return None
    try:
        return MacKeychain()
    except (OSError, AttributeError, ValueError):
        return None


class Credentials:
    def __init__(self, backend=None):
        self.backend = backend
        self._session = None
        self._lock = threading.RLock()

    @staticmethod
    def _validate(key):
        if not isinstance(key, str) or not 8 <= len(key) <= 4096 or any(ord(c) < 33 or ord(c) > 126 for c in key):
            raise CredentialError("Schlüssel muss 8 bis 4096 Zeichen ohne Leerzeichen oder Steuerzeichen enthalten.")
        return key

    def status(self):
        with self._lock:
            result = {"configured": False, "source": None, "persistence_available": self.backend is not None}
            if os.environ.get("FAL_KEY"):
                result.update(configured=True, source="environment")
            elif self._session:
                result.update(configured=True, source="session")
            elif self.backend:
                try:
                    if self.backend.contains():
                        result.update(configured=True, source="keychain")
                except Exception:
                    result["error"] = "Schlüsselbundstatus nicht verfügbar. macOS-Schlüsselbund entsperren oder Sitzungsmodus verwenden."
            return result

    def get(self):
        with self._lock:
            if os.environ.get("FAL_KEY"):
                return os.environ["FAL_KEY"]
            if self._session:
                return self._session
            if self.backend:
                try:
                    return self.backend.get()
                except Exception as exc:
                    raise CredentialError("Gespeicherter fal-Schlüssel ist nicht zugänglich. Schlüsselbund entsperren oder Sitzungsmodus verwenden.") from exc
            return None

    def set(self, key, persistence):
        key = self._validate(key)
        with self._lock:
            if persistence == "session":
                self._session = key
            elif persistence == "keychain":
                if not self.backend:
                    raise CredentialError("Dauerhaftes Speichern ist hier nicht verfügbar. Bitte Sitzungsmodus wählen.")
                try:
                    self.backend.set(key)
                except Exception as exc:
                    raise CredentialError("Schlüsselbund konnte nicht speichern. Kein Klartext-Fallback; bitte Sitzungsmodus wählen.") from exc
                self._session = None
            else:
                raise CredentialError("Speichermodus muss keychain oder session sein.")
            return self.status()

    def delete(self):
        with self._lock:
            if self.backend:
                try:
                    self.backend.delete()
                except Exception as exc:
                    raise CredentialError("Gespeicherter Schlüssel konnte nicht gelöscht werden. Schlüsselbundzugriff prüfen; Einträge bleiben unverändert.") from exc
            self._session = None
            return self.status()


_credentials = None
_credentials_lock = threading.Lock()


def get_credentials():
    global _credentials
    with _credentials_lock:
        if _credentials is None:
            _credentials = Credentials(native_backend())
        return _credentials
