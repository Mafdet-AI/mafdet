"""The walk benchmark's pure parts (research/walk_bench.py, research/walk_bench_keys.py): tokens, F1, the
runtime rule, layers, aggregation, calibration, and the PDB reader's name and container handling. No GPU,
no decompiler, no network."""
import struct
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "research"))
import walk_bench as wb  # noqa: E402
import walk_bench_keys as wk  # noqa: E402


@pytest.mark.parametrize("name, expected", [
    ("IsConsoleHandle", ["is", "console", "handle"]),
    ("is_console_handle", ["is", "console", "handle"]),
    ("DynArrayAppendDWORD", ["dyn", "array", "append", "dword"]),
    ("__DynArrayAppend", ["dyn", "array", "append"]),
    ("GetHTTPResponse", ["get", "http", "response"]),
    ("ShowLastErrorEx", ["show", "last", "error"]),            # Ex: a variant, not a word
    ("StringCopyW", ["string", "copy"]),                       # W: the charset
    ("FindString2", ["find", "string"]),                       # digits dropped
    ("GetResString2FromModule", ["get", "resource", "string", "module"]),
    ("add_config_entries", ["add", "config", "entry"]),        # plural folded
    ("alloc_buf", ["allocate", "buffer"]),                     # abbreviations
    ("init_msg_str", ["initialize", "message", "string"]),
    ("fetch_value", ["get", "value"]),
    ("wmain", ["main"]),
    ("CWhere::Search", ["search"]),                            # the method, not the class
    ("wmainCRTStartup$filt$0", ["main", "crt", "startup"]),
    ("copy_file_to_the_target", ["copy", "file", "target"]),   # stop words
    ("", []),
])
def test_tokens(name, expected):
    assert wb.tokens(name) == expected


def test_prf():
    assert wb.prf("is_console_handle", "IsConsoleHandle") == (1.0, 1.0, 1.0)
    p, r, f = wb.prf("allocate_heap_memory", "AllocateMemory")
    assert (p, r) == (pytest.approx(2 / 3), 1.0) and f == pytest.approx(0.8)
    assert wb.prf("validate_config_and_initialize", "wmain") == (0.0, 0.0, 0.0)
    assert wb.prf("", "wmain") == (0.0, 0.0, 0.0)
    # Repeated words count once.
    assert wb.prf("free_free_list", "FreeList") == (1.0, 1.0, 1.0)
    assert [wb.f1_bucket(x) for x in (1.0, 0.5, 0.49, 0.0)] == ["high", "high", "some", "none"]


@pytest.mark.parametrize("name", [
    "__security_init_cookie", "__report_gsfailure", "_guard_check_icall", "__scrt_common_main_seh",
    "wmainCRTStartup", "mainCRTStartup", "__CxxUnhandledExceptionFilter", "_ValidateImageBase",
    "RtlpImageNtHeader", "_wsetargv", "__acrt_iob_func", "StringCchPrintfW", "_RTC_Initialize",
    "__GSHandlerCheck", "pre_c_init", "_get_image_app_type", "operator_new", "scalar_deleting_destructor",
    "_Init_thread_header", "IsCharToOemAPresent", "__chkstk", "__tailMerge_ext_ms_win_ntuser_l1_1_0_dll"])
def test_runtime(name):
    assert wb.is_runtime(name)


@pytest.mark.parametrize("name", ["wmain", "ShowMessage", "__DynArrayAppend", "FindforFile", "grepbuffer",
                                  "Clip_OnCreate", "StringCompareW", "AllocateMemory", "CWhere::Search"])
def test_program(name):
    assert not wb.is_runtime(name)


def test_score_name():
    assert wb.score_name("DoCopy", "XCOPY::DoCopy") == "DoCopy"
    assert wb.score_name("destructor", "PAGER::destructor") == "PAGER_destructor"
    assert wb.tokens(wb.score_name("destructor", "PAGER::destructor")) == ["pager", "destroy"]
    assert wb.prf("destroy_pager", wb.score_name("destructor", "PAGER::destructor"))[2] == 1.0
    assert wb.score_name("constructor", None) == "constructor"


def fn(status="pending", callees=()):
    return {"status": status, "callees": list(callees)}


def test_heights_and_layers():
    # a -> b -> c (leaf); a -> d (leaf); e <-> f (a cycle) -> c; k is known (not counted, and no edge).
    fs = {"0x1": fn(callees=["0x2", "0x4", "0x9"]), "0x2": fn(callees=["0x3"]), "0x3": fn(),
          "0x4": fn(callees=["0x9"]), "0x5": fn(callees=["0x6", "0x3"]), "0x6": fn(callees=["0x5"]),
          "0x9": fn("known", ["0x3"]), "0x7": fn(callees=["0x7"])}       # 0x7 calls itself only: a leaf
    h = wb.heights(fs)
    assert "0x9" not in h
    assert h == {"0x1": 2, "0x2": 1, "0x3": 0, "0x4": 0, "0x5": 1, "0x6": 1, "0x7": 0}
    assert [wb.layer_of(x, 2) for x in (0, 1, 2)] == ["leaf", "middle", "top"]
    assert [wb.layer_of(x, 14) for x in (0, 6, 7, 14)] == ["leaf", "middle", "top", "top"]
    assert wb.layer_of(1, 1) == "middle"                               # top needs a height of 2 at least


