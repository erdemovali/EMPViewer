"""A dependency-free reader for Unicode ``.pst`` / ``.ost`` files.

Implements just enough of Microsoft's PFF/PST format ([MS-PST]) to walk the
folder tree and pull messages + attachments + recipients:

    NDB layer   header -> node BTree (NBT) + block BTree (BBT) -> blocks
                (+ permute / cyclic decryption, XBLOCK/XXBLOCK, subnode trees)
    LTP layer   Heap-on-Node (HN), BTree-on-Heap (BTH),
                Property Context (PC), Table Context (TC)
    Messaging   message store -> folders (hierarchy/contents tables)
                -> messages -> attachment & recipient tables

Scope: Unicode PST/OST (Outlook 2003+). ANSI stores and the 4K "Unicode4K"
variant are detected and rejected with a clear message rather than mis-parsed.
Encryption: none, "compressible"/permute, and "high"/cyclic are all handled.

The three substitution tables and the cyclic algorithm are transcribed from
libyal/libpff (``libpff_encryption.c``); each table is asserted to be a
bijection over 0..255 at import time.
"""

from __future__ import annotations

import mmap
import struct
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Iterator

# --------------------------------------------------------------------------- #
# Encryption (from libyal/libpff libpff_encryption.c)
# --------------------------------------------------------------------------- #
_COMPRESSIBLE = bytes((
    0x47,0xf1,0xb4,0xe6,0x0b,0x6a,0x72,0x48,0x85,0x4e,0x9e,0xeb,0xe2,0xf8,0x94,0x53,
    0xe0,0xbb,0xa0,0x02,0xe8,0x5a,0x09,0xab,0xdb,0xe3,0xba,0xc6,0x7c,0xc3,0x10,0xdd,
    0x39,0x05,0x96,0x30,0xf5,0x37,0x60,0x82,0x8c,0xc9,0x13,0x4a,0x6b,0x1d,0xf3,0xfb,
    0x8f,0x26,0x97,0xca,0x91,0x17,0x01,0xc4,0x32,0x2d,0x6e,0x31,0x95,0xff,0xd9,0x23,
    0xd1,0x00,0x5e,0x79,0xdc,0x44,0x3b,0x1a,0x28,0xc5,0x61,0x57,0x20,0x90,0x3d,0x83,
    0xb9,0x43,0xbe,0x67,0xd2,0x46,0x42,0x76,0xc0,0x6d,0x5b,0x7e,0xb2,0x0f,0x16,0x29,
    0x3c,0xa9,0x03,0x54,0x0d,0xda,0x5d,0xdf,0xf6,0xb7,0xc7,0x62,0xcd,0x8d,0x06,0xd3,
    0x69,0x5c,0x86,0xd6,0x14,0xf7,0xa5,0x66,0x75,0xac,0xb1,0xe9,0x45,0x21,0x70,0x0c,
    0x87,0x9f,0x74,0xa4,0x22,0x4c,0x6f,0xbf,0x1f,0x56,0xaa,0x2e,0xb3,0x78,0x33,0x50,
    0xb0,0xa3,0x92,0xbc,0xcf,0x19,0x1c,0xa7,0x63,0xcb,0x1e,0x4d,0x3e,0x4b,0x1b,0x9b,
    0x4f,0xe7,0xf0,0xee,0xad,0x3a,0xb5,0x59,0x04,0xea,0x40,0x55,0x25,0x51,0xe5,0x7a,
    0x89,0x38,0x68,0x52,0x7b,0xfc,0x27,0xae,0xd7,0xbd,0xfa,0x07,0xf4,0xcc,0x8e,0x5f,
    0xef,0x35,0x9c,0x84,0x2b,0x15,0xd5,0x77,0x34,0x49,0xb6,0x12,0x0a,0x7f,0x71,0x88,
    0xfd,0x9d,0x18,0x41,0x7d,0x93,0xd8,0x58,0x2c,0xce,0xfe,0x24,0xaf,0xde,0xb8,0x36,
    0xc8,0xa1,0x80,0xa6,0x99,0x98,0xa8,0x2f,0x0e,0x81,0x65,0x73,0xe4,0xc2,0xa2,0x8a,
    0xd4,0xe1,0x11,0xd0,0x08,0x8b,0x2a,0xf2,0xed,0x9a,0x64,0x3f,0xc1,0x6c,0xf9,0xec,
))
_HIGH1 = bytes((
    0x41,0x36,0x13,0x62,0xa8,0x21,0x6e,0xbb,0xf4,0x16,0xcc,0x04,0x7f,0x64,0xe8,0x5d,
    0x1e,0xf2,0xcb,0x2a,0x74,0xc5,0x5e,0x35,0xd2,0x95,0x47,0x9e,0x96,0x2d,0x9a,0x88,
    0x4c,0x7d,0x84,0x3f,0xdb,0xac,0x31,0xb6,0x48,0x5f,0xf6,0xc4,0xd8,0x39,0x8b,0xe7,
    0x23,0x3b,0x38,0x8e,0xc8,0xc1,0xdf,0x25,0xb1,0x20,0xa5,0x46,0x60,0x4e,0x9c,0xfb,
    0xaa,0xd3,0x56,0x51,0x45,0x7c,0x55,0x00,0x07,0xc9,0x2b,0x9d,0x85,0x9b,0x09,0xa0,
    0x8f,0xad,0xb3,0x0f,0x63,0xab,0x89,0x4b,0xd7,0xa7,0x15,0x5a,0x71,0x66,0x42,0xbf,
    0x26,0x4a,0x6b,0x98,0xfa,0xea,0x77,0x53,0xb2,0x70,0x05,0x2c,0xfd,0x59,0x3a,0x86,
    0x7e,0xce,0x06,0xeb,0x82,0x78,0x57,0xc7,0x8d,0x43,0xaf,0xb4,0x1c,0xd4,0x5b,0xcd,
    0xe2,0xe9,0x27,0x4f,0xc3,0x08,0x72,0x80,0xcf,0xb0,0xef,0xf5,0x28,0x6d,0xbe,0x30,
    0x4d,0x34,0x92,0xd5,0x0e,0x3c,0x22,0x32,0xe5,0xe4,0xf9,0x9f,0xc2,0xd1,0x0a,0x81,
    0x12,0xe1,0xee,0x91,0x83,0x76,0xe3,0x97,0xe6,0x61,0x8a,0x17,0x79,0xa4,0xb7,0xdc,
    0x90,0x7a,0x5c,0x8c,0x02,0xa6,0xca,0x69,0xde,0x50,0x1a,0x11,0x93,0xb9,0x52,0x87,
    0x58,0xfc,0xed,0x1d,0x37,0x49,0x1b,0x6a,0xe0,0x29,0x33,0x99,0xbd,0x6c,0xd9,0x94,
    0xf3,0x40,0x54,0x6f,0xf0,0xc6,0x73,0xb8,0xd6,0x3e,0x65,0x18,0x44,0x1f,0xdd,0x67,
    0x10,0xf1,0x0c,0x19,0xec,0xae,0x03,0xa1,0x14,0x7b,0xa9,0x0b,0xff,0xf8,0xa3,0xc0,
    0xa2,0x01,0xf7,0x2e,0xbc,0x24,0x68,0x75,0x0d,0xfe,0xba,0x2f,0xb5,0xd0,0xda,0x3d,
))
_HIGH2 = bytes((
    0x14,0x53,0x0f,0x56,0xb3,0xc8,0x7a,0x9c,0xeb,0x65,0x48,0x17,0x16,0x15,0x9f,0x02,
    0xcc,0x54,0x7c,0x83,0x00,0x0d,0x0c,0x0b,0xa2,0x62,0xa8,0x76,0xdb,0xd9,0xed,0xc7,
    0xc5,0xa4,0xdc,0xac,0x85,0x74,0xd6,0xd0,0xa7,0x9b,0xae,0x9a,0x96,0x71,0x66,0xc3,
    0x63,0x99,0xb8,0xdd,0x73,0x92,0x8e,0x84,0x7d,0xa5,0x5e,0xd1,0x5d,0x93,0xb1,0x57,
    0x51,0x50,0x80,0x89,0x52,0x94,0x4f,0x4e,0x0a,0x6b,0xbc,0x8d,0x7f,0x6e,0x47,0x46,
    0x41,0x40,0x44,0x01,0x11,0xcb,0x03,0x3f,0xf7,0xf4,0xe1,0xa9,0x8f,0x3c,0x3a,0xf9,
    0xfb,0xf0,0x19,0x30,0x82,0x09,0x2e,0xc9,0x9d,0xa0,0x86,0x49,0xee,0x6f,0x4d,0x6d,
    0xc4,0x2d,0x81,0x34,0x25,0x87,0x1b,0x88,0xaa,0xfc,0x06,0xa1,0x12,0x38,0xfd,0x4c,
    0x42,0x72,0x64,0x13,0x37,0x24,0x6a,0x75,0x77,0x43,0xff,0xe6,0xb4,0x4b,0x36,0x5c,
    0xe4,0xd8,0x35,0x3d,0x45,0xb9,0x2c,0xec,0xb7,0x31,0x2b,0x29,0x07,0x68,0xa3,0x0e,
    0x69,0x7b,0x18,0x9e,0x21,0x39,0xbe,0x28,0x1a,0x5b,0x78,0xf5,0x23,0xca,0x2a,0xb0,
    0xaf,0x3e,0xfe,0x04,0x8c,0xe7,0xe5,0x98,0x32,0x95,0xd3,0xf6,0x4a,0xe8,0xa6,0xea,
    0xe9,0xf3,0xd5,0x2f,0x70,0x20,0xf2,0x1f,0x05,0x67,0xad,0x55,0x10,0xce,0xcd,0xe3,
    0x27,0x3b,0xda,0xba,0xd7,0xc2,0x26,0xd4,0x91,0x1d,0xd2,0x1c,0x22,0x33,0xf8,0xfa,
    0xf1,0x5a,0xef,0xcf,0x90,0xb6,0x8b,0xb5,0xbd,0xc0,0xbf,0x08,0x97,0x1e,0x6c,0xe2,
    0x61,0xe0,0xc6,0xc1,0x59,0xab,0xbb,0x58,0xde,0x5f,0xdf,0x60,0x79,0x7e,0xb2,0x8a,
))
for _t in (_COMPRESSIBLE, _HIGH1, _HIGH2):
    assert len(_t) == 256 and sorted(_t) == list(range(256)), "bad PST crypt table"

