"""Pure-Python decompressor for "compressed RTF" (LZFu / MS-OXRTFCP).

Used by the native PST reader so ``PidTagRtfCompressed`` bodies can be shown
without the optional ``compressed_rtf`` dependency.
"""

from __future__ import annotations

import struct

# The 207-byte dictionary preamble every LZFu stream starts from.
_INIT_DICT = (
    b"{\\rtf1\\ansi\\mac\\deff0\\deftab720{\\fonttbl;}"
    b"{\\f0\\fnil \\froman \\fswiss \\fmodern \\fscript "
    b"\\fdecor MS Sans SerifSymbolArialTimes New RomanCourier"
    b"{\\colortbl\\red0\\green0\\blue0\r\n\\par "
    b"\\pard\\plain\\f0\\fs20\\b\\i\\u\\tab\\tx"
)

_MAGIC_COMPRESSED = 0x75465A4C  # "LZFu"
_MAGIC_UNCOMPRESSED = 0x414C454D  # "MELA"
_DICT_SIZE = 4096


def decompress_rtf(data: bytes) -> bytes:
    if len(data) < 16:
        raise ValueError("compressed RTF header truncated")
    comp_size, raw_size, magic, _crc = struct.unpack_from("<IIII", data, 0)
    body = data[16 : 16 + comp_size - 12] if comp_size >= 12 else data[16:]

    if magic == _MAGIC_UNCOMPRESSED:
        return body[:raw_size]
    if magic != _MAGIC_COMPRESSED:
        raise ValueError(f"unknown compressed-RTF magic {magic:#x}")

    dict_buf = bytearray(_DICT_SIZE)
    dict_buf[: len(_INIT_DICT)] = _INIT_DICT
    write_pos = len(_INIT_DICT)

    out = bytearray()
    i = 0
    n = len(body)
    while i < n and len(out) < raw_size:
        flags = body[i]
        i += 1
        for bit in range(8):
            if i >= n or len(out) >= raw_size:
                break
            if flags & (1 << bit):
                if i + 2 > n:
                    return bytes(out)
                token = (body[i] << 8) | body[i + 1]
                i += 2
                offset = token >> 4
                length = (token & 0x0F) + 2
                if offset == (write_pos % _DICT_SIZE):
                    return bytes(out)  # end-of-stream marker
                for _ in range(length):
                    b = dict_buf[offset % _DICT_SIZE]
                    out.append(b)
                    dict_buf[write_pos % _DICT_SIZE] = b
                    write_pos += 1
                    offset += 1
            else:
                b = body[i]
                i += 1
                out.append(b)
                dict_buf[write_pos % _DICT_SIZE] = b
                write_pos += 1
    return bytes(out[:raw_size])