def test_heights_deep_chain_is_iterative():
    n = 5000
    fs = {f"0x{i:x}": fn(callees=[f"0x{i + 1:x}"] if i + 1 < n else []) for i in range(n)}
    assert wb.heights(fs)["0x0"] == n - 1


def book_and_key():
    book = {"functions": {
        "0x10": {**fn("done", ["0x20"]), "name": "parse_options", "summary": "s", "seconds": 10.0,
                 "prompt_tokens": 100, "completion_tokens": 50, "steps": 1},
        "0x20": {**fn("done"), "name": "is_console_handle", "summary": "s", "seconds": 2.0,
                 "prompt_tokens": 80, "completion_tokens": 20, "steps": 1},
        "0x30": {**fn("failed"), "name": "", "summary": "", "seconds": 4.0, "prompt_tokens": 90,
                 "completion_tokens": 30, "steps": 2},
        "0x40": {**fn("done"), "name": "init_cookie", "summary": "s", "seconds": 1.0},
        "0x50": {**fn("known"), "orig": "entry"}}}
    key = {"functions": {"0x10": {"name": "ProcessOptions", "full": "ProcessOptions"},
                         "0x20": {"name": "IsConsoleHandle", "full": "IsConsoleHandle"},
                         "0x30": {"name": "Match", "full": "Match"},
                         "0x50": {"name": "wmainCRTStartup", "full": "wmainCRTStartup"}}}
    return book, key


def test_score_book_and_aggregate():
    book, key = book_and_key()
    rows = wb.score_book(book, key)
    by = {r["address"]: r for r in rows}
    assert set(by) == {"0x10", "0x20", "0x30", "0x40"}                 # known functions are not scored
    assert by["0x20"]["f1"] == 1.0 and by["0x20"]["layer"] == "leaf" and by["0x20"]["kind"] == "program"
    assert by["0x10"]["f1"] == pytest.approx(0.5) and by["0x10"]["height"] == 1
    assert by["0x30"]["f1"] == 0.0 and by["0x40"]["real"] is None and by["0x40"]["kind"] is None
    by["0x10"]["judge"], by["0x20"]["judge"] = "related", "same"
    a = wb.aggregate(rows)
    assert (a["functions"], a["walked"], a["named"], a["failed"], a["with_key"], a["scored"]) == (4, 4, 3, 1, 3, 2)
    assert a["f1"] == pytest.approx(0.75) and a["f1_zero"] == 0.0
    assert a["judge"] == {"same": 1, "related": 1, "different": 0} and a["judge_score"] == 0.75
    assert a["seconds_mean"] == pytest.approx(17 / 4)                  # every function walked, with a key or not
    b = wb.breakdown(rows)
    assert b["leaf"]["scored"] == 1 and b["middle"]["scored"] == 1 and b["runtime"]["scored"] == 0
    c = wb.coverage(rows)
    assert c["to_name"] == 4 and c["with_key"] == 3 and c["missing"][0]["address"] == "0x40"


def test_rescore_keeps_the_judge():
    book, key = book_and_key()
    rows = wb.score_book(book, key)
    rows[0]["judge"] = "same"
    result = {"label": "t", "runs": [{"binary": "x", "repeat": 1, "functions": rows}]}
    wb.rescore(result)
    assert result["runs"][0]["functions"][0]["judge"] == "same"
    assert result["aggregate"]["all"]["scored"] == 2 and "x" in result["by_binary"] and "1" in result["by_repeat"]
    assert "walk bench t" in wb.summarize(result)


def test_parse_judge():
    assert wb.parse_judge("Same") == "same"
    assert wb.parse_judge("**related**.") == "related"
    assert wb.parse_judge("different") == "different"
    assert wb.parse_judge("I think not") is None


def test_calibration_measures():
    rows = [{"address": a, "real": "X", "status": "done", "f1": f, "judge": j} for a, f, j in
            [("1", 0.9, "same"), ("2", 0.5, "related"), ("3", 0.0, "related"), ("4", 0.0, "different")]]
    labels = {"1": "right", "2": "vague", "3": "vague", "4": "wrong"}
    c = wb.calibration(rows, labels)
    assert c["n"] == 4 and c["f1_by_label"]["right"]["mean_f1"] == 0.9 and c["f1_by_label"]["vague"]["f1_zero"] == 1
    assert c["judge_exact"] == 1.0 and c["judge_kappa"] == 1.0 and c["judge_concordance"] == 1.0
    # F1 ties the vague 0.0 with the wrong 0.0: half a point for that pair.
    assert c["f1_concordance"] == pytest.approx((4 + 0.5) / 5)
    assert wb.concordance([(1, 0.0), (1, 1.0)]) is None
    assert wb.kappa([0, 1, 2], [2, 0, 1], 3) < 0


# ---------------------------------------------------------------- the keys' reader

