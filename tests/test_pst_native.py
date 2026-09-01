"""Unit tests for the isolated pieces of the pure-Python PST reader.

Full folder/message traversal needs a real Unicode .pst at
``tests/data/sample.pst`` (the round-trip test in ``test_pst_parser.py`` picks
it up automatically). These tests cover everything that can be checked without
one: the crypt tables, the LZFu decompressor, header validation, the Heap-on-Node
resolver and the BTree-on-Heap iterator.
"""

from __future__ import annotations

import struct

import pytest

from parsers import pst_native as pn
from parsers._rtf import decompress_rtf


# --------------------------------------------------------------------------- #
# String decoding (Turkish etc. was turning into boxes)
# --------------------------------------------------------------------------- #
_TURKISH = [
    "Bağlantısı Hatası hk.",
    "İLT: Acente Bağlantı Hatası",
    "Ağustos 2026 Pazartesi",
    "Toplantısı Katılım kimliği",
    "şğıışğıı",  # all-Turkish, no ASCII to anchor the heuristic
]


@pytest.mark.parametrize("text", _TURKISH)
def test_decode_pst_str_handles_every_byte_form(text):
    assert pn._decode_pst_str(text.encode("utf-16-le")) == text
    assert pn._decode_pst_str(text.encode("utf-8")) == text
    assert pn._decode_pst_str(text.encode("cp1254")) == text


def test_looks_utf16le_discriminates():
    assert pn._looks_utf16le("Merhaba dünya".encode("utf-16-le")) is True
    assert pn._looks_utf16le("şğıışğıı".encode("utf-16-le")) is True
    assert pn._looks_utf16le("Merhaba dünya".encode("utf-8")) is False
    assert pn._looks_utf16le(b"odd-length!") is False
    assert pn._looks_utf16le(b"") is False


def test_decode_pst_str_strips_trailing_nul_and_empty():
    assert pn._decode_pst_str(b"") == ""
    assert pn._decode_pst_str("Hi".encode("utf-16-le") + b"\x00\x00") == "Hi"


# --------------------------------------------------------------------------- #
# Crypt tables
# --------------------------------------------------------------------------- #
def test_crypt_tables_are_bijections() -> None:
    for tbl in (pn._COMPRESSIBLE, pn._HIGH1, pn._HIGH2):
        assert len(tbl) == 256
        assert sorted(tbl) == list(range(256))


def test_decrypt_none_is_passthrough() -> None:
    assert pn._decrypt(b"hello", pn.CRYPT_NONE, 0) == b"hello"


def test_decrypt_permute_is_table_substitution_and_reversible() -> None:
    plain = bytes(range(256))
    dec = pn._decrypt(plain, pn.CRYPT_PERMUTE, 0)
    assert dec != plain
    # Rebuild the inverse table and confirm it undoes the substitution.
    inv = bytearray(256)
    for i, v in enumerate(pn._COMPRESSIBLE):
        inv[v] = i
    assert bytes(inv[b] for b in dec) == plain


def test_decrypt_cyclic_is_deterministic() -> None:
    data = bytes(range(64))
    a = pn._decrypt(data, pn.CRYPT_CYCLIC, 0x1234ABCD)
    b = pn._decrypt(data, pn.CRYPT_CYCLIC, 0x1234ABCD)
    assert a == b and a != data


# --------------------------------------------------------------------------- #
# FILETIME + text helpers
# --------------------------------------------------------------------------- #
def test_filetime_to_dt() -> None:
    from datetime import datetime, timezone

    target = datetime(2021, 3, 5, 10, 30, tzinfo=timezone.utc)
    ticks = int((target - datetime(1601, 1, 1, tzinfo=timezone.utc)).total_seconds() * 10_000_000)
    dt = pn.filetime_to_dt(ticks)
    assert dt is not None and dt.replace(microsecond=0) == target
    assert pn.filetime_to_dt(0) is None


def test_clean_subject_strips_control_prefix() -> None:
    assert pn._clean_subject("\x01\x05Hello") == "Hello"
    assert pn._clean_subject("Normal") == "Normal"


