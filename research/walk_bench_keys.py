"""Answer keys for the walk benchmark (`walk_bench.py`): address -> real function name, from the public PDB
of a Microsoft binary, read here without Ghidra (so the walk's Ghidra projects never see a symbol).

    python research/walk_bench_keys.py E:/mafdet-tools/samples/bench/where.exe ... [--pdb-dir DIR] [--out DIR]

For each binary: the CodeView record of its debug directory (RSDS: GUID, age, PDB name), the PDB fetched
once from Microsoft's symbol server into its own folder under --pdb-dir (allowed for Microsoft's own tools
only: the request names the file), checked against the binary (the GUID and age in the PDB's info and DBI
streams), then its public symbols (S_PUB32) turned into addresses through the PDB's copy of the section
headers. The key, research/data/walk_bench_keys/<name>.json, holds every public symbol in an executable
section with its raw (decorated) and readable name; `walk_bench.py` matches the walk's functions to it.

Pure Python, no dependency: MSF 7.0 container, PDB info stream (1), DBI stream (3), symbol record stream,
section header stream (DBI's optional debug header, slot 5).
"""
from __future__ import annotations

import argparse
import hashlib
import json
import struct
import sys
import urllib.request
import uuid
from pathlib import Path

SYMBOL_SERVER = "https://msdl.microsoft.com/download/symbols"
MSF_MAGIC = b"Microsoft C/C++ MSF 7.00\r\n\x1aDS\x00\x00\x00"
S_PUB32 = 0x110E
IMAGE_SCN_MEM_EXECUTE = 0x20000000
IMAGE_SCN_CNT_CODE = 0x20


# ---------------------------------------------------------------- PE