@pytest.mark.parametrize("raw, readable, short", [
    ("wmain", "wmain", "wmain"),
    ("?Search@CWhere@@QEAAHXZ", "CWhere::Search", "Search"),
    ("?Run@Inner@Outer@@QEAAXXZ", "Outer::Inner::Run", "Run"),
    ("??0CWhere@@QEAA@XZ", "CWhere::constructor", "constructor"),
    ("??_GCWhere@@UEAAPEAXI@Z", "CWhere::scalar_deleting_destructor", "scalar_deleting_destructor"),
    ("__imp_GetLastError", "GetLastError", "GetLastError"),
    ("_guard_dispatch_icall$thunk$10345483385596137414", "_guard_dispatch_icall$thunk$10345483385596137414",
     "_guard_dispatch_icall$thunk$10345483385596137414"),
])
def test_undecorate(raw, readable, short):
    assert wk.undecorate(raw) == (readable, short)


def test_msf_streams_and_url():
    # A tiny MSF: block size 512, the directory in block 3, two streams (sizes 5 and 600 over blocks 4, 5-6).
    bs = 512
    data = bytearray(bs * 7)
    data[:32] = wk.MSF_MAGIC
    directory = struct.pack("<I", 2) + struct.pack("<II", 5, 600) + struct.pack("<III", 4, 5, 6)
    struct.pack_into("<IIIIII", data, 32, bs, 1, 7, len(directory), 0, 2)
    struct.pack_into("<I", data, 2 * bs, 3)                             # the block map: the directory is block 3
    data[3 * bs:3 * bs + len(directory)] = directory
    data[4 * bs:4 * bs + 5] = b"hello"
    data[5 * bs:5 * bs + 600] = bytes(range(256)) * 2 + bytes(88)
    msf = wk.Msf(bytes(data))
    assert msf.stream(0) == b"hello" and len(msf.stream(1)) == 600 and msf.stream(1)[:3] == b"\0\1\2"
    assert msf.stream(9) == b""
    with pytest.raises(ValueError):
        wk.Msf(b"MSCF" + bytes(100))
    url = wk.symbol_server_url({"pdb": r"C:\build\where.pdb", "guid": "A8B37774-47EF-BAA8-2506-17E8722F8ADC", "age": 1})
    assert url == "https://msdl.microsoft.com/download/symbols/where.pdb/A8B3777447EFBAA8250617E8722F8ADC1/where.pdb"


def test_paired_difference():
    a = [{"binary": "x", "address": str(i), "v": 0.0} for i in range(20)]
    b = [{"binary": "x", "address": str(i), "v": 0.5} for i in range(20)] + [{"binary": "x", "address": "99", "v": 1.0}]
    d = wb.paired(a, b, lambda r: r["v"])
    assert d["n"] == 20 and d["diff"] == 0.5 and d["ci95"] == [0.5, 0.5]
    assert wb.paired(a, [], lambda r: r["v"]) == {"n": 0}
    assert wb.paired(a, b, lambda r: None) == {"n": 0}


# ---------------------------------------------------------------- the second pass (--revise, --refine)

def two_pass_books():
    """One first-pass book (`base`), the same after a local --revise (`revise`) and after upstream's --refine
    (`refine`). The tree: 0x50 entry (known) -> 0x10 -> 0x20 (leaf), 0x10 -> 0x40 (leaf); 0x30 failed."""
    import copy
    base, key = book_and_key()
    for a, f in base["functions"].items():
        f.setdefault("orig", f"FUN_{a}")
        f.setdefault("flags", {})
        f.setdefault("confidence", "medium")
    base["functions"]["0x50"]["callees"] = ["0x10"]
    base["functions"]["0x10"]["callees"] = ["0x20", "0x40"]
    base["runs"] = [{"done": 3}]

    def first(f):
        return {"name": f["name"], "summary": f["summary"], "confidence": f["confidence"], "comment": ""}
    revise = copy.deepcopy(base)
    fs = revise["functions"]
    fs["0x10"]["first"] = first(fs["0x10"])          # renamed: parse_options -> process_options (F1 0.5 -> 1)
    fs["0x10"].update(name="process_options", summary="new")
    fs["0x10"]["revision"] = {"status": "done", "renamed": True, "seconds": 7.0, "prompt_tokens": 900,
                              "completion_tokens": 300, "steps": 1, "with": {"program": True, "callers": True}}
    fs["0x20"]["first"] = first(fs["0x20"])          # kept, its summary changed
    fs["0x20"].update(summary="another summary")
    fs["0x20"]["revision"] = {"status": "done", "renamed": False, "seconds": 3.0, "prompt_tokens": 500,
                              "completion_tokens": 100, "steps": 1}
    fs["0x40"]["first"] = first(fs["0x40"])          # renamed, no real name in the key
    fs["0x40"].update(name="set_up_cookie")
    fs["0x40"]["revision"] = {"status": "done", "renamed": True, "seconds": 2.0, "prompt_tokens": 400,
                              "completion_tokens": 100, "steps": 1}
    revise["program"] = {"status": "done", "summary": "A tool.", "seconds": 9.5, "prompt_tokens": 2000,
                         "completion_tokens": 400, "steps": 1, "inputs": {"imports": 3, "functions": 4, "strings": 0},
                         "facts": "x" * 50}
    revise["runs"].append({"revised": 3})
    refine = copy.deepcopy(base)
    fs = refine["functions"]
    fs["0x10"]["first"] = first(fs["0x10"])          # renamed: first kept, refine "renamed"
    fs["0x10"].update(name="handle_options", refine="renamed", refine_seconds=6.0,
                      prompt_tokens=100 + 700, completion_tokens=50 + 200)
    fs["0x20"].update(refine="kept", refine_seconds=2.5)   # kept, nothing changed: no `first`
    fs["0x40"].update(refine="failed: no call", refine_seconds=1.5)
    refine["program"] = {"summary": "A tool.", "model": "m", "seconds": 4.2}
    return base, revise, refine, key