CRYPT_NONE, CRYPT_PERMUTE, CRYPT_CYCLIC = 0, 1, 2


def _decrypt(data: bytes, method: int, key: int) -> bytes:
    if method == CRYPT_NONE or not data:
        return data
    out = bytearray(len(data))
    if method == CRYPT_PERMUTE:
        tbl = _COMPRESSIBLE
        for i, b in enumerate(data):
            out[i] = tbl[b]
        return bytes(out)
    # CRYPT_CYCLIC
    salt = ((key >> 16) ^ (key & 0xFFFF)) & 0xFFFF
    for i, b in enumerate(data):
        lo = salt & 0xFF
        hi = (salt >> 8) & 0xFF
        x = (b + lo) & 0xFF
        x = _HIGH1[x]
        x = (x + hi) & 0xFF
        x = _HIGH2[x]
        x = (x - hi) & 0xFF
        x = _COMPRESSIBLE[x]
        x = (x - lo) & 0xFF
        out[i] = x
        salt = (salt + 1) & 0xFFFF
    return bytes(out)


# --------------------------------------------------------------------------- #
# Small binary helpers
# --------------------------------------------------------------------------- #
_u16 = struct.Struct("<H").unpack_from
_u32 = struct.Struct("<I").unpack_from
_u64 = struct.Struct("<Q").unpack_from


