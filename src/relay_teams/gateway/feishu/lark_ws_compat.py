# -*- coding: utf-8 -*-
from __future__ import annotations

import importlib
from types import ModuleType
import warnings

import websockets
from websockets.exceptions import InvalidStatus

_UTCFROMTIMESTAMP_WARNING = r"datetime\.datetime\.utcfromtimestamp\(\) is deprecated.*"
_UTCFROMTIMESTAMP_MODULE = (
    r"lark_oapi\.ws\.pb\.google\.protobuf\.internal\.well_known_types"
)
_NO_CURRENT_EVENT_LOOP_WARNING = r"There is no current event loop"
_WEBSOCKETS_INVALID_STATUS_CODE_WARNING = r"websockets\.InvalidStatusCode is deprecated"
_WEBSOCKETS_LEGACY_WARNING = r"websockets\.legacy is deprecated.*"
_LARK_WS_CLIENT_MODULE = r"lark_oapi\.ws\.client"
_WEBSOCKETS_LEGACY_MODULE = r"websockets\.legacy(?:\..*)?"


def _install_websocket_compat_aliases() -> None:
    # lark-oapi still resolves the pre-14 websockets alias during import.
    setattr(websockets, "InvalidStatusCode", InvalidStatus)


def import_lark_module(module_name: str) -> ModuleType:
    _install_websocket_compat_aliases()
    with warnings.catch_warnings():
        warnings.filterwarnings(
            "ignore",
            message=_UTCFROMTIMESTAMP_WARNING,
            category=DeprecationWarning,
            module=_UTCFROMTIMESTAMP_MODULE,
        )
        warnings.filterwarnings(
            "ignore",
            message=_NO_CURRENT_EVENT_LOOP_WARNING,
            category=DeprecationWarning,
            module=_LARK_WS_CLIENT_MODULE,
        )
        warnings.filterwarnings(
            "ignore",
            message=_WEBSOCKETS_INVALID_STATUS_CODE_WARNING,
            category=DeprecationWarning,
            module=_LARK_WS_CLIENT_MODULE,
        )
        warnings.filterwarnings(
            "ignore",
            message=_WEBSOCKETS_LEGACY_WARNING,
            category=DeprecationWarning,
            module=_WEBSOCKETS_LEGACY_MODULE,
        )
        return importlib.import_module(module_name)


def import_lark_ws_client_module() -> ModuleType:
    return import_lark_module("lark_oapi.ws.client")