def test_pass_books_read_both_formats():
    base, revise, refine, key = two_pass_books()
    assert not wb.is_two_pass(base) and wb.is_two_pass(revise) and wb.is_two_pass(refine)
    assert wb.pass_book(revise, 1)["functions"]["0x10"]["name"] == "parse_options"
    assert wb.pass_book(revise, 2)["functions"]["0x10"]["name"] == "process_options"
    assert wb.pass_book(refine, 1)["functions"]["0x10"]["name"] == "parse_options"
    assert wb.pass_book(refine, 1)["functions"]["0x20"]["name"] == "is_console_handle"   # kept, no `first`
    assert [wb.second_status(refine["functions"][a]) for a in ("0x10", "0x20", "0x40", "0x30")] == \
        ["done", "done", "failed", None]
    assert [wb.second_status(revise["functions"][a]) for a in ("0x10", "0x20", "0x40", "0x30")] == \
        ["done", "done", "done", None]
    # A book is never changed by being scored.
    assert "first" in revise["functions"]["0x10"] and revise["functions"]["0x10"]["name"] == "process_options"


def test_root_depths():
    base, *_ = two_pass_books()
    d = wb.root_depths(base["functions"])
    assert d["0x50"] == 0 and d["0x10"] == 1 and d["0x20"] == 2 and d["0x40"] == 2
    fs = {"0x2": fn(callees=["0x1"]), "0x1": fn(callees=["0x2"])}     # a cycle nobody calls: its lowest address
    assert wb.root_depths(fs) == {"0x1": 0, "0x2": 1}


def test_second_rows_and_stats_revise():
    base, revise, _, key = two_pass_books()
    rows1, rows2 = wb.second_rows(revise, key, base)
    by1, by2 = {r["address"]: r for r in rows1}, {r["address"]: r for r in rows2}
    assert by1["0x10"]["given"] == "parse_options" and by1["0x10"]["f1"] == pytest.approx(0.5)
    assert by2["0x10"]["given"] == "process_options" and by2["0x10"]["f1"] == 1.0
    assert by2["0x10"]["renamed"] and not by2["0x20"]["renamed"] and by2["0x40"]["renamed"]
    assert by2["0x10"]["first_given"] == "parse_options" and by2["0x10"]["depth"] == 1
    assert by2["0x30"]["read_again"] is False and by2["0x30"]["second"] is None
    assert by2["0x10"]["second"]["seconds"] == 7.0 and by2["0x10"]["second"]["prompt_tokens"] == 900
    assert by1["0x10"]["seconds"] == by2["0x10"]["seconds"] == 10.0          # the first pass's cost in both
    s = wb.second_stats(rows1, rows2)
    assert (s["named"], s["read"], s["read_done"], s["renamed"]) == (3, 3, 3, 2)
    assert s["by_depth"] == {"1": {"n": 1, "read": 1, "renamed": 1}, "2": {"n": 2, "read": 2, "renamed": 1}}
    assert s["renamed_scored"] == 1 and s["renamed_f1"] == {"n": 1, "better": 1, "worse": 0, "same": 0}
    assert s["seconds_mean"] == 4.0 and s["seconds_total"] == 12.0 and s["prompt_tokens_mean"] == 600.0
    assert wb.program_info(revise)["prompt_tokens"] == 2000 and wb.program_info(revise)["facts_chars"] == 50
    assert wb.program_info(base) == {}


def test_second_rows_refine_shape():
    base, _, refine, key = two_pass_books()
    rows1, rows2 = wb.second_rows(refine, key, base)
    by1, by2 = {r["address"]: r for r in rows1}, {r["address"]: r for r in rows2}
    assert by2["0x10"]["renamed"] and by2["0x10"]["f1"] == pytest.approx(0.5)       # parse_options -> handle_options
    assert by2["0x20"]["read_again"] and not by2["0x20"]["renamed"]
    assert by2["0x40"]["second_status"] == "failed" and not by2["0x40"]["renamed"]
    # --refine adds its tokens to the function's: the second pass's are the difference with the base book,
    # and the rows carry the first pass's own.
    assert by2["0x10"]["second"] == {"seconds": 6.0, "prompt_tokens": 700, "completion_tokens": 200}
    assert by1["0x10"]["prompt_tokens"] == by2["0x10"]["prompt_tokens"] == 100
    _, rows2_nobase = wb.second_rows(refine, key)
    assert {r["address"]: r for r in rows2_nobase}["0x10"]["second"]["prompt_tokens"] is None
    s = wb.second_stats(rows1, rows2)
    assert (s["read"], s["read_done"], s["read_failed"], s["renamed"]) == (3, 2, 1, 1)
    assert s["renamed_f1"] == {"n": 1, "better": 0, "worse": 0, "same": 1}
    assert wb.program_info(refine) == {"summary": "A tool.", "seconds": 4.2}


