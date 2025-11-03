#!/usr/bin/env python3
# -*- coding: utf-8 -*-

import argparse
import io
import os
import struct
import sys
import zlib
import lzma
from math import ceil

# Optional compressors
_have_lz4 = False
_have_zstd = False
_have_lzo = False
try:
    import lz4.frame as lz4f
    _have_lz4 = True
except Exception:
    pass
try:
    import zstandard as zstd
    _have_zstd = True
except Exception:
    pass
try:
    import lzo as pylzo
    _have_lzo = True
except Exception:
    pass

# ---- Constants ----
MAGIC = 0x73717368

COMPRESSOR_GZIP = 1
COMPRESSOR_LZMA = 2
COMPRESSOR_LZO  = 3
COMPRESSOR_XZ   = 4
COMPRESSOR_LZ4  = 5
COMPRESSOR_ZSTD = 6

def u16(b, o): return struct.unpack_from("<H", b, o)[0]
def u32(b, o): return struct.unpack_from("<I", b, o)[0]
def u64(b, o): return struct.unpack_from("<Q", b, o)[0]

def human(n):
    for unit in ["B","KiB","MiB","GiB","TiB"]:
        if n < 1024 or unit == "TiB":
            return f"{n:.1f} {unit}"
        n /= 1024.0

def _safe_join(base, name):
    # 경로 탈출 방지: 이름 내 슬래시 제거/정규화
    name = name.replace("\\", "/").split("/")[-1]
    return os.path.join(base, name)

class Decompressor:
    def __init__(self, comp_id):
        self.comp_id = comp_id
        if comp_id not in (COMPRESSOR_GZIP, COMPRESSOR_XZ, COMPRESSOR_LZMA,
                           COMPRESSOR_LZO, COMPRESSOR_LZ4, COMPRESSOR_ZSTD):
            raise ValueError(f"Unknown compressor id {comp_id}")

        if comp_id == COMPRESSOR_LZ4 and not _have_lz4:
            raise RuntimeError("LZ4 image but python 'lz4' not installed. `pip install lz4`")
        if comp_id == COMPRESSOR_ZSTD and not _have_zstd:
            raise RuntimeError("ZSTD image but python 'zstandard' not installed. `pip install zstandard`")
        if comp_id == COMPRESSOR_LZO and not _have_lzo:
            raise RuntimeError("LZO image but 'python-lzo' not installed. `pip install python-lzo`")

    def _decomp(self, data):
        if self.comp_id == COMPRESSOR_GZIP:
            return zlib.decompress(data)
        elif self.comp_id == COMPRESSOR_XZ:
            return lzma.decompress(data, format=lzma.FORMAT_XZ)
        elif self.comp_id == COMPRESSOR_LZMA:
            try:
                return lzma.decompress(data)  # auto-detect
            except lzma.LZMAError:
                filt = {"id": lzma.FILTER_LZMA1, "dict_size": 1 << 23}
                return lzma.decompress(data, format=lzma.FORMAT_RAW, filters=[filt])
        elif self.comp_id == COMPRESSOR_LZ4:
            return lz4f.decompress(data)
        elif self.comp_id == COMPRESSOR_ZSTD:
            dctx = zstd.ZstdDecompressor()
            return dctx.decompress(data)
        elif self.comp_id == COMPRESSOR_LZO:
            return pylzo.decompress(data)

    def decompress_meta(self, data, uncompressed_flag):
        return data if uncompressed_flag else self._decomp(data)

    def decompress_data(self, data, is_uncompressed):
        return data if is_uncompressed else self._decomp(data)