def test_split_recipients() -> None:
    assert pn._split("a@x.com; b@y.com , c@z.com") == ["a@x.com", "b@y.com", "c@z.com"]
    assert pn._split("") == []


# --------------------------------------------------------------------------- #
# LZFu / compressed RTF
# --------------------------------------------------------------------------- #
def test_decompress_rtf_uncompressed_mela() -> None:
    payload = b"{\\rtf1 hello}"
    header = struct.pack("<IIII", len(payload) + 12, len(payload), 0x414C454D, 0)
    assert decompress_rtf(header + payload) == payload


def test_decompress_rtf_literals_only() -> None:
    # One flag byte = 0x00 -> the next 8 bytes are literals.
    body = b"\x00" + b"ABCDEFGH"
    header = struct.pack("<IIII", len(body) + 12, 8, 0x75465A4C, 0)
    assert decompress_rtf(header + body) == b"ABCDEFGH"


def test_decompress_rtf_backreference_into_preamble() -> None:
    # A reference with offset 0, length 6 copies "{\rtf1" + "\" from the preamble.
    token = (0 << 4) | (6 - 2)  # offset 0, length 6
    body = bytes([0b00000001, token >> 8, token & 0xFF])
    header = struct.pack("<IIII", len(body) + 12, 6, 0x75465A4C, 0)
    assert decompress_rtf(header + body) == b"{\\rtf1"


def test_decompress_rtf_bad_magic() -> None:
    header = struct.pack("<IIII", 12, 0, 0xDEADBEEF, 0)
    with pytest.raises(ValueError):
        decompress_rtf(header)


# --------------------------------------------------------------------------- #
# NDB header validation
# --------------------------------------------------------------------------- #
def _write(tmp_path, data: bytes):
    p = tmp_path / "x.pst"
    p.write_bytes(data)
    return str(p)


def test_ndb_rejects_non_pst(tmp_path) -> None:
    with pytest.raises(pn.PstFormatError):
        pn.NDB(_write(tmp_path, b"not a pst" * 100))


def test_ndb_rejects_ansi(tmp_path) -> None:
    buf = bytearray(600)
    buf[0:4] = b"!BDN"
    struct.pack_into("<H", buf, 0x0A, 14)  # ANSI version
    with pytest.raises(pn.PstFormatError) as ei:
        pn.NDB(_write(tmp_path, bytes(buf)))
    assert "ANSI" in str(ei.value)


# --------------------------------------------------------------------------- #
# Heap-on-Node + BTree-on-Heap
# --------------------------------------------------------------------------- #
def _make_hn_block(items: list[bytes], client_sig: int) -> bytes:
    """Build a single-block HN with an HNHDR + packed items + HNPAGEMAP."""

    body = bytearray()
    offsets = [12]  # data starts right after the 12-byte HNHDR
    body += b"\x00" * 12
    for it in items:
        body += it
        offsets.append(len(body))
    ib_pm = len(body)
    pm = bytearray()
    pm += struct.pack("<H", len(items))   # cAlloc
    pm += struct.pack("<H", 0)            # cFree
    for off in offsets:
        pm += struct.pack("<H", off)
    body += pm
    struct.pack_into("<H", body, 0, ib_pm)          # ibHnpm
    body[2] = 0xEC                                   # bSig
    body[3] = client_sig                             # bClientSig
    struct.pack_into("<I", body, 4, (1 << 5))        # hidUserRoot -> item index 1
    return bytes(body)


def test_hn_resolves_hids() -> None:
    blk = _make_hn_block([b"FIRST", b"second-item", b"3"], client_sig=0xBC)
    hn = pn._HN([blk])
    assert hn.client_sig == 0xBC
    assert hn.get((1 << 5)) == b"FIRST"
    assert hn.get((2 << 5)) == b"second-item"
    assert hn.get((3 << 5)) == b"3"
    assert hn.get(0) == b""
    assert hn.get(0x0000000D) == b""  # low bits set -> not an HID


