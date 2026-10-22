"""Current-user Windows Credential Manager storage; no plaintext fallback."""

import ctypes
from ctypes import wintypes
import re
import sys

SERVICE = "ai.auday.workflow-link.experimental"


class CredentialError(Exception):
    pass


class Credential(ctypes.Structure):
    _fields_ = [
        ("Flags", wintypes.DWORD),
        ("Type", wintypes.DWORD),
        ("TargetName", wintypes.LPWSTR),
        ("Comment", wintypes.LPWSTR),
        ("LastWritten", wintypes.FILETIME),
        ("CredentialBlobSize", wintypes.DWORD),
        ("CredentialBlob", ctypes.POINTER(wintypes.BYTE)),
        ("Persist", wintypes.DWORD),
        ("AttributeCount", wintypes.DWORD),
        ("Attributes", ctypes.c_void_p),
        ("TargetAlias", wintypes.LPWSTR),
        ("UserName", wintypes.LPWSTR),
    ]


def _target(profile):
    if not isinstance(profile, str) or not re.fullmatch(r"[a-zA-Z0-9_-]{1,64}", profile):
        raise CredentialError("invalid_profile_name")
    return SERVICE + "/" + profile


def _api():
    if sys.platform != "win32":
        raise CredentialError("windows_credential_manager_required")
    api = ctypes.WinDLL("advapi32", use_last_error=True)
    api.CredWriteW.argtypes = [ctypes.POINTER(Credential), wintypes.DWORD]
    api.CredWriteW.restype = wintypes.BOOL
    api.CredReadW.argtypes = [wintypes.LPCWSTR, wintypes.DWORD, wintypes.DWORD,
                             ctypes.POINTER(ctypes.POINTER(Credential))]
    api.CredReadW.restype = wintypes.BOOL
    api.CredDeleteW.argtypes = [wintypes.LPCWSTR, wintypes.DWORD, wintypes.DWORD]
    api.CredDeleteW.restype = wintypes.BOOL
    api.CredFree.argtypes = [ctypes.c_void_p]
    api.CredFree.restype = None
    return api


def save_secret(password, profile):
    target = _target(profile)
    data = password.encode("utf-8")
    if not data or len(data) > 2560:
        raise CredentialError("invalid_credential")
    buffer = (wintypes.BYTE * len(data)).from_buffer_copy(data)
    credential = Credential(Type=1, TargetName=target, CredentialBlobSize=len(data),
                            CredentialBlob=buffer, Persist=2, UserName=profile)
    try:
        if not _api().CredWriteW(ctypes.byref(credential), 0):
            raise CredentialError("credential_manager_save_failed")
    finally:
        ctypes.memset(buffer, 0, len(data))


def read_secret(profile):
    target = _target(profile)
    api = _api()
    pointer = ctypes.POINTER(Credential)()
    if not api.CredReadW(target, 1, 0, ctypes.byref(pointer)):
        raise CredentialError("pair_on_local_terminal_first")
    try:
        credential = pointer.contents
        size = credential.CredentialBlobSize
        if not credential.CredentialBlob or not 0 < size <= 2560:
            raise CredentialError("invalid_credential")
        try:
            return ctypes.string_at(credential.CredentialBlob, size).decode("utf-8")
        except UnicodeError:
            raise CredentialError("invalid_credential") from None
    finally:
        if pointer.contents.CredentialBlob and pointer.contents.CredentialBlobSize <= 2560:
            ctypes.memset(pointer.contents.CredentialBlob, 0, pointer.contents.CredentialBlobSize)
        api.CredFree(pointer)


def delete_secret(profile):
    """Used only to clean up an explicitly chosen disposable test credential."""
    if not _api().CredDeleteW(_target(profile), 1, 0):
        raise CredentialError("credential_manager_delete_failed")