def test_judgements_reused_and_unchanged_names_identical():
    base, revise, _, key = two_pass_books()
    rows1, rows2 = wb.second_rows(revise, key, base)
    # The baseline judged parse_options and is_console_handle (with the baseline's summary).
    cache = wb.judge_cache([{"runs": [{"binary": "x", "functions": [
        {"address": "0x10", "given": "parse_options", "judge": "related", "judge_text": "related"},
        {"address": "0x20", "given": "is_console_handle", "judge": "same", "judge_text": "same"}]}]}])
    asked = []

    def ask(r):
        asked.append(r["given"])
        return "different", "different"
    n = wb.fill_judgements("x", rows1, rows2, cache, ask)
    by1, by2 = {r["address"]: r for r in rows1}, {r["address"]: r for r in rows2}
    assert n == 1 and asked == ["process_options"]                  # only the new name with a real name
    assert by1["0x10"]["judge"] == "related" and by2["0x10"]["judge"] == "different"
    assert by1["0x20"]["judge"] == by2["0x20"]["judge"] == "same"   # unchanged name, changed summary: same answer
    assert by2["0x40"]["judge"] is None                              # no real name: never judged
    rows1b, rows2b = wb.second_rows(revise, key, base)               # again, the cache filled: nothing asked
    assert wb.fill_judgements("x", rows1b, rows2b, cache, ask) == 0 and len(asked) == 1
    for r in rows1 + rows2:
        r["binary"] = "x"
    p = wb.pass_pairs(rows1, rows2)
    assert p["all"]["f1"]["n"] == 2 and p["all"]["f1"]["diff"] == pytest.approx(0.25)
    assert p["leaf"]["f1"]["diff"] == 0 and p["leaf"]["judge"]["diff"] == 0   # unchanged: identical scores
    assert p["renamed"]["f1"]["n"] == 1 and p["read again"]["f1"]["n"] == 2


def second_result(book, key, base, label="r", rep=1):
    run = wb.second_run("x", book, key, base)
    run.update(repeat=rep, base_book_costs=wb.base_costs(base))
    return {"label": label, "kind": "second_pass", "pass_args": ["--revise"], "runs": [run]}


def test_rescore_second_and_summary():
    base, revise, _, key = two_pass_books()
    result = second_result(revise, key, base)
    wb.rescore_second(result)
    assert result["aggregate_pass1"]["all"]["f1"] == pytest.approx(0.75)
    assert result["aggregate"]["all"]["f1"] == pytest.approx(1.0)
    assert result["paired"]["1"]["all"]["f1"]["diff"] == pytest.approx(0.25)
    assert result["second"]["renamed"] == 2 and "x" in result["by_binary_pass1"]
    text = wb.summarize_second(result)
    assert "pass 2 minus pass 1" in text and "renamed / read / named" in text and "program x#1" in text
    # Rescored from the kept book with keys: the same numbers, the judge's answers kept.
    result["runs"][0]["pass1"][0]["judge"] = "same"
    wb.rescore_second(result, {"x": key})
    assert result["aggregate"]["all"]["f1"] == pytest.approx(1.0)
    assert result["runs"][0]["functions"][0]["second"]["prompt_tokens"] == 900
    assert result["runs"][0]["pass1"][0]["judge"] == "same"


def test_first_pass_check():
    import copy
    base, revise, _, key = two_pass_books()
    assert wb.first_pass_check(base, revise, "  to do    0 functions, leaves first\n")["clean"]
    assert not wb.first_pass_check(base, revise, "")["clean"]                      # no `to do` line: unknown
    bad = copy.deepcopy(revise)
    bad["functions"]["0x20"]["seconds"] = 99.0                                      # named again by a first pass
    bad["functions"]["0x60"] = {**fn("done"), "name": "new_one", "summary": "s"}
    chk = wb.first_pass_check(base, bad, "to do    1 functions")
    assert not chk["clean"] and chk["first_cost_changed"] == ["0x20"] and chk["added"] == ["0x60"]


def test_compare_two_second_passes(tmp_path, capsys):
    base, revise, refine, key = two_pass_books()
    ra, rb = second_result(revise, key, base, "revise"), second_result(refine, key, base, "refine")
    for r in (ra, rb):
        wb.rescore_second(r)
    pa, pb = tmp_path / "a.json", tmp_path / "b.json"
    pa.write_text(wb.json.dumps(ra))
    pb.write_text(wb.json.dumps(rb))
    assert wb.main(["compare", str(pa), str(pb)]) == 0
    out = capsys.readouterr().out
    assert "minus A (revise)" in out and "only the functions both read again" in out
    assert "the same given name in both: 1 of 4" in out                           # 0x20, kept by both
    assert wb.main(["compare", str(pa), str(pb), "--pass1", "ab"]) == 0           # the same first reading
    out = capsys.readouterr().out
    assert "+0.000" in out and "-0.0" not in out.replace("-0.000..", "")
    assert wb.main(["score", str(pa)]) == 0                                        # a second-pass result rescored
    assert "pass 1 (the first reading)" in capsys.readouterr().out


