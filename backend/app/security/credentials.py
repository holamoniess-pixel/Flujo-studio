from __future__ import annotations

import ctypes
import hashlib
import os
import uuid
from dataclasses import dataclass
from pathlib import Path
from typing import Any


class CredentialStoreError(Exception):
    def __init__(self, code: str, message: str) -> None:
        super().__init__(message)
        self.code = code
        self.message = message


if os.name == "nt":
    class DataBlob(ctypes.Structure):
        _fields_ = [("cbData", ctypes.c_uint32), ("pbData", ctypes.POINTER(ctypes.c_ubyte))]

    _crypt32 = ctypes.windll.crypt32
    _kernel32 = ctypes.windll.kernel32
    _crypt32.CryptProtectData.argtypes = [ctypes.POINTER(DataBlob), ctypes.c_wchar_p, ctypes.POINTER(DataBlob), ctypes.c_void_p, ctypes.c_void_p, ctypes.c_uint32, ctypes.POINTER(DataBlob)]
    _crypt32.CryptProtectData.restype = ctypes.c_bool
    _crypt32.CryptUnprotectData.argtypes = [ctypes.POINTER(DataBlob), ctypes.POINTER(ctypes.c_wchar_p), ctypes.POINTER(DataBlob), ctypes.c_void_p, ctypes.c_void_p, ctypes.c_uint32, ctypes.POINTER(DataBlob)]
    _crypt32.CryptUnprotectData.restype = ctypes.c_bool
    _kernel32.LocalFree.argtypes = [ctypes.c_void_p]


def _protect(value: str) -> bytes:
    if os.name != "nt":
        raise CredentialStoreError("SECURE_STORE_UNAVAILABLE", "The local secure credential store is only enabled on Windows.")
    raw = value.encode("utf-8")
    source = ctypes.create_string_buffer(raw)
    source_blob = DataBlob(len(raw), ctypes.cast(source, ctypes.POINTER(ctypes.c_ubyte)))
    protected_blob = DataBlob()
    if not _crypt32.CryptProtectData(ctypes.byref(source_blob), "Flujo Studio credential", None, None, None, 0, ctypes.byref(protected_blob)):
        raise CredentialStoreError("SECURE_STORE_WRITE_FAILED", "Windows could not protect the credential.")
    try:
        return ctypes.string_at(protected_blob.pbData, protected_blob.cbData)
    finally:
        _kernel32.LocalFree(protected_blob.pbData)


def _unprotect(value: bytes) -> str:
    if os.name != "nt":
        raise CredentialStoreError("SECURE_STORE_UNAVAILABLE", "The local secure credential store is only enabled on Windows.")
    source = ctypes.create_string_buffer(value)
    source_blob = DataBlob(len(value), ctypes.cast(source, ctypes.POINTER(ctypes.c_ubyte)))
    plain_blob = DataBlob()
    description = ctypes.c_wchar_p()
    if not _crypt32.CryptUnprotectData(ctypes.byref(source_blob), ctypes.byref(description), None, None, None, 0, ctypes.byref(plain_blob)):
        raise CredentialStoreError("SECURE_STORE_READ_FAILED", "Windows could not unprotect the credential.")
    try:
        return ctypes.string_at(plain_blob.pbData, plain_blob.cbData).decode("utf-8")
    finally:
        _kernel32.LocalFree(plain_blob.pbData)


@dataclass(frozen=True)
class CredentialMetadata:
    credential_id: str
    provider: str
    display_name: str
    masked_value: str
    scope: str
    status: str
    blob_path: str
    created_at: str
    updated_at: str

    def as_dict(self) -> dict[str, Any]:
        return self.__dict__.copy()


class CredentialStore:
    def __init__(self, root: Path) -> None:
        self.root = root.resolve()
        self.root.mkdir(parents=True, exist_ok=True)

    def _path(self, credential_id: str) -> Path:
        if not credential_id or any(char not in "abcdefghijklmnopqrstuvwxyzABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789_-" for char in credential_id):
            raise CredentialStoreError("INVALID_CREDENTIAL_ID", "Credential ID contains unsupported characters.")
        return self.root / f"{credential_id}.bin"

    @staticmethod
    def _validate_secret(secret: str) -> None:
        if not secret or len(secret) > 400:
            raise CredentialStoreError("INVALID_CREDENTIAL", "Credential value is empty or exceeds the maximum size.")

    def create(self, provider: str, display_name: str, secret: str, scope: str, now: str) -> CredentialMetadata:
        self._validate_secret(secret)
        credential_id = f"cred_{uuid.uuid4().hex}"
        blob_path = self._path(credential_id)
        blob_path.write_bytes(_protect(secret))
        try:
            os.chmod(blob_path, 0o600)
        except OSError:
            pass
        masked = f"{secret[:3]}••••••••{secret[-3:]}" if len(secret) >= 8 else "••••••••"
        return CredentialMetadata(credential_id, provider[:100], display_name[:120], masked, scope[:120], "active", str(blob_path), now, now)

    def read(self, metadata: CredentialMetadata) -> str:
        try:
            expected_path = self._path(metadata.credential_id).resolve()
            if expected_path.parent != self.root or Path(metadata.blob_path).resolve() != expected_path:
                raise CredentialStoreError("CREDENTIAL_PATH_FORBIDDEN", "Credential metadata points outside the managed credential store.")
            return _unprotect(expected_path.read_bytes())
        except FileNotFoundError as exc:
            raise CredentialStoreError("CREDENTIAL_NOT_FOUND", "Credential blob was not found.") from exc

    def rotate(self, metadata: CredentialMetadata, secret: str, now: str) -> CredentialMetadata:
        self._validate_secret(secret)
        path = self._path(metadata.credential_id)
        path.write_bytes(_protect(secret))
        try:
            os.chmod(path, 0o600)
        except OSError:
            pass
        masked = f"{secret[:3]}••••••••{secret[-3:]}" if len(secret) >= 8 else "••••••••"
        return CredentialMetadata(metadata.credential_id, metadata.provider, metadata.display_name, masked, metadata.scope, "active", metadata.blob_path, metadata.created_at, now)

    def revoke(self, metadata: CredentialMetadata, now: str) -> CredentialMetadata:
        try:
            self._path(metadata.credential_id).unlink(missing_ok=True)
        except OSError as exc:
            raise CredentialStoreError("SECURE_STORE_DELETE_FAILED", "Credential could not be revoked safely.") from exc
        return CredentialMetadata(metadata.credential_id, metadata.provider, metadata.display_name, metadata.masked_value, metadata.scope, "revoked", metadata.blob_path, metadata.created_at, now)

    @staticmethod
    def fingerprint(secret: str) -> str:
        return hashlib.sha256(secret.encode("utf-8")).hexdigest()