def u16(b, o=0) -> int:
    return _u16(b, o)[0]


def u32(b, o=0) -> int:
    return _u32(b, o)[0]


def u64(b, o=0) -> int:
    return _u64(b, o)[0]


_FILETIME_EPOCH = datetime(1601, 1, 1, tzinfo=timezone.utc)


def filetime_to_dt(ft: int) -> datetime | None:
    if ft <= 0:
        return None
    try:
        return _FILETIME_EPOCH + timedelta(microseconds=ft // 10)
    except (OverflowError, OSError):
        return None


class PstFormatError(Exception):
    pass


# NID types
NID_TYPE_NORMAL_FOLDER = 0x02
NID_TYPE_NORMAL_MESSAGE = 0x04
NID_TYPE_ATTACHMENT = 0x05
NID_TYPE_CONTENTS_TABLE = 0x0E
NID_TYPE_HIERARCHY_TABLE = 0x0D
NID_TYPE_ASSOC_CONTENTS_TABLE = 0x0F

NID_ROOT_FOLDER = 0x122
NID_MESSAGE_STORE = 0x21
NID_ATTACHMENT_TABLE = 0x671
NID_RECIPIENT_TABLE = 0x692

# Property tags we care about
PID_DISPLAY_NAME = 0x3001
PID_SUBJECT = 0x0037
PID_SENDER_NAME = 0x0C1A
PID_SENT_REPR_NAME = 0x0042
PID_SENDER_EMAIL = 0x0C1F
PID_SENT_REPR_EMAIL = 0x0065
PID_DISPLAY_TO = 0x0E04
PID_DISPLAY_CC = 0x0E03
PID_CLIENT_SUBMIT_TIME = 0x0039
PID_DELIVERY_TIME = 0x0E06
PID_BODY = 0x1000
PID_HTML = 0x1013
PID_RTF_COMPRESSED = 0x1009
PID_TRANSPORT_HEADERS = 0x007D
PID_ATTACH_DATA_BIN = 0x3701
PID_ATTACH_LONG_FILENAME = 0x3707
PID_ATTACH_FILENAME = 0x3704
PID_ATTACH_MIME_TAG = 0x370E
PID_ATTACH_CONTENT_ID = 0x3712
PID_ATTACH_METHOD = 0x3705
PID_EMAIL_ADDRESS = 0x3003
PID_SMTP_ADDRESS = 0x39FE
PID_RECIPIENT_TYPE = 0x0C15
PID_CONTENT_COUNT = 0x3602


# --------------------------------------------------------------------------- #
# NDB layer
# --------------------------------------------------------------------------- #
class _BBTEntry:
    __slots__ = ("ib", "cb")

    def __init__(self, ib: int, cb: int) -> None:
        self.ib = ib
        self.cb = cb


class NDB:
    """Node & block database: parses the two BTrees and reads/decrypts blocks."""

    def __init__(self, path: str) -> None:
        self._f = open(path, "rb")
        try:
            self._mm = mmap.mmap(self._f.fileno(), 0, access=mmap.ACCESS_READ)
        except (ValueError, OSError):
            self._mm = self._f.read()  # tiny/empty file fallback

        data = self._mm
        if len(data) < 564 or data[:4] != b"!BDN":
            raise PstFormatError("not a PST/OST file (missing !BDN signature)")
        ver = u16(data, 0x0A)
        if ver < 23:
            raise PstFormatError(
                "this is an ANSI (Outlook 97-2002) PST; only Unicode PST/OST is supported"
            )
        if ver >= 36:
            raise PstFormatError("Unicode4K PST variant is not supported")

        self.crypt = data[0x0201]
        if self.crypt not in (CRYPT_NONE, CRYPT_PERMUTE, CRYPT_CYCLIC):
            raise PstFormatError(f"unknown encryption method {self.crypt}")

        nbt_bref_off = 0x00B4 + 0x24
        bbt_bref_off = 0x00B4 + 0x34
        nbt_root = (u64(data, nbt_bref_off), u64(data, nbt_bref_off + 8))
        bbt_root = (u64(data, bbt_bref_off), u64(data, bbt_bref_off + 8))
        self._file_eof = u64(data, 0x00B8)

        self.bbt: dict[int, _BBTEntry] = {}
        self.nbt: dict[int, tuple[int, int, int]] = {}  # nid -> (bidData, bidSub, nidParent)
        self._walk_bbt(bbt_root[1])
        self._walk_nbt(nbt_root[1])

    def close(self) -> None:
        try:
            if isinstance(self._mm, mmap.mmap):
                self._mm.close()
        finally:
            self._f.close()

    # -- BTree page walking ------------------------------------------- #
    def _page(self, ib: int) -> memoryview:
        return memoryview(self._mm)[ib : ib + 512]

    def _walk_bbt(self, ib: int, depth: int = 0) -> None:
        if depth > 32:
            return
        page = self._page(ib)
        c_ent = page[488]
        cb_ent = page[490]
        c_level = page[491]
        for i in range(c_ent):
            e = page[i * cb_ent : (i + 1) * cb_ent]
            if len(e) < cb_ent:
                break
            if c_level > 0:  # BTENTRY: btkey(8) BREF(bid8 ib8)
                child_ib = u64(e, 16)
                self._walk_bbt(child_ib, depth + 1)
            else:  # BBTENTRY: BREF(bid8 ib8) cb(2) cRef(2) pad(4)
                bid = u64(e, 0) & ~1
                self.bbt[bid] = _BBTEntry(u64(e, 8), u16(e, 16))

    def _walk_nbt(self, ib: int, depth: int = 0) -> None:
        if depth > 32:
            return
        page = self._page(ib)
        c_ent = page[488]
        cb_ent = page[490]
        c_level = page[491]
        for i in range(c_ent):
            e = page[i * cb_ent : (i + 1) * cb_ent]
            if len(e) < cb_ent:
                break
            if c_level > 0:  # BTENTRY
                self._walk_nbt(u64(e, 16), depth + 1)
            else:  # NBTENTRY: nid(8) bidData(8) bidSub(8) nidParent(4) pad(4)
                nid = u64(e, 0) & 0xFFFFFFFF
                self.nbt[nid] = (u64(e, 8), u64(e, 16), u32(e, 24))

    # -- blocks ---------------------------------------------------- #
    def _raw_block(self, bid: int) -> bytes:
        entry = self.bbt.get(bid & ~1)
        if entry is None:
            raise PstFormatError(f"block {bid:#x} not found in BBT")
        return bytes(self._mm[entry.ib : entry.ib + entry.cb])

    def read_data_blocks(self, bid: int) -> list[bytes]:
        """Return the ordered list of leaf data blocks for a data BID.

        Leaf blocks are decrypted; XBLOCK/XXBLOCK index blocks (bid bit 0x02)
        are followed but never decrypted.
        """

        if bid == 0:
            return []
        if bid & 0x02:  # internal: XBLOCK or XXBLOCK
            raw = self._raw_block(bid)
            if not raw or raw[0] != 0x01:
                return []
            c_ent = u16(raw, 2)
            out: list[bytes] = []
            for i in range(c_ent):
                sub = u64(raw, 8 + i * 8)
                out.extend(self.read_data_blocks(sub))
            return out
        raw = self._raw_block(bid)
        return [_decrypt(raw, self.crypt, bid & 0xFFFFFFFF)]

    def read_subnodes(self, bid: int) -> dict[int, tuple[int, int]]:
        """Parse a subnode BTree -> {sub_nid: (bidData, bidSub)}."""

        result: dict[int, tuple[int, int]] = {}
        if bid == 0:
            return result
        self._read_subnode_block(bid, result, 0)
        return result

    def _read_subnode_block(self, bid: int, out: dict, depth: int) -> None:
        if depth > 32 or bid == 0:
            return
        raw = self._raw_block(bid)
        if len(raw) < 8 or raw[0] != 0x02:
            return
        c_level = raw[1]
        c_ent = u16(raw, 2)
        base = 8
        if c_level == 0:  # SLBLOCK: SLENTRY nid(8) bidData(8) bidSub(8)
            for i in range(c_ent):
                o = base + i * 24
                if o + 24 > len(raw):
                    break
                nid = u64(raw, o) & 0xFFFFFFFF
                out[nid] = (u64(raw, o + 8), u64(raw, o + 16))
        else:  # SIBLOCK: SIENTRY nid(8) bid(8) -> child SLBLOCK
            for i in range(c_ent):
                o = base + i * 16
                if o + 16 > len(raw):
                    break
                self._read_subnode_block(u64(raw, o + 8), out, depth + 1)


# --------------------------------------------------------------------------- #
# LTP layer: Heap-on-Node, BTree-on-Heap, Property/Table Contexts
# --------------------------------------------------------------------------- #
class _HN:
    """Heap-on-Node: resolves HIDs against a node's list of data blocks."""

    def __init__(self, blocks: list[bytes]) -> None:
        self.blocks = blocks
        self.client_sig = 0
        self.user_root = 0
        self._maps: list[list[int]] = []
        for idx, blk in enumerate(blocks):
            if idx == 0:
                if len(blk) < 12 or blk[2] != 0xEC:
                    raise PstFormatError("bad HNHDR signature")
                self.client_sig = blk[3]
                self.user_root = u32(blk, 4)
                ib_pm = u16(blk, 0)
            else:
                ib_pm = u16(blk, 0)  # HNPAGEHDR / HNBITMAPHDR both start with ibHnpm
            self._maps.append(self._parse_pagemap(blk, ib_pm))

    @staticmethod
    def _parse_pagemap(blk: bytes, ib_pm: int) -> list[int]:
        if ib_pm + 4 > len(blk):
            return []
        c_alloc = u16(blk, ib_pm)
        offs = []
        p = ib_pm + 4
        for _ in range(c_alloc + 1):
            if p + 2 > len(blk):
                break
            offs.append(u16(blk, p))
            p += 2
        return offs

    def get(self, hid: int) -> bytes:
        if hid == 0 or (hid & 0x1F) != 0:  # 0 or a NID, not an HID
            return b""
        index = (hid >> 5) & 0x7FF
        block_index = (hid >> 16) & 0xFFFF
        if block_index >= len(self.blocks):
            return b""
        offs = self._maps[block_index]
        if index < 1 or index >= len(offs):
            return b""
        blk = self.blocks[block_index]
        return blk[offs[index - 1] : offs[index]]


def _bth_records(hn: _HN, hid_root: int, cb_key: int, cb_ent: int, levels: int) -> Iterator[bytes]:
    """Yield fixed-size (cb_key + cb_ent) leaf records of a BTree-on-Heap."""

    if hid_root == 0:
        return
    if levels == 0:
        buf = hn.get(hid_root)
        rec = cb_key + cb_ent
        for o in range(0, len(buf) - rec + 1, rec):
            yield buf[o : o + rec]
        return
    buf = hn.get(hid_root)
    step = cb_key + 4
    for o in range(0, len(buf) - step + 1, step):
        child = u32(buf, o + cb_key)
        yield from _bth_records(hn, child, cb_key, cb_ent, levels - 1)


# Property-type -> fixed byte width when stored inline (<= 4 => in the HNID slot)
_FIXED_WIDTH = {
    0x0002: 2, 0x0003: 4, 0x000A: 4, 0x000B: 2, 0x0004: 4,
    0x0005: 8, 0x0006: 8, 0x0007: 8, 0x0014: 8, 0x0040: 8,
}


class PC:
    """Property Context: {prop_id: python value}."""

    def __init__(self, ndb: NDB, blocks: list[bytes], subnodes: dict[int, tuple[int, int]]):
        self._ndb = ndb
        self._sub = subnodes
        self.hn = _HN(blocks)
        if self.hn.client_sig != 0xBC:
            raise PstFormatError(f"not a PC (client sig {self.hn.client_sig:#x})")
        hdr = self.hn.get(self.hn.user_root)
        if len(hdr) < 8 or hdr[0] != 0xB5:
            raise PstFormatError("bad BTHHEADER in PC")
        self._props: dict[int, tuple[int, int]] = {}
        for rec in _bth_records(self.hn, u32(hdr, 4), hdr[1], hdr[2], hdr[3]):
            if len(rec) >= 8:
                self._props[u16(rec, 0)] = (u16(rec, 2), u32(rec, 4))

    # -- raw resolution -------------------------------------------- #
    def _bytes_for(self, prop_type: int, hnid: int) -> bytes:
        if (hnid & 0x1F) == 0:  # HID (or 0)
            return self.hn.get(hnid)
        # NID into this node's subnode tree
        ref = self._sub.get(hnid & 0xFFFFFFFF)
        if ref is None:
            return b""
        blocks = self._ndb.read_data_blocks(ref[0])
        return b"".join(blocks)

    def get(self, prop_id: int):
        entry = self._props.get(prop_id)
        if entry is None:
            return None
        ptype, val = entry
        if ptype in _FIXED_WIDTH and _FIXED_WIDTH[ptype] <= 4:
            if ptype == 0x000B:
                return bool(val & 0xFF)
            if ptype == 0x0002:
                return val - 0x10000 if val & 0x8000 else val
            return val  # PT_LONG etc.
        raw = self._bytes_for(ptype, val)
        if ptype == 0x0003:
            return u32(raw) if len(raw) >= 4 else None
        if ptype in (0x0014, 0x0006):  # I8
            return u64(raw) if len(raw) >= 8 else None
        if ptype == 0x0040:  # PT_SYSTIME
            return filetime_to_dt(u64(raw)) if len(raw) >= 8 else None
        if ptype == 0x001F:  # PT_UNICODE
            return raw.decode("utf-16-le", "replace")
        if ptype == 0x001E:  # PT_STRING8
            return raw.decode("cp1252", "replace")
        if ptype in (0x0102, 0x000D):  # PT_BINARY / PT_OBJECT
            return raw
        return raw or None

    def get_str(self, *prop_ids: int) -> str:
        for pid in prop_ids:
            v = self.get(pid)
            if isinstance(v, str) and v:
                return v
            if isinstance(v, bytes) and v:
                return v.decode("utf-8", "replace")
        return ""


class TC:
    """Table Context: list of row dicts {prop_id: value}."""

    def __init__(self, ndb: NDB, blocks: list[bytes], subnodes: dict[int, tuple[int, int]]):
        self._ndb = ndb
        self._sub = subnodes
        self.hn = _HN(blocks)
        if self.hn.client_sig != 0x7C:
            raise PstFormatError(f"not a TC (client sig {self.hn.client_sig:#x})")
        info = self.hn.get(self.hn.user_root)
        if len(info) < 0x16 or info[0] != 0x7C:
            raise PstFormatError("bad TCINFO")
        n_cols = info[1]
        self._row_size = u16(info, 8)              # rgib[TCI_bm]
        self._ceb_off = u16(info, 6)              # rgib[TCI_1b] -> start of cell-existence bitmap
        hnid_rows = u32(info, 0x0E)
        self._cols: list[tuple[int, int, int, int, int]] = []  # (pid, ptype, ib, cb, ibit)
        for i in range(n_cols):
            o = 0x16 + i * 8
            tag = u32(info, o)
            self._cols.append((tag >> 16, tag & 0xFFFF, u16(info, o + 4), info[o + 6], info[o + 7]))
        # Row matrix, kept as a list of blocks: rows never span a block, and each
        # block holds floor(len(block) / row_size) rows.
        self._row_blocks: list[bytes] = self._load_row_matrix(hnid_rows)

    def _load_row_matrix(self, hnid: int) -> list[bytes]:
        if hnid == 0:
            return []
        if (hnid & 0x1F) == 0:
            return [self.hn.get(hnid)]
        ref = self._sub.get(hnid & 0xFFFFFFFF)
        if ref is None:
            return []
        return self._ndb.read_data_blocks(ref[0])

    @property
    def row_count(self) -> int:
        """Number of rows without decoding any of them.

        Matches what :meth:`__iter__` yields (see :func:`_iter_rows`): each block
        holds ``floor(len(block) / row_size)`` whole rows.
        """

        rs = self._row_size
        if rs <= 0:
            return 0
        return sum(len(seg) // rs for seg in self._row_blocks)

    def __iter__(self) -> Iterator[dict]:
        rs = self._row_size
        if rs <= 0:
            return
        n_cols = len(self._cols)
        ceb_len = (n_cols + 7) // 8
        for row in _iter_rows(self._row_blocks, rs):
            if len(row) < rs:
                continue
            ceb = row[self._ceb_off : self._ceb_off + ceb_len]
            out = {"_rowid": u32(row, 0)}
            for ci, (pid, ptype, ib, cb, ibit) in enumerate(self._cols):
                if ib + cb > len(row):
                    continue
                if ceb and ci < len(ceb) * 8 and not (ceb[ci >> 3] & (0x80 >> (ci & 7))):
                    continue
                cell = row[ib : ib + cb]
                out[pid] = self._decode_cell(ptype, cell)
            yield out

    def _decode_cell(self, ptype: int, cell: bytes):
        if ptype == 0x000B:
            return bool(cell[0]) if cell else False
        if ptype == 0x0002:
            v = u16(cell) if len(cell) >= 2 else 0
            return v - 0x10000 if v & 0x8000 else v
        if ptype in (0x0003, 0x000A, 0x0004):
            return u32(cell) if len(cell) >= 4 else 0
        if ptype in (0x0014, 0x0006, 0x0005, 0x0007):
            return u64(cell) if len(cell) >= 8 else 0
        if ptype == 0x0040:
            return filetime_to_dt(u64(cell)) if len(cell) >= 8 else None
        # variable-width: cell holds an HNID
        hnid = u32(cell) if len(cell) >= 4 else 0
        raw = self._var(hnid)
        if ptype == 0x001F:
            return raw.decode("utf-16-le", "replace")
        if ptype == 0x001E:
            return raw.decode("cp1252", "replace")
        return raw

    def _var(self, hnid: int) -> bytes:
        if hnid == 0:
            return b""
        if (hnid & 0x1F) == 0:
            return self.hn.get(hnid)
        ref = self._sub.get(hnid & 0xFFFFFFFF)
        if ref is None:
            return b""
        return b"".join(self._ndb.read_data_blocks(ref[0]))


def _iter_rows(blocks: list[bytes], row_size: int) -> Iterator[bytes]:
    """Yield rows: each block holds floor(len(block) / row_size) whole rows."""

    for seg in blocks:
        n = len(seg) // row_size
        for i in range(n):
            yield seg[i * row_size : (i + 1) * row_size]


# --------------------------------------------------------------------------- #
# Messaging layer
# --------------------------------------------------------------------------- #
class NativeFolder:
    __slots__ = ("nid", "name", "child_nids", "message_count")

    def __init__(self, nid: int, name: str, child_nids: list[int], message_count: int):
        self.nid = nid
        self.name = name
        self.child_nids = child_nids
        self.message_count = message_count


class PstFile:
    def __init__(self, path: str) -> None:
        self.ndb = NDB(path)
        self.path = path

    def close(self) -> None:
        self.ndb.close()

    # -- node helpers -------------------------------------------- #
    def _node_blocks_and_subs(self, nid: int):
        ref = self.ndb.nbt.get(nid & 0xFFFFFFFF)
        if ref is None:
            raise PstFormatError(f"node {nid:#x} not in NBT")
        blocks = self.ndb.read_data_blocks(ref[0])
        subs = self.ndb.read_subnodes(ref[1])
        return blocks, subs

    def _pc(self, nid: int) -> PC:
        blocks, subs = self._node_blocks_and_subs(nid)
        return PC(self.ndb, blocks, subs)

    def _tc_from_nid(self, nid: int) -> TC:
        blocks, subs = self._node_blocks_and_subs(nid)
        return TC(self.ndb, blocks, subs)

    def _tc_from_sub(self, parent_subs: dict, sub_nid: int) -> TC | None:
        ref = parent_subs.get(sub_nid)
        if ref is None:
            # some stores key the table under a type-only nid
            for k, v in parent_subs.items():
                if (k & 0x1F) == (sub_nid & 0x1F):
                    ref = v
                    break
        if ref is None:
            return None
        blocks = self.ndb.read_data_blocks(ref[0])
        subs = self.ndb.read_subnodes(ref[1])
        return TC(self.ndb, blocks, subs)

    # -- folders ------------------------------------------------ #
    def root_folder_nid(self) -> int:
        return NID_ROOT_FOLDER

    def folder(self, nid: int) -> NativeFolder:
        nid_index = nid >> 5
        try:
            pc = self._pc(nid)
            name = pc.get_str(PID_DISPLAY_NAME)
        except PstFormatError:
            name = ""
        child_nids: list[int] = []
        msg_count = 0
        hier_nid = (nid_index << 5) | NID_TYPE_HIERARCHY_TABLE
        try:
            tc = self._tc_from_nid(hier_nid)
            for row in tc:
                child = row.get("_rowid", 0)
                if child:
                    child_nids.append(child)
        except (PstFormatError, KeyError):
            pass
        cont_nid = (nid_index << 5) | NID_TYPE_CONTENTS_TABLE
        try:
            # row_count reads block sizes only - no per-row cell decoding, which
            # matters when opening a PST with many large folders.
            msg_count = self._tc_from_nid(cont_nid).row_count
        except (PstFormatError, KeyError):
            msg_count = 0
        return NativeFolder(nid, name or ("Top of Outlook data file" if nid == NID_ROOT_FOLDER else ""),
                            child_nids, msg_count)

    def folder_contents(self, nid: int) -> list[dict]:
        nid_index = nid >> 5
        cont_nid = (nid_index << 5) | NID_TYPE_CONTENTS_TABLE
        try:
            return list(self._tc_from_nid(cont_nid))
        except (PstFormatError, KeyError):
            return []

    # -- messages --------------------------------------------- #
    def message(self, nid: int) -> dict:
        blocks, subs = self._node_blocks_and_subs(nid)
        pc = PC(self.ndb, blocks, subs)

        out: dict = {
            "subject": _clean_subject(pc.get_str(PID_SUBJECT)),
            "sender": pc.get_str(PID_SENT_REPR_NAME, PID_SENDER_NAME,
                                 PID_SENT_REPR_EMAIL, PID_SENDER_EMAIL),
            "to": _split(pc.get_str(PID_DISPLAY_TO)),
            "cc": _split(pc.get_str(PID_DISPLAY_CC)),
            "date": pc.get(PID_DELIVERY_TIME) or pc.get(PID_CLIENT_SUBMIT_TIME),
            "headers": _parse_headers(pc.get_str(PID_TRANSPORT_HEADERS)),
            "body_text": pc.get_str(PID_BODY) or None,
            "body_html": None,
            "attachments": [],
        }
        html = pc.get(PID_HTML)
        if isinstance(html, bytes) and html:
            for enc in ("utf-8", "cp1252", "latin-1"):
                try:
                    out["body_html"] = html.decode(enc)
                    break
                except UnicodeDecodeError:
                    continue
        if not out["body_html"]:
            rtf = pc.get(PID_RTF_COMPRESSED)
            if isinstance(rtf, bytes) and rtf:
                out["body_html"] = _rtf_html(rtf)

        # recipients (fallback for to/cc, and better names)
        if not out["to"] and not out["cc"]:
            rtc = self._tc_from_sub(subs, NID_RECIPIENT_TABLE)
            if rtc is not None:
                for row in rtc:
                    disp = _row_str(row, PID_DISPLAY_NAME) or _row_str(row, PID_SMTP_ADDRESS) \
                        or _row_str(row, PID_EMAIL_ADDRESS)
                    rtype = row.get(PID_RECIPIENT_TYPE, 1)
                    if not disp:
                        continue
                    (out["cc"] if rtype == 2 else out["to"]).append(disp)

        # attachments
        atc = self._tc_from_sub(subs, NID_ATTACHMENT_TABLE)
        if atc is not None:
            for row in atc:
                att_nid = row.get("_rowid", 0)
                if not att_nid:
                    continue
                try:
                    out["attachments"].append(self._attachment(subs, att_nid))
                except PstFormatError:
                    continue
        return out

    def _attachment(self, msg_subs: dict, att_nid: int) -> dict:
        ref = msg_subs.get(att_nid) or msg_subs.get(att_nid & 0xFFFFFFFF)
        if ref is None:
            raise PstFormatError("attachment subnode missing")
        blocks = self.ndb.read_data_blocks(ref[0])
        subs = self.ndb.read_subnodes(ref[1])
        pc = PC(self.ndb, blocks, subs)
        name = pc.get_str(PID_ATTACH_LONG_FILENAME, PID_ATTACH_FILENAME) or "attachment"
        data = pc.get(PID_ATTACH_DATA_BIN)
        if not isinstance(data, (bytes, bytearray)):
            data = b""
        cid = pc.get_str(PID_ATTACH_CONTENT_ID)
        mime = pc.get_str(PID_ATTACH_MIME_TAG) or "application/octet-stream"
        return {
            "filename": name,
            "data": bytes(data),
            "mime_type": mime,
            "content_id": cid.strip("<>") if cid else None,
        }


# --------------------------------------------------------------------------- #
# tiny text helpers
# --------------------------------------------------------------------------- #
def _split(value: str) -> list[str]:
    if not value:
        return []
    return [c.strip() for c in value.replace(",", ";").split(";") if c.strip()]


def _row_str(row: dict, pid: int) -> str:
    v = row.get(pid)
    if isinstance(v, str):
        return v
    if isinstance(v, bytes):
        return v.decode("utf-8", "replace")
    return ""


def _clean_subject(subj: str) -> str:
    # PST subjects sometimes carry a 2-char control prefix (0x01 <lcp>).
    if subj and ord(subj[0]) == 0x01 and len(subj) >= 2:
        return subj[2:]
    return subj


def _parse_headers(text: str) -> dict[str, str]:
    if not text:
        return {}
    try:
        import email

        return {k: str(v) for k, v in email.message_from_string(text).items()}
    except Exception:
        return {}


def _rtf_html(compressed: bytes) -> str | None:
    try:
        from parsers._rtf import decompress_rtf

        rtf = decompress_rtf(compressed)
    except Exception:
        return None
    try:
        from striprtf.striprtf import rtf_to_text

        text = rtf_to_text(rtf.decode("latin-1", "replace"))
    except Exception:
        return None
    if not text.strip():
        return None
    import html as _h

    return f"<pre style='white-space:pre-wrap;font-family:inherit'>{_h.escape(text)}</pre>"


# --------------------------------------------------------------------------- #
# Diagnostic CLI:  python -m parsers.pst_native <file.pst> [--dump-first]
# --------------------------------------------------------------------------- #
def _diagnostic(path: str, dump_first: bool) -> int:
    import sys as _sys
    import traceback

    try:
        pst = PstFile(path)
    except Exception as exc:  # noqa: BLE001
        print(f"OPEN FAILED: {type(exc).__name__}: {exc}")
        traceback.print_exc()
        return 2

    print(f"opened OK   crypt={pst.ndb.crypt}   nodes={len(pst.ndb.nbt)}   blocks={len(pst.ndb.bbt)}")

    total_msgs = 0
    first_msg_nid = None
    first_folder = None

    def walk(nid: int, depth: int) -> None:
        nonlocal total_msgs, first_msg_nid, first_folder
        try:
            f = pst.folder(nid)
        except Exception as exc:  # noqa: BLE001
            print("  " * depth + f"[folder {nid:#x} FAILED: {exc}]")
            return
        print("  " * depth + f"- {f.name!r}  ({f.message_count} messages)  nid={nid:#x}")
        total_msgs += f.message_count
        if f.message_count and first_msg_nid is None:
            rows = pst.folder_contents(nid)
            if rows:
                first_msg_nid = rows[0].get("_rowid")
                first_folder = nid
        for child in f.child_nids:
            walk(child, depth + 1)

    walk(pst.root_folder_nid(), 0)
    print(f"\ntotal messages across tree: {total_msgs}")

    if dump_first and first_msg_nid:
        print(f"\n--- first message ({first_msg_nid:#x}) ---")
        try:
            m = pst.message(first_msg_nid)
            for k in ("subject", "sender", "to", "cc", "date"):
                print(f"  {k}: {m[k]!r}")
            print(f"  body_text: {(m['body_text'] or '')[:200]!r}")
            print(f"  body_html length: {len(m['body_html'] or '')}")
            print(f"  attachments: {[(a['filename'], len(a['data'])) for a in m['attachments']]}")
        except Exception as exc:  # noqa: BLE001
            print(f"  MESSAGE FAILED: {type(exc).__name__}: {exc}")
            traceback.print_exc()
            return 3

    pst.close()
    return 0


if __name__ == "__main__":
    import sys

    args = [a for a in sys.argv[1:] if a != "--dump-first"]
    if not args:
        print("usage: python -m parsers.pst_native <file.pst|file.ost> [--dump-first]")
        raise SystemExit(1)
    raise SystemExit(_diagnostic(args[0], "--dump-first" in sys.argv))