def test_book_command_two_pass(tmp_path, capsys):
    base, revise, _, key = two_pass_books()
    key = {**key, "binary": "x.exe"}
    for name, data in (("book.json", revise), ("base.json", base), ("key.json", key)):
        (tmp_path / name).write_text(wb.json.dumps(data))
    assert wb.main(["book", str(tmp_path / "book.json"), str(tmp_path / "key.json"), "--base-book",
                    str(tmp_path / "base.json"), "--out", str(tmp_path / "out.json")]) == 0
    assert "what the second pass did: 3 of 3" in capsys.readouterr().out
    assert wb.json.loads((tmp_path / "out.json").read_text())["kind"] == "second_pass"


def test_tree_state_imports_that_tree():
    s = wb.tree_state(wb.ROOT)
    assert s["ok"] and s["commit"] and Path(s["imports"]).resolve().parent == (wb.ROOT / "mafdet").resolve()
    env = wb.tree_env(Path("T:/tree"), Path("T:/home"))
    assert env["PYTHONPATH"].startswith(str(Path("T:/tree"))) and env["MAFDET_HOME"] == str(Path("T:/home"))


def test_second_runner_on_a_copied_home(tmp_path, monkeypatch, capsys):
    """`second` end to end with the walk faked: the base home is copied (never written), the walk runs in
    the copy from --tree with the pass's options, the judge reuses the base result's answers."""
    base, revise, _, key = two_pass_books()
    binary = tmp_path / "x.exe"
    binary.write_bytes(b"MZ fake")
    key = {**key, "binary": "x.exe", "sha256": wb._sha256(binary)}
    (tmp_path / "key.json").write_text(wb.json.dumps(key))
    (tmp_path / "set.json").write_text(wb.json.dumps({"binaries": [{"name": "x", "path": str(binary),
                                                                     "key": "key.json", "program": "x.exe"}]}))
    src_book = tmp_path / "from" / "x-r1" / "home" / "binaries" / "abcd"
    src_book.mkdir(parents=True)
    (src_book / "book.json").write_text(wb.json.dumps(base))
    (tmp_path / "from" / "x-r1" / "home" / "mcp.json").write_text("{}")
    base_result = {"label": "baseline", "model": "m", "alias": "m", "ctx": 8192, "server": "rea",
                   "walk_args": ["--no-think"], "runs": [{"binary": "x", "repeat": 1, "functions": [
                       {"address": "0x10", "given": "parse_options", "judge": "related"},
                       {"address": "0x20", "given": "is_console_handle", "judge": "same"}]}]}
    (tmp_path / "base.json").write_text(wb.json.dumps(base_result))
    calls = []

    def fake_call(cmd, cwd, env, stdout, stderr):
        calls.append((cmd, cwd, env))
        home = Path(env["MAFDET_HOME"])
        assert (home / "mcp.json").is_file()
        stdout.write("  to do    0 functions, leaves first\n")
        (home / "binaries" / "abcd" / "book.json").write_text(wb.json.dumps(revise))
        return 0
    monkeypatch.setattr(wb.subprocess, "call", fake_call)
    monkeypatch.setattr(wb, "judge_one", lambda *a: ("different", "different"))
    out = tmp_path / "result.json"
    assert wb.main(["second", "t", "--from", str(tmp_path / "from"), "--base-result", str(tmp_path / "base.json"),
                    "--work", str(tmp_path / "work"), "--set", str(tmp_path / "set.json"), "--url", "http://x",
                    "--pass-args=--revise --revise-no-callers", "--repeat", "2", "--out", str(out)]) == 0
    assert len(calls) == 2
    cmd, cwd, env = calls[0]
    assert cmd[-4:] == ["--no-think", "--revise", "--revise-no-callers",       # the base's options, then the pass's,
                        "--revise-draft"]                                      # then --revise's old default spelled out
    assert cmd[cmd.index("--ctx") + 1] == "8192" and cmd[cmd.index("--model") + 1] == "m"
    assert cwd == str(wb.ROOT) and env["PYTHONPATH"].startswith(str(wb.ROOT))
    assert wb.json.loads((src_book / "book.json").read_text()) == base         # the base home is not written
    assert (tmp_path / "work" / "x-r1" / "book.json").is_file() and (tmp_path / "work" / "x-r2" / "book.json").is_file()
    r = wb.json.loads(out.read_text())
    assert r["kind"] == "second_pass" and r["tree"]["ok"] and r["pass_args"] == ["--revise", "--revise-no-callers"]
    assert [run["repeat"] for run in r["runs"]] == [1, 2] and all(run["first_pass_check"]["clean"] for run in r["runs"])
    assert r["runs"][0]["judged"] == 1 and r["runs"][1]["judged"] == 0      # process_options asked once
    assert r["runs"][0]["walk_runs"] == [{"revised": 3}]
    assert "what the second pass did" in capsys.readouterr().out
    with pytest.raises(SystemExit):                                          # never into the folder it copies
        wb.walk_second(binary, tmp_path / "from" / "x-r1", tmp_path / "from" / "x-r1" / "sub", "u", "m", 1, [],
                       "rea", wb.ROOT)


# ---------------------------------------------------------------- the names-only judge (rejudge)

