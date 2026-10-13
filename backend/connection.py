import sys
import os
import threading
import pythonnet
pythonnet.load("netfx")
import clr

import config

SDK = None
_dll_loaded = False
_dll_lock = threading.Lock()
_local = threading.local()


def _load_dll():
    global _dll_loaded, SDK
    if _dll_loaded:
        return
    with _dll_lock:
        if _dll_loaded:
            return
        dll_dir = os.path.dirname(config.DLL_PATH)
        if dll_dir not in sys.path:
            sys.path.insert(0, dll_dir)
        clr.AddReference(config.DLL_PATH)
        import Pastel.Evolution as _SDK
        SDK = _SDK
        _dll_loaded = True


def connect():
    """
    Ensure Evolution SDK is loaded (process-wide) and that a DatabaseContext
    exists on the current thread. The Pastel SDK stores its connection in
    thread-local state, so every worker thread FastAPI dispatches to needs
    its own CreateConnection call.
    """
    _load_dll()

    if getattr(_local, "connected", False):
        return SDK

    from Pastel.Evolution import DatabaseContext
    DatabaseContext.CreateCommonDBConnection(
        config.COMMON_SERVER,
        config.COMMON_DATABASE,
        config.COMMON_USERNAME,
        config.COMMON_PASSWORD,
        False,
    )
    DatabaseContext.SetLicense(config.LICENSE_KEY, config.LICENSE_CODE)
    DatabaseContext.CreateConnection(
        config.SERVER,
        config.COMPANY_DATABASE,
        config.DB_USERNAME,
        config.DB_PASSWORD,
        False,
    )
    _local.connected = True
    print(f"Evolution connected on thread {threading.get_ident()}.")
    return SDK


def get_sdk():
    return connect()