class SquashFS:
    def __init__(self, f):
        self.f = f
        self._read_super()
        self.decomp = Decompressor(self.compression_id)
        self._meta_cache = {}      # abs_off -> decompressed 8KiB (or smaller) block
        self._id_list = None       # list of u32, length = id_count
        self._fragments = None     # list of (start, size_on_disk, uncompressed_flag)
        self._xattr_lookup = None  # list of dict entries for xattr lookup table
        self._xattr_stream_base = None  # absolute start of xattr key/value metadata stream

        # 권한/속성 적용 토글 (main에서 --no-meta로 끌 수 있음)
        self.apply_meta = True

        # 통계 수집
        self.stats = {
            "dirs": 0,
            "files": 0,
            "symlinks": 0,
            "other": 0,
            "total_bytes": 0,
            "files_nonempty": 0,
            "fragments_used": set(),
            "max_depth": 0,
        }

    # ---------- Superblock ----------
    def _read_super(self):
        self.f.seek(0)
        sb = self.f.read(96)
        if len(sb) < 96:
            raise ValueError("File too small")
        if u32(sb, 0) != MAGIC:
            raise ValueError("Not a SquashFS image")
        self.inode_count = u32(sb, 4)
        self.mod_time = u32(sb, 8)
        self.block_size = u32(sb, 12)
        self.fragment_entry_count = u32(sb, 16)
        self.compression_id = u16(sb, 20)
        self.block_log = u16(sb, 22)
        self.flags = u16(sb, 24)
        self.id_count = u16(sb, 26)
        self.version_major = u16(sb, 28)
        self.version_minor = u16(sb, 30)
        self.root_inode_ref = u64(sb, 32)
        self.bytes_used = u64(sb, 40)
        self.id_table_start = u64(sb, 48)
        self.xattr_id_table_start = u64(sb, 56)
        self.inode_table_start = u64(sb, 64)
        self.directory_table_start = u64(sb, 72)
        self.fragment_table_start = u64(sb, 80)
        self.export_table_start = u64(sb, 88)
        if self.version_major != 4:
            raise ValueError(f"Unsupported SquashFS version {self.version_major}.{self.version_minor}")

    # ---------- Metadata helpers ----------
    def _read_meta_block(self, abs_off):
        if abs_off in self._meta_cache:
            return self._meta_cache[abs_off]
        self.f.seek(abs_off)
        hdr2 = self.f.read(2)
        if len(hdr2) != 2:
            raise EOFError("EOF in metadata header")
        size = struct.unpack("<H", hdr2)[0]
        uncompressed = (size & 0x8000) != 0
        size &= 0x7FFF
        raw = self.f.read(size)
        if len(raw) != size:
            raise EOFError("EOF in metadata payload")
        data = self.decomp.decompress_meta(raw, uncompressed)
        self._meta_cache[abs_off] = data
        return data

    def _skip_n_meta_blocks(self, table_start_abs, n):
        abs_ptr = table_start_abs
        for _ in range(n):
            self.f.seek(abs_ptr)
            hdr2 = self.f.read(2)
            if len(hdr2) != 2:
                raise EOFError("EOF while skipping metadata blocks")
            size = struct.unpack("<H", hdr2)[0] & 0x7FFF
            abs_ptr += 2 + size
        return abs_ptr

    def _read_from_meta_stream(self, table_start_abs, rel_off, need_len):
        out = bytearray()
        while len(out) < need_len:
            block_index = rel_off // 8192
            in_block_off = rel_off % 8192
            abs_ptr = self._skip_n_meta_blocks(table_start_abs, block_index)
            block = self._read_meta_block(abs_ptr)
            take = min(need_len - len(out), len(block) - in_block_off)
            if take <= 0:
                break
            out += block[in_block_off:in_block_off + take]
            rel_off += take
        return bytes(out)

    def _read_meta_span(self, first_abs_ptr, start_in_first, need_len):
        abs_ptr = first_abs_ptr
        remaining = need_len
        out = bytearray()

        block = self._read_meta_block(abs_ptr)
        take = min(remaining, max(0, len(block) - start_in_first))
        if take > 0:
            out += block[start_in_first:start_in_first + take]
            remaining -= take

        self.f.seek(abs_ptr)
        hdr = self.f.read(2)
        if len(hdr) != 2:
            if remaining > 0:
                raise EOFError("EOF while advancing metadata (first block)")
            return bytes(out)
        size_on_disk = struct.unpack("<H", hdr)[0] & 0x7FFF
        abs_ptr += 2 + size_on_disk

        while remaining > 0:
            block = self._read_meta_block(abs_ptr)
            take = min(remaining, len(block))
            if take <= 0:
                break
            out += block[:take]
            remaining -= take

            self.f.seek(abs_ptr)
            hdr = self.f.read(2)
            if len(hdr) != 2:
                break
            size_on_disk = struct.unpack("<H", hdr)[0] & 0x7FFF
            abs_ptr += 2 + size_on_disk

        return bytes(out)

    # ---------- ID (UID/GID) table ----------
    def _load_id_table(self):
        if self._id_list is not None:
            return
        if self.id_count == 0 or self.id_table_start == 0xFFFFFFFFFFFFFFFF:
            self._id_list = []
            return
        num_md = int(ceil(self.id_count / 2048.0))
        self.f.seek(self.id_table_start)
        ptrs = [u64(self.f.read(8), 0) for _ in range(num_md)]
        out = []
        for p in ptrs:
            block = self._read_meta_block(p)
            off = 0
            while off + 4 <= len(block) and len(out) < self.id_count:
                out.append(u32(block, off))
                off += 4
        self._id_list = out  # index -> uid/gid value

    # ---------- Xattr tables ----------
    def _load_xattr_tables(self):
        if self._xattr_lookup is not None:
            return
        if self.xattr_id_table_start == 0xFFFFFFFFFFFFFFFF:
            self._xattr_lookup = []
            self._xattr_stream_base = None
            return

        self.f.seek(self.xattr_id_table_start)
        xattr_table_start = u64(self.f.read(8), 0)
        xattr_ids = u32(self.f.read(4), 0)
        _ = self.f.read(4)  # unused
        num_md = int(ceil(xattr_ids / 512.0))
        md_ptrs = [u64(self.f.read(8), 0) for _ in range(num_md)]

        entries = []
        for p in md_ptrs:
            block = self._read_meta_block(p)
            off = 0
            while off + 16 <= len(block) and len(entries) < xattr_ids:
                xref = u64(block, off + 0)
                cnt  = u32(block, off + 8)
                size = u32(block, off + 12)
                entries.append({"ref": xref, "count": cnt, "size": size})
                off += 16

        self._xattr_lookup = entries
        self._xattr_stream_base = xattr_table_start

    @staticmethod
    def _xattr_prefix(tid):
        ns = tid & 0xFF
        return {0: "user", 1: "trusted", 2: "security"}.get(ns, None), (tid & 0x0100) != 0

    def _read_xattrs_for_index(self, idx):
        self._load_xattr_tables()
        if self._xattr_lookup is None or not self._xattr_lookup:
            return []
        if idx == 0xFFFFFFFF or idx >= len(self._xattr_lookup):
            return []

        ent = self._xattr_lookup[idx]
        ref = ent["ref"]
        kv_count = ent["count"]
        total_size = ent["size"]

        md0_abs = (ref >> 16) & 0xFFFFFFFF
        off_in_block = ref & 0xFFFF

        blob = self._read_from_meta_stream(md0_abs, off_in_block, total_size)
        cur = 0
        out = []

        for _ in range(kv_count):
            if cur + 4 > len(blob):
                break
            typ = u16(blob, cur + 0)
            name_size = u16(blob, cur + 2)
            cur += 4
            if cur + name_size > len(blob):
                break
            name_raw = blob[cur:cur + name_size]
            cur += name_size

            prefix, value_is_ref = self._xattr_prefix(typ)
            if prefix is None:
                if cur + 4 > len(blob): break
                vlen = u32(blob, cur); cur += 4
                cur += vlen
                continue

            if cur + 4 > len(blob): break
            vlen = u32(blob, cur); cur += 4
            if not value_is_ref:
                if cur + vlen > len(blob): break
                value = blob[cur:cur + vlen]
                cur += vlen
            else:
                if vlen != 8 or cur + 8 > len(blob): break
                vref = u64(blob, cur); cur += 8
                v_md0_abs = (vref >> 16) & 0xFFFFFFFF
                v_off = vref & 0xFFFF
                raw4 = self._read_from_meta_stream(v_md0_abs, v_off, 4)
                val_len = u32(raw4, 0)
                value = self._read_from_meta_stream(v_md0_abs, v_off + 4, val_len)

            key = f"{prefix}.{name_raw.decode('utf-8', errors='surrogateescape')}"
            out.append((key, value))

        return out

    # ---------- Inodes / directories / data blocks ----------
    def _inode_by_ref(self, ref):
        rel_block = (ref >> 16) & 0xFFFFFFFF
        off_in_block = ref & 0xFFFF
        abs_block_start = self.inode_table_start + rel_block

        # 블록 경계 안전 버퍼
        safe_buf = self._read_meta_span(abs_block_start, off_in_block, 256)

        if len(safe_buf) < 16:
            safe_buf = self._read_meta_span(abs_block_start, off_in_block, 64)
            if len(safe_buf) < 16:
                raise EOFError("inode header truncated across metadata blocks")

        inode_type   = u16(safe_buf, 0)
        permissions  = u16(safe_buf, 2)
        uid_idx      = u16(safe_buf, 4)
        gid_idx      = u16(safe_buf, 6)
        mtime        = u32(safe_buf, 8)
        ino          = u32(safe_buf, 12)
        hdr = {
            "type": inode_type, "mode": permissions,
            "uid_idx": uid_idx, "gid_idx": gid_idx,
            "mtime": mtime, "ino": ino
        }
        cursor = 16
        return safe_buf, inode_type, hdr, cursor

    def _read_dir_entries(self, dir_block_start, block_offset, total_size):
        abs_ptr = self.directory_table_start + dir_block_start
        remaining = total_size
        buf = bytearray()

        first_block = self._read_meta_block(abs_ptr)
        take = min(remaining, max(0, len(first_block) - block_offset))
        if take > 0:
            buf += first_block[block_offset:block_offset + take]
        remaining -= take

        self.f.seek(abs_ptr)
        hdr = self.f.read(2)
        if len(hdr) != 2:
            raise EOFError("EOF while advancing directory metadata")
        size_on_disk = struct.unpack("<H", hdr)[0] & 0x7FFF
        abs_ptr += 2 + size_on_disk

        while remaining > 0:
            block = self._read_meta_block(abs_ptr)
            take = min(remaining, len(block))
            if take <= 0:
                break
            buf += block[:take]
            remaining -= take

            self.f.seek(abs_ptr)
            hdr = self.f.read(2)
            if len(hdr) != 2:
                break
            size_on_disk = struct.unpack("<H", hdr)[0] & 0x7FFF
            abs_ptr += 2 + size_on_disk

        entries = []
        cur = 0
        end = len(buf)

        while cur + 12 <= end:
            count = u32(buf, cur + 0)
            inode_table_rel_start = u32(buf, cur + 4)
            ref_ino_base = u32(buf, cur + 8)
            cur += 12

            for _ in range(count + 1):
                if cur + 8 > end:
                    return entries
                off = u16(buf, cur + 0)                       # inode 메타블록 내 오프셋 (decompressed)
                ino_delta = struct.unpack_from("<h", buf, cur + 2)[0]
                ent_type = u16(buf, cur + 4)
                name_size_m1 = u16(buf, cur + 6)
                cur += 8

                name_len = name_size_m1 + 1
                if cur + name_len > end:
                    return entries
                name = buf[cur:cur + name_len].decode('utf-8', errors='surrogateescape')
                cur += name_len

                ref = ((inode_table_rel_start & 0xFFFFFFFF) << 16) | (off & 0xFFFF)
                entries.append({
                    "name": name,
                    "inode_ref": ref,
                    "inode_no": (ref_ino_base + ino_delta),
                    "type": ent_type
                })

        return entries

    def _load_fragments(self):
        if self._fragments is not None:
            return
        c = self.fragment_entry_count
        if c == 0 or self.fragment_table_start == 0xFFFFFFFFFFFFFFFF:
            self._fragments = []
            return
        self.f.seek(self.fragment_table_start)
        num_md = int(ceil(c / 512.0))
        md_ptrs = [u64(self.f.read(8), 0) for _ in range(num_md)]
        frags = []
        for p in md_ptrs:
            block = self._read_meta_block(p)
            off = 0
            while off + 16 <= len(block) and len(frags) < c:
                start = u64(block, off)
                size_raw = u32(block, off + 8)
                uncompressed = (size_raw & 0x01000000) != 0
                size_on_disk = size_raw & 0x00FFFFFF
                frags.append((start, size_on_disk, uncompressed))
                off += 16
        self._fragments = frags

    def _read_file_data(self, blocks_start, file_size, block_sizes, fragment_block_index, frag_offset):
        out = io.BytesIO()
        cur = blocks_start
        remaining = file_size
        for raw in block_sizes:
            if remaining <= 0:
                break
            is_uncompressed = (raw & 0x01000000) != 0
            on_disk = raw & 0x00FFFFFF
            if on_disk == 0:
                # sparse block
                to_write = min(self.block_size, remaining)
                out.write(b"\x00" * to_write)
                remaining -= to_write
                continue
            self.f.seek(cur)
            chunk = self.f.read(on_disk)
            cur += on_disk
            data = self.decomp.decompress_data(chunk, is_uncompressed)
            take = min(len(data), remaining)
            out.write(data[:take])
            remaining -= take

        if remaining > 0 and fragment_block_index != 0xFFFFFFFF:
            self._load_fragments()
            start, on_disk, is_uncomp = self._fragments[fragment_block_index]
            self.f.seek(start)
            frag_raw = self.f.read(on_disk)
            frag_data = self.decomp.decompress_data(frag_raw, is_uncomp)
            out.write(frag_data[frag_offset:frag_offset+remaining])
            remaining = 0
        return out.getvalue()

    # ---------- Extraction ----------
    def _apply_mode_uidgid_xattr(self, path, mode_bits, uid_idx, gid_idx, xattrs_idx):
        if not getattr(self, "apply_meta", True):
            return
        # mode
        try:
            os.chmod(path, mode_bits & 0o7777)
        except Exception:
            pass
        # uid/gid
        try:
            self._load_id_table()
            uid = self._id_list[uid_idx] if self._id_list and uid_idx < len(self._id_list) else -1
            gid = self._id_list[gid_idx] if self._id_list and gid_idx < len(self._id_list) else -1
            if uid != -1 or gid != -1:
                os.chown(path, uid if uid != -1 else -1, gid if gid != -1 else -1)
        except Exception:
            pass
        # xattr
        try:
            if xattrs_idx is not None:
                for k, v in self._read_xattrs_for_index(xattrs_idx):
                    try:
                        os.setxattr(path, k.encode(), v)
                    except (AttributeError, NotImplementedError, OSError):
                        pass
        except Exception:
            pass

    def extract_all(self, outdir):
        os.makedirs(outdir, exist_ok=True)
        self._extract_node(self.root_inode_ref, outdir, "", depth=0)

    def _extract_node(self, inode_ref, outdir, name, depth):
        # 최대 디렉터리 깊이 갱신
        self.stats["max_depth"] = max(self.stats["max_depth"], depth)

        block, typ, hdr, cur = self._inode_by_ref(inode_ref)

        if typ in (1, 8):  # directory
            self.stats["dirs"] += 1

            if typ == 1:  # basic dir
                dir_block_start = u32(block, cur + 0)
                file_size = u16(block, cur + 8)
                block_offset = u16(block, cur + 10)
                xattr_idx = None
                cur += 16
                total = max(0, file_size - 3)  # . / .. / self 보정
            else:          # dir v2
                file_size = u32(block, cur + 4)
                dir_block_start = u32(block, cur + 8)
                block_offset = u16(block, cur + 18)
                xattr_idx = u32(block, cur + 20)
                cur += 24
                total = file_size

            here = outdir if name == "" else _safe_join(outdir, name)
            os.makedirs(here, exist_ok=True)
            self._apply_mode_uidgid_xattr(here, hdr["mode"], hdr["uid_idx"], hdr["gid_idx"], xattr_idx)

            ents = self._read_dir_entries(dir_block_start, block_offset, total)
            for e in ents:
                self._extract_node(e["inode_ref"], _safe_join(outdir, name) if name else outdir, e["name"], depth=depth+1)

        elif typ in (2, 9):  # regular file
            self.stats["files"] += 1

            if typ == 2:  # basic file
                blocks_start = u32(block, cur + 0)
                frag_idx = u32(block, cur + 4)
                frag_off = u32(block, cur + 8)
                file_size = u32(block, cur + 12)
                cur += 16
                full_blocks = file_size // self.block_size
                has_tail = (file_size % self.block_size) != 0
                count = full_blocks if (has_tail and frag_idx != 0xFFFFFFFF) else (
                    int(ceil(file_size / float(self.block_size))) if file_size else 0
                )
                block_sizes = [u32(block, cur + 4*i) for i in range(count)]
                xattr_idx = None
            else:          # file v2
                blocks_start = u64(block, cur + 0)
                file_size = u64(block, cur + 8)
                frag_idx = u32(block, cur + 28)
                frag_off = u32(block, cur + 32)
                xattr_idx = u32(block, cur + 36)
                cur += 40
                count = (file_size // self.block_size) if frag_idx != 0xFFFFFFFF else (
                    int(ceil(file_size / float(self.block_size))) if file_size else 0
                )
                block_sizes = [u32(block, cur + 4*i) for i in range(count)]

            data = self._read_file_data(blocks_start, file_size, block_sizes, frag_idx, frag_off)

            path = _safe_join(outdir if name == "" else _safe_join(outdir, ""), name)
            os.makedirs(os.path.dirname(path), exist_ok=True)
            with open(path, "wb") as wf:
                wf.write(data)

            # 통계 업데이트
            size_written = len(data)
            self.stats["total_bytes"] += size_written
            if size_written > 0:
                self.stats["files_nonempty"] += 1
            if (file_size % self.block_size) != 0 and frag_idx != 0xFFFFFFFF:
                self.stats["fragments_used"].add(frag_idx)

            self._apply_mode_uidgid_xattr(path, hdr["mode"], hdr["uid_idx"], hdr["gid_idx"], xattr_idx)

        elif typ in (3, 10):  # symlink
            self.stats["symlinks"] += 1
            tsize = u32(block, cur + 4)
            target = block[cur + 8: cur + 8 + tsize].decode('utf-8', errors='surrogateescape')
            path = _safe_join(outdir if name == "" else _safe_join(outdir, ""), name)
            try:
                if os.path.lexists(path):
                    os.remove(path)
                os.symlink(target, path)
            except (NotImplementedError, OSError):
                with open(path, "w", encoding="utf-8") as wf:
                    wf.write(f"SYMLINK -> {target}\n")

        else:
            # device/fifo/socket 등
            self.stats["other"] += 1
            path = _safe_join(outdir if name == "" else _safe_join(outdir, ""), name)
            os.makedirs(os.path.dirname(path), exist_ok=True)
            with open(path + ".unsupported", "w", encoding="utf-8") as wf:
                wf.write(f"Unsupported inode type {typ}\n")

    def print_summary(self, outdir):
        comp_name = {1:"gzip",2:"lzma",3:"lzo",4:"xz",5:"lz4",6:"zstd"}.get(self.compression_id, "unknown")
        total_entries = self.stats["dirs"] + self.stats["files"] + self.stats["symlinks"] + self.stats["other"]
        avg_file = (self.stats["total_bytes"] / self.stats["files_nonempty"]) if self.stats["files_nonempty"] else 0
        print("\n===== SquashFS Extract Summary =====")
        print(f"- Output dir            : {outdir}")
        print(f"- Version               : {self.version_major}.{self.version_minor}")
        print(f"- Block size            : {self.block_size} bytes")
        print(f"- Compression           : {self.compression_id} ({comp_name})")
        print(f"- Inodes (super)        : {self.inode_count}")
        print(f"- Fragment entries (SB) : {self.fragment_entry_count}")
        print(f"- Entries extracted     : {total_entries}")
        print(f"  · Directories         : {self.stats['dirs']}")
        print(f"  · Files               : {self.stats['files']}")
        print(f"  · Symlinks            : {self.stats['symlinks']}")
        print(f"  · Other               : {self.stats['other']}")
        print(f"- Total bytes written   : {self.stats['total_bytes']} ({human(self.stats['total_bytes'])})")
        print(f"- Avg non-empty file    : {avg_file:.1f} bytes ({human(avg_file)})")
        print(f"- Unique tail fragments : {len(self.stats['fragments_used'])}")
        print(f"- Max directory depth   : {self.stats['max_depth']}")
        print("====================================\n")

def main():
    ap = argparse.ArgumentParser(description="SquashFS v4 extractor (UID/GID + xattr, multi-compressor)")
    ap.add_argument("image", help="SquashFS.img path")
    ap.add_argument("-o", "--out", default="squashfs_out", help="Output directory")
    ap.add_argument("--no-meta", action="store_true", help="Do not apply chmod/chown/xattr on extract")
    args = ap.parse_args()

    with open(args.image, "rb") as f:
        fs = SquashFS(f)
        if args.no_meta:
            fs.apply_meta = False
        comp_name = {1:"gzip",2:"lzma",3:"lzo",4:"xz",5:"lz4",6:"zstd"}.get(fs.compression_id, "unknown")
        print(f"[+] SquashFS v{fs.version_major}.{fs.version_minor} block_size={fs.block_size} comp_id={fs.compression_id}({comp_name})")
        fs.extract_all(args.out)
        print(f"[+] Extracted to {args.out}")
        fs.print_summary(args.out)

if __name__ == "__main__":
    main()