def test_the_names_only_judge_does_not_read_the_summary(monkeypatch):
    sent = []

    class Answer:
        def __enter__(self):
            return self

        def __exit__(self, *a):
            return False

        def read(self):
            return wb.json.dumps({"choices": [{"message": {"content": "Related."}}]}).encode()

    def urlopen(req, timeout=0):
        sent.append(wb.json.loads(req.data))
        return Answer()
    monkeypatch.setattr(wb.urllib.request, "urlopen", urlopen)
    assert wb.judge_name_one("http://x", "m", "where.exe, a tool", "CWhere::Search", "search_files") == \
        ("related", "Related.")
    user = sent[0]["messages"][1]["content"]
    assert "CWhere::Search" in user and "search_files" in user and "where.exe, a tool" in user
    assert "summary" not in user.lower() and sent[0]["messages"][0]["content"] == wb.JUDGE_SYSTEM
    assert sent[0]["temperature"] == 0.0 and sent[0]["chat_template_kwargs"] == {"enable_thinking": False}


def first_pass_result(label, given10="parse_options"):
    book, key = book_and_key()
    book["functions"]["0x10"]["name"] = given10
    rows = wb.score_book(book, key)
    for r in rows:
        if r["real"] and r["status"] == "done":
            r["judge"], r["judge_text"] = "related", "related"
    result = {"label": label, "runs": [{"binary": "x", "repeat": 1, "functions": rows, "book": None}]}
    return wb.rescore(result)


def fake_engine(monkeypatch, answers=None):
    asked = []

    def ask(url, model, program, real, given):
        asked.append((program, real, given))
        return (answers or {}).get(given, "same"), "same"

    class Up:
        def close(self):
            pass
    monkeypatch.setattr(wb, "judge_name_one", ask)
    monkeypatch.setattr(wb.urllib.request, "urlopen", lambda *a, **k: Up())
    return asked


def write_set(tmp_path):
    (tmp_path / "set.json").write_text(wb.json.dumps({"binaries": [{"name": "x", "path": "x.exe", "key": "k.json",
                                                                     "program": "x.exe, a tool"}]}))
    return str(tmp_path / "set.json")


def test_a_result_without_the_names_only_judge_is_as_before():
    result = first_pass_result("a")
    assert "judge_name" not in result["aggregate"]["all"] and "judge_name_n" not in result["aggregate"]["all"]
    assert "names-only" not in wb.summarize(result)
    assert all("judge_name" not in r for r in result["runs"][0]["functions"])


def test_rejudge_first_pass_results(tmp_path, monkeypatch, capsys):
    pa, pb = tmp_path / "a.json", tmp_path / "b.json"
    pa.write_text(wb.json.dumps(first_pass_result("a")))
    pb.write_text(wb.json.dumps(first_pass_result("b", "process_options")))
    asked = fake_engine(monkeypatch, {"parse_options": "different"})
    before = pa.read_text()
    assert wb.main(["rejudge", str(pa), str(pb), "--names-only", "--dry-run", "--set", write_set(tmp_path)]) == 0
    assert "3 names to ask in all" in capsys.readouterr().out and pa.read_text() == before and not asked
    assert wb.main(["rejudge", str(pa), str(pb), "--names-only", "--set", write_set(tmp_path)]) == 0
    # is_console_handle is the same name in both files: asked once; the program from the set file.
    assert sorted(g for _, _, g in asked) == ["is_console_handle", "parse_options", "process_options"]
    assert {p for p, _, _ in asked} == {"x.exe, a tool"}
    ra = wb.json.loads(pa.read_text())
    rows = {r["address"]: r for r in ra["runs"][0]["functions"]}
    assert rows["0x10"]["judge_name"] == "different" and rows["0x10"]["judge"] == "related"   # beside, not instead
    assert ra["aggregate"]["all"]["judge_name_score"] == 0.5 and ra["aggregate"]["all"]["judge_name_n"] == 2
    assert ra["aggregate"]["all"]["judge_score"] == 0.5 and ra["judge_name"]["only_changed"] is False
    assert "the names-only judge" in wb.summarize(ra)
    # Again: everything answered, nothing asked.
    assert wb.main(["rejudge", str(pa), str(pb), "--names-only", "--set", write_set(tmp_path)]) == 0
    assert len(asked) == 3
    # compare: the names-only block, only because both sides have it.
    capsys.readouterr()
    assert wb.main(["compare", str(pa), str(pb)]) == 0
    out = capsys.readouterr().out
    assert "the names-only judge" in out and "names-only judge 0.500 -> 1.000 over 2 judged; +0.500" in out
    # score keeps the answers.
    assert wb.main(["score", str(pa)]) == 0
    assert "the names-only judge" in capsys.readouterr().out
    with pytest.raises(SystemExit):
        wb.main(["rejudge", str(pa)])                                                # --names-only is required


