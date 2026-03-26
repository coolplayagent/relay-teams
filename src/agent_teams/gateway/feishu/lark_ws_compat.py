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


def import_lark_ws_client_module() -> ModuleType:
    # lark-oapi still resolves the pre-14 websockets alias during import.
    setattr(websockets, "InvalidStatusCode", InvalidStatus)
    with warnings.catch_warnings():
        warnings.filterwarnings(
            "ignore",
            message=_UTCFROMTIMESTAMP_WARNING,
            category=DeprecationWarning,
            module=_UTCFROMTIMESTAMP_MODULE,
        )
        return importlib.import_module("lark_oapi.ws.client")