def test_bth_records_flat() -> None:
    # A leaf BTH with cbKey=2, cbEnt=2, 3 records.
    recs = b"\x01\x00AA" b"\x02\x00BB" b"\x03\x00CC"
    blk = _make_hn_block([b"unused", recs], client_sig=0xBC)
    hn = pn._HN([blk])
    got = list(pn._bth_records(hn, (2 << 5), cb_key=2, cb_ent=2, levels=0))
    assert got == [b"\x01\x00AA", b"\x02\x00BB", b"\x03\x00CC"]


def test_tc_row_count_matches_block_math() -> None:
    # With no Row Index, row_count falls back to floor(len(block)/row_size) per block.
    tc = pn.TC.__new__(pn.TC)
    tc._ri_hid = 0
    tc._row_size = 10
    tc._row_blocks = [b"x" * 25, b"y" * 10, b"", b"z" * 9]
    assert tc.slot_count == 3  # 2 + 1 + 0 + 0
    assert tc.row_count == 3
    assert sum(1 for _ in pn._iter_rows(tc._row_blocks, tc._row_size)) == tc.row_count

    empty = pn.TC.__new__(pn.TC)
    empty._ri_hid = 0
    empty._row_size = 0
    empty._row_blocks = []
    assert empty.row_count == 0


def test_tc_row_index_skips_stale_matrix_slots() -> None:
    # A folder with a Row Index must enumerate only the rows it lists, in order,
    # ignoring stale/zeroed matrix slots that a linear walk would pick up.
    tc = pn.TC.__new__(pn.TC)
    tc._ri_hid = 0
    tc._row_size = 4
    # 5 matrix slots; only slots 0, 2 and 4 are live.
    tc._row_blocks = [b"AAAA" b"\x00\x00\x00\x00" b"CCCC" b"\x00\x00\x00\x00" b"EEEE"]
    tc._cols = []
    tc._ceb_off = 4
    tc._row_index = lambda: [4, 0, 2]  # Row Index order need not be matrix order

    rows = list(tc)
    assert [r["_rowid"] for r in rows] == [
        pn.u32(b"EEEE"), pn.u32(b"AAAA"), pn.u32(b"CCCC")
    ]
    assert tc.row_count == 3
    assert tc.slot_count == 5


def _tc_with_row_index(row_blocks: list[bytes], row_size: int, positions: list[int]) -> "pn.TC":
    """A TC whose Row Index reports *positions*, over the given row matrix."""

    tc = pn.TC.__new__(pn.TC)
    tc._ri_hid = 1  # non-zero so _row_index() runs
    tc._row_size = row_size
    tc._row_blocks = row_blocks
    tc._cols = []
    tc._ceb_off = row_size

    # BTHHEADER: btype=0xB5, cbKey=4, cbEnt=4, bIdxLevels=0, hidRoot=2
    header = struct.pack("<BBBBI", 0xB5, 4, 4, 0, 2)
    # Leaf records: {dwRowID: dwRowIndex}, 4+4 bytes each.
    leaf = b"".join(struct.pack("<II", p, p) for p in positions)

    class _FakeHN:
        def get(self, hid: int) -> bytes:
            return header if hid == 1 else leaf

    tc.hn = _FakeHN()
    return tc


def test_tc_ignores_row_index_that_outruns_the_matrix() -> None:
    # A store still being written (or with row blocks we failed to assemble) has
    # a Row Index naming rows whose bytes we do not hold. Honouring it would show
    # a handful of messages out of thousands, so the linear scan must win.
    tc = _tc_with_row_index([b"AAAABBBBCCCC"], 4, list(range(10)))  # 3 slots, 10 claimed

    assert tc._row_index() is None                  # index rejected as unreachable
    assert tc.slot_count == 3
    assert tc.row_count == 3
    assert [r["_rowid"] for r in tc] == [
        pn.u32(b"AAAA"), pn.u32(b"BBBB"), pn.u32(b"CCCC")
    ]


def test_tc_keeps_row_index_with_a_few_unreachable_rows() -> None:
    # A handful of unreachable positions is normal slack, not a broken matrix:
    # keep using the index and just skip what we cannot locate.
    tc = _tc_with_row_index([b"A" * 4 * 20], 4, [*range(19), 999])
    idx = tc._row_index()
    assert idx is not None and len(idx) == 20
    assert len(list(tc)) == 19  # the out-of-range one is dropped