def test_rejudge_second_pass_all_and_only_changed(tmp_path, monkeypatch, capsys):
    base, revise, _, key = two_pass_books()
    result = second_result(revise, key, base)
    wb.rescore_second(result)
    p = tmp_path / "r.json"
    p.write_text(wb.json.dumps(result))
    asked = fake_engine(monkeypatch, {"parse_options": "related", "process_options": "same",
                                      "is_console_handle": "same"})
    # --only-changed: only 0x10 changed with a real name (0x40 has none): its two names.
    assert wb.main(["rejudge", str(p), "--names-only", "--only-changed", "--set", write_set(tmp_path)]) == 0
    assert sorted(g for _, _, g in asked) == ["parse_options", "process_options"]
    r = wb.json.loads(p.read_text())
    pp = r["paired"]["1"]["all"]["judge_name"]
    # 0x10 judged on both sides (+0.5), 0x20 the same name judged on neither: counted 0.
    assert (pp["n"], pp["judged"], pp["filled"], pp["diff"], pp["diff_judged"]) == (2, 1, 1, 0.25, 0.5)
    text = wb.summarize_second(r)
    assert "answered 1 of 2 pass-2 names" in text and "1 unchanged names not judged counted as 0" in text
    assert r["second"]["renamed_judge_name"] == {"n": 1, "better": 1, "worse": 0, "same": 0}
    # The rest later, without --only-changed: only is_console_handle is new; pass 2 shares its answer.
    assert wb.main(["rejudge", str(p), "--names-only", "--set", write_set(tmp_path)]) == 0
    assert sorted(g for _, _, g in asked) == ["is_console_handle", "parse_options", "process_options"]
    r = wb.json.loads(p.read_text())
    by2 = {x["address"]: x for x in r["runs"][0]["functions"]}
    by1 = {x["address"]: x for x in r["runs"][0]["pass1"]}
    assert by1["0x20"]["judge_name"] == by2["0x20"]["judge_name"] == "same"
    pp = r["paired"]["1"]["all"]["judge_name"]
    assert (pp["n"], pp["judged"], pp["filled"], pp["diff"]) == (2, 2, 0, 0.25)
    assert "names-only judge answered" not in wb.summarize_second(r)
    # Rescored from the book with keys: the names-only answers stay.
    wb.rescore_second(r, {"x": key})
    assert {x["address"]: x for x in r["runs"][0]["functions"]}["0x10"]["judge_name"] == "same"
    # Two second passes compared: the names-only lines in compare.
    q = tmp_path / "q.json"
    q.write_text(p.read_text())
    capsys.readouterr()
    assert wb.main(["compare", str(p), str(q)]) == 0
    assert "names-only judge" in capsys.readouterr().out


def test_calibrate_with_the_names_only_judge(tmp_path, capsys):
    rows = [{"address": a, "real": "X", "status": "done", "f1": f, "judge": j, "judge_name": n, "given": g,
             "layer": layer} for a, f, j, n, g, layer in [
        ("0x1", 0.9, "same", "same", "a1", "leaf"), ("0x2", 0.5, "related", "different", "a2", "top"),
        ("0x3", 0.0, "same", "related", "a3", "top"), ("0x4", 0.0, "different", "different", "a4", "leaf"),
        ("0x5", 0.0, "different", None, "a5", "leaf")]]
    labels = {"x": {"0x1": {"label": "right", "given": "a1"}, "0x2": {"label": "vague", "given": "a2"},
                    "0x3": {"label": "vague", "given": "a3"}, "0x4": {"label": "wrong", "given": "a4"},
                    "0x5": {"label": "wrong", "given": "a5"}}}
    (tmp_path / "r.json").write_text(wb.json.dumps({"label": "r", "runs": [{"binary": "x", "repeat": 1,
                                                                             "functions": rows}]}))
    (tmp_path / "l.json").write_text(wb.json.dumps(labels))
    c = wb.calibration(rows, {r["address"]: labels["x"][r["address"]]["label"] for r in rows}, "judge_name")
    assert c["judge_by_label"]["vague"] == {"same": 0, "related": 1, "different": 1} and c["judge_exact"] == 0.75
    assert wb.main(["calibrate", str(tmp_path / "r.json"), "--labels", str(tmp_path / "l.json"),
                    "--judge-field", "both", "--by-layer"]) == 0
    out = capsys.readouterr().out.splitlines()
    assert out[0].startswith("pooled [with summary]: n 4") and out[1].startswith("pooled [names only]: n 4")
    assert any(line.startswith("top [names only]: n 2") for line in out)
    # The default is as before: the judge with the summary, all labelled names.
    assert wb.main(["calibrate", str(tmp_path / "r.json"), "--labels", str(tmp_path / "l.json")]) == 0
    assert capsys.readouterr().out.startswith("r x#1: n 5")


def test_pinned_spells_out_the_old_defaults(tmp_path):
    """Since 2026-11-14 the walk goes without thinking and --revise hides the first name: a recorded walk_args
    gets the old defaults spelled out, but only for a tree whose command line knows those switches."""
    new, old = tmp_path / "new", tmp_path / "old"
    (new / "mafdet").mkdir(parents=True)
    (old / "mafdet").mkdir(parents=True)
    (new / "mafdet" / "cli.py").write_text('p.add_argument("--think")\np.add_argument("--revise-draft")\n', encoding="utf-8")
    (old / "mafdet" / "cli.py").write_text('p.add_argument("--no-think")\n', encoding="utf-8")
    assert wb.pinned([], new) == ["--think"]
    assert wb.pinned(["--revise"], new) == ["--revise", "--think", "--revise-draft"]
    assert wb.pinned(["--revise", "--revise-no-draft", "--no-think"], new) == ["--revise", "--revise-no-draft", "--no-think"]
    assert wb.pinned(["--refine"], new) == ["--refine", "--think"]
    assert wb.pinned(["--revise"], old) == ["--revise"]
