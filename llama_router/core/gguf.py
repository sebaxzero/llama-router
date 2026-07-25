"""Minimal GGUF header reader — just enough metadata to classify a file.

Reads only the KV section at the start of the file (a few KB at most),
never tensor data, so it is safe to call on every file during a scan.
"""
from __future__ import annotations

import struct
from pathlib import Path

# GGUF value-type id → struct format (strings and arrays handled separately)
_SCALAR_FMT = {0: "<B", 1: "<b", 2: "<H", 3: "<h", 4: "<I", 5: "<i",
               6: "<f", 7: "<?", 10: "<Q", 11: "<q", 12: "<d"}
_SCALAR_SIZES = {k: struct.calcsize(f) for k, f in _SCALAR_FMT.items()}
_STRING, _ARRAY = 8, 9

# String-valued keys captured verbatim (stored under their last segment)
_WANTED_STR = {"general.architecture", "general.type", "general.size_label"}
# Scalar-valued keys captured as numbers
_WANTED_NUM = {"general.file_type"}

# llama.cpp LLAMA_FTYPE enum → quantisation label (bit 10 = "guessed" flag)
_FTYPE_NAMES = {
    0: "F32", 1: "F16", 2: "Q4_0", 3: "Q4_1", 7: "Q8_0", 8: "Q5_0",
    9: "Q5_1", 10: "Q2_K", 11: "Q3_K_S", 12: "Q3_K_M", 13: "Q3_K_L",
    14: "Q4_K_S", 15: "Q4_K_M", 16: "Q5_K_S", 17: "Q5_K_M", 18: "Q6_K",
    19: "IQ2_XXS", 20: "IQ2_XS", 21: "Q2_K_S", 22: "IQ3_XS", 23: "IQ3_XXS",
    24: "IQ1_S", 25: "IQ4_NL", 26: "IQ3_S", 27: "IQ3_M", 28: "IQ2_S",
    29: "IQ2_M", 30: "IQ4_XS", 31: "IQ1_M", 32: "BF16", 36: "TQ1_0",
    37: "TQ2_0", 38: "MXFP4",
}


def quant_name(file_type: int) -> str:
    """Human label for a general.file_type value ('' when unknown)."""
    return _FTYPE_NAMES.get(file_type & ~1024, "")


def read_gguf_info(path: Path | str) -> dict | None:
    """Return header metadata from a GGUF file.

    Possible keys (each absent if the file doesn't define it):
      architecture, type, size_label (str) · file_type, context_length (int)

    Returns None for non-GGUF / unreadable / truncated files instead of
    raising.
    """
    try:
        with open(path, "rb") as f:
            if f.read(4) != b"GGUF":
                return None
            (version,) = struct.unpack("<I", f.read(4))
            if version < 2:  # v1 used 32-bit lengths; not worth supporting
                return None
            _n_tensors, n_kv = struct.unpack("<QQ", f.read(16))

            def _read_str() -> str:
                (n,) = struct.unpack("<Q", f.read(8))
                return f.read(n).decode("utf-8", "replace")

            def _skip_value(vtype: int) -> None:
                if vtype == _STRING:
                    (n,) = struct.unpack("<Q", f.read(8))
                    f.seek(n, 1)
                elif vtype == _ARRAY:
                    itype, count = struct.unpack("<IQ", f.read(12))
                    if itype in (_STRING, _ARRAY):
                        for _ in range(count):
                            _skip_value(itype)
                    else:
                        f.seek(_SCALAR_SIZES[itype] * count, 1)
                else:
                    f.seek(_SCALAR_SIZES[vtype], 1)

            def _read_scalar(vtype: int):
                fmt = _SCALAR_FMT.get(vtype)
                if fmt is None:
                    _skip_value(vtype)
                    return None
                (v,) = struct.unpack(fmt, f.read(_SCALAR_SIZES[vtype]))
                return v

            info: dict = {}
            for _ in range(n_kv):
                key = _read_str()
                (vtype,) = struct.unpack("<I", f.read(4))
                if key in _WANTED_STR and vtype == _STRING:
                    info[key.rsplit(".", 1)[1]] = _read_str()
                elif key in _WANTED_NUM and vtype not in (_STRING, _ARRAY):
                    v = _read_scalar(vtype)
                    if v is not None:
                        info[key.rsplit(".", 1)[1]] = int(v)
                elif (key.endswith(".context_length")
                        and vtype not in (_STRING, _ARRAY)):
                    v = _read_scalar(vtype)
                    if v is not None:
                        info["context_length"] = int(v)
                else:
                    _skip_value(vtype)
            return info or None
    except Exception:
        return None