def pe_info(data: bytes) -> dict:
    """Image base, sections and the CodeView record (RSDS) of a PE file."""
    if data[:2] != b"MZ":
        raise ValueError("not a PE file (no MZ)")
    pe = struct.unpack_from("<I", data, 0x3C)[0]
    if data[pe:pe + 4] != b"PE\0\0":
        raise ValueError("not a PE file (no PE signature)")
    machine, nsec, _, _, _, opt_size, _ = struct.unpack_from("<HHIIIHH", data, pe + 4)
    opt = pe + 24
    magic = struct.unpack_from("<H", data, opt)[0]
    if magic == 0x20B:                                  # PE32+
        image_base = struct.unpack_from("<Q", data, opt + 24)[0]
        dirs = opt + 112
    else:
        image_base = struct.unpack_from("<I", data, opt + 28)[0]
        dirs = opt + 96
    sections = []
    for i in range(nsec):
        off = opt + opt_size + 40 * i
        name = data[off:off + 8].rstrip(b"\0").decode("ascii", "replace")
        vsize, va, rsize, rptr = struct.unpack_from("<IIII", data, off + 8)
        chars = struct.unpack_from("<I", data, off + 36)[0]
        sections.append({"name": name, "va": va, "vsize": vsize, "raw_size": rsize, "raw_ptr": rptr,
                         "characteristics": chars})

    def rva_to_off(rva: int) -> int | None:
        for s in sections:
            if s["va"] <= rva < s["va"] + max(s["vsize"], s["raw_size"]):
                return s["raw_ptr"] + rva - s["va"]
        return None
    dbg_rva, dbg_size = struct.unpack_from("<II", data, dirs + 8 * 6)
    codeview = None
    off = rva_to_off(dbg_rva) if dbg_rva else None
    for i in range((dbg_size // 28) if off is not None else 0):
        _, _, _, _, typ, size, _, ptr = struct.unpack_from("<IIHHIIII", data, off + 28 * i)
        if typ == 2 and data[ptr:ptr + 4] == b"RSDS":     # IMAGE_DEBUG_TYPE_CODEVIEW
            guid = uuid.UUID(bytes_le=data[ptr + 4:ptr + 20])
            age = struct.unpack_from("<I", data, ptr + 20)[0]
            name = data[ptr + 24:ptr + size].split(b"\0", 1)[0].decode("utf-8", "replace")
            codeview = {"guid": str(guid).upper(), "age": age, "pdb": name}
    return {"machine": machine, "image_base": image_base, "sections": sections, "codeview": codeview}


def symbol_server_url(codeview: dict) -> str:
    pdb = Path(codeview["pdb"].replace("\\", "/")).name
    key = codeview["guid"].replace("-", "").upper() + format(codeview["age"], "X")
    return f"{SYMBOL_SERVER}/{pdb}/{key}/{pdb}"


# ---------------------------------------------------------------- PDB (MSF 7.0)

class Msf:
    def __init__(self, data: bytes):
        if not data.startswith(MSF_MAGIC):
            raise ValueError("not an MSF 7.00 PDB (a compressed .pd_ or an old format?)")
        self.data = data
        self.block, _, nblocks, dir_bytes, _, map_addr = struct.unpack_from("<IIIIII", data, 32)
        ndir = -(-dir_bytes // self.block)
        dir_blocks = struct.unpack_from(f"<{ndir}I", data, map_addr * self.block)
        directory = b"".join(self._blk(b) for b in dir_blocks)[:dir_bytes]
        n = struct.unpack_from("<I", directory, 0)[0]
        sizes = struct.unpack_from(f"<{n}I", directory, 4)
        pos = 4 + 4 * n
        self.streams = []
        for size in sizes:
            if size == 0xFFFFFFFF:
                self.streams.append(None)
                continue
            count = -(-size // self.block)
            blocks = struct.unpack_from(f"<{count}I", directory, pos)
            pos += 4 * count
            self.streams.append((size, blocks))

    def _blk(self, i: int) -> bytes:
        return self.data[i * self.block:(i + 1) * self.block]

    def stream(self, i: int) -> bytes:
        if i >= len(self.streams) or self.streams[i] is None:
            return b""
        size, blocks = self.streams[i]
        return b"".join(self._blk(b) for b in blocks)[:size]


def pdb_read(data: bytes) -> dict:
    """GUID and ages, section headers and public symbols of a PDB."""
    msf = Msf(data)
    info = msf.stream(1)
    _, _, info_age = struct.unpack_from("<III", info, 0)
    guid = str(uuid.UUID(bytes_le=info[12:28])).upper()
    dbi = msf.stream(3)
    (_, _, dbi_age, _, _, _, _, sym_stream, _, mod_size, sc_size, sm_size, src_size, tsm_size, _,
     dbg_size, ec_size, _, _) = struct.unpack_from("<iIIHHHHHHiiiiiIiiHH", dbi, 0)
    dbg_off = 64 + mod_size + sc_size + sm_size + src_size + tsm_size + ec_size
    dbg = struct.unpack_from(f"<{dbg_size // 2}H", dbi, dbg_off)
    sections = []
    if len(dbg) > 5 and dbg[5] != 0xFFFF:
        sh = msf.stream(dbg[5])
        for off in range(0, len(sh) - 39, 40):
            name = sh[off:off + 8].rstrip(b"\0").decode("ascii", "replace")
            vsize, va = struct.unpack_from("<II", sh, off + 8)
            chars = struct.unpack_from("<I", sh, off + 36)[0]
            sections.append({"name": name, "va": va, "vsize": vsize, "characteristics": chars})
    publics = []
    rec = msf.stream(sym_stream)
    pos = 0
    while pos + 4 <= len(rec):
        length, kind = struct.unpack_from("<HH", rec, pos)
        if length < 2:
            break
        if kind == S_PUB32:
            flags, offset, seg = struct.unpack_from("<IIH", rec, pos + 4)
            name = rec[pos + 14:pos + 2 + length].split(b"\0", 1)[0].decode("utf-8", "replace")
            publics.append({"flags": flags, "segment": seg, "offset": offset, "name": name})
        pos += 2 + length
    return {"guid": guid, "info_age": info_age, "dbi_age": dbi_age, "sections": sections, "publics": publics}


# ---------------------------------------------------------------- names

def undecorate(raw: str) -> tuple[str, str]:
    """A public symbol's name -> (readable name, function part). Enough of MSVC's scheme for keys:
    `?Search@CWhere@@QEAAHXZ` -> ("CWhere::Search", "Search"); special members (`??0`, `??_G`...) keep the
    class: ("CWhere::`constructor'", "constructor"); C names stay; `__imp_` and `@n` suffixes go."""
    name = raw
    if name.startswith("__imp_"):
        name = name[6:]
    if not name.startswith("?"):
        base = name.split("@", 1)[0] if not name.startswith("@") else name.lstrip("@").split("@", 1)[0]
        return base, base
    body = name[1:]
    special = ""
    if body.startswith("?"):
        code = body[1:3] if body[1:2] == "_" else body[1:2]
        special = {"0": "constructor", "1": "destructor", "_G": "scalar_deleting_destructor",
                   "_E": "vector_deleting_destructor", "_7": "vftable", "2": "operator_new",
                   "3": "operator_delete", "4": "operator_assign"}.get(code, "operator")
        body = body[1 + len(code):]
    parts = []
    for p in body.split("@"):
        if not p:
            break
        if p[0].isdigit() or p.startswith("?"):          # back-references, templates: stop there
            break
        parts.append(p)
    if special:
        scope = "::".join(reversed(parts))
        return (f"{scope}::{special}" if scope else special), special
    if not parts:
        return raw, raw
    return "::".join(reversed(parts)), parts[0]


def make_key(binary: Path, pdb_path: Path) -> dict:
    data = binary.read_bytes()
    pe = pe_info(data)
    cv = pe["codeview"]
    pdb = pdb_read(pdb_path.read_bytes())
    if pdb["guid"] != cv["guid"] or cv["age"] not in (pdb["dbi_age"], pdb["info_age"]):
        raise ValueError(f"{pdb_path} does not match {binary}: GUID {pdb['guid']} age {pdb['dbi_age']}/"
                         f"{pdb['info_age']} against {cv['guid']} age {cv['age']}")
    secs = pdb["sections"] or pe["sections"]
    base = pe["image_base"]
    functions = {}
    for p in pdb["publics"]:
        if not 1 <= p["segment"] <= len(secs):
            continue
        s = secs[p["segment"] - 1]
        if not s["characteristics"] & (IMAGE_SCN_MEM_EXECUTE | IMAGE_SCN_CNT_CODE):
            continue
        addr = base + s["va"] + p["offset"]
        readable, short = undecorate(p["name"])
        entry = {"name": short, "full": readable, "raw": p["name"], "function": bool(p["flags"] & 2)}
        key = f"0x{addr:x}"
        if key in functions:                            # aliases (identical code folded): keep them all
            functions[key].setdefault("aliases", []).append(p["name"])
            continue
        functions[key] = entry
    return {"binary": binary.name, "sha256": hashlib.sha256(data).hexdigest(), "image_base": f"0x{base:x}",
            "pdb": {"name": Path(cv["pdb"].replace("\\", "/")).name, "guid": cv["guid"], "age": cv["age"],
                    "url": symbol_server_url(cv), "sha256": hashlib.sha256(pdb_path.read_bytes()).hexdigest()},
            "publics": len(pdb["publics"]), "functions": dict(sorted(functions.items(), key=lambda kv: int(kv[0], 16)))}


def fetch_pdb(binary: Path, pdb_dir: Path) -> Path:
    """The PDB of `binary`, from the symbol server, into its own folder <pdb_dir>/<name>-<guid+age>/."""
    cv = pe_info(binary.read_bytes())["codeview"]
    if not cv:
        raise ValueError(f"{binary} has no CodeView record")
    url = symbol_server_url(cv)
    folder = pdb_dir / f"{binary.name}-{url.rsplit('/', 2)[-2]}"
    target = folder / Path(cv["pdb"].replace("\\", "/")).name
    if target.is_file():
        return target
    folder.mkdir(parents=True, exist_ok=True)
    req = urllib.request.Request(url, headers={"User-Agent": "Microsoft-Symbol-Server/10.0.0.0"})
    with urllib.request.urlopen(req, timeout=120) as r:
        body = r.read()
    if not body.startswith(MSF_MAGIC):
        raise ValueError(f"{url} did not return an MSF 7.00 PDB ({body[:8]!r}...)")
    target.write_bytes(body)
    return target


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    ap.add_argument("binaries", nargs="+")
    ap.add_argument("--pdb-dir", default=r"E:\mafdet-tools\samples\bench-pdb")
    ap.add_argument("--out", default=str(Path(__file__).parent / "data" / "walk_bench_keys"))
    a = ap.parse_args(argv)
    out = Path(a.out)
    out.mkdir(parents=True, exist_ok=True)
    for b in a.binaries:
        binary = Path(b)
        pdb = fetch_pdb(binary, Path(a.pdb_dir))
        key = make_key(binary, pdb)
        n = len(key["functions"])
        nf = sum(1 for f in key["functions"].values() if f["function"])
        (out / f"{binary.stem}.json").write_text(json.dumps(key, indent=1, ensure_ascii=False), encoding="utf-8")
        print(f"{binary.name:<16} {key['pdb']['name']:<16} {key['pdb']['guid']} age {key['pdb']['age']}: "
              f"{key['publics']} publics, {n} in code ({nf} flagged function)")
    return 0


if __name__ == "__main__":
    sys.exit(main())
