#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""真实文件夹验证流程（源目录全程只读，且不删除任何已有内容）。

用法：
    python examples/verify_real_folder.py
    python examples/verify_real_folder.py --no-pause          # 一次跑完，不暂停
    python examples/verify_real_folder.py --source "D:\\别的目录"

流程分 7 个阶段，每阶段结束暂停等你按回车 —— 正好逐屏截图。
所有读写都发生在项目内的 _demo/真实截图验证/ 副本上；
源文件夹只被读取，绝不会被移动、改名、写入或创建子目录。
重复运行时不会删除旧副本，而是自动新建一个带时间戳的副本目录。
"""
from __future__ import annotations

import argparse
import collections
import datetime as dt
import os
import shutil
import subprocess
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
ROOT = HERE.parent
MAIN = ROOT / "organize_photos.py"
DEFAULT_SOURCE = Path(r"C:\Users\lenovo\Pictures\联想截图")
WORK = ROOT / "_demo" / "真实截图验证"
COPY_ROOT = WORK / "联想截图"

PHOTO_EXTS = {
    ".jpg", ".jpeg", ".jpe", ".jfif", ".png", ".heic", ".heif",
    ".tif", ".tiff", ".bmp", ".gif", ".webp", ".avif",
    ".dng", ".cr2", ".nef", ".arw", ".raw",
}


# ---------------------------------------------------------------------------
def setup_console() -> None:
    for stream in (sys.stdout, sys.stderr):
        try:
            stream.reconfigure(encoding="utf-8", errors="replace")
        except (AttributeError, ValueError, OSError):
            pass
    if os.name == "nt":
        try:
            import ctypes
            ctypes.windll.kernel32.SetConsoleOutputCP(65001)
            ctypes.windll.kernel32.SetConsoleCP(65001)
        except Exception:
            pass


def emit(text: str = "") -> None:
    try:
        print(text, flush=True)
    except UnicodeEncodeError:
        print(text.encode("ascii", "replace").decode("ascii"), flush=True)


def stage(no: int, title: str) -> None:
    emit("")
    emit("=" * 78)
    emit("  STAGE %d  |  %s" % (no, title))
    emit("=" * 78)


def hold(no_pause: bool, hint: str = "现在可以截图；按回车继续下一阶段 ...") -> None:
    if no_pause:
        return
    emit("")
    try:
        input("  >>> " + hint)
    except (EOFError, KeyboardInterrupt):
        emit("  （无交互输入，自动继续）")


def run_main(extra: list[str]) -> int:
    """调用主程序；输出直接继承到当前窗口，便于截图。"""
    cmd = [sys.executable, str(MAIN), str(COPY_ROOT)] + extra
    emit("  $ " + " ".join(cmd))
    emit("")
    return subprocess.run(cmd, cwd=str(ROOT)).returncode


def source_snapshot(src: Path) -> dict[str, tuple[int, int]]:
    return {
        p.name: (p.stat().st_size, int(p.stat().st_mtime))
        for p in src.iterdir() if p.is_file()
    }


def expected_dates(src: Path) -> dict[str, int]:
    """按文件修改时间推算预计的 年/月/日 分组（PNG 截图无 EXIF 时即为此结果）。"""
    g: dict[str, int] = collections.Counter()
    for p in src.iterdir():
        if p.is_file() and p.suffix.lower() in PHOTO_EXTS:
            m = dt.datetime.fromtimestamp(p.stat().st_mtime)
            g[m.strftime("%Y/%m/%d")] += 1
    return dict(g)


def is_date_dir(p: Path, base: Path) -> bool:
    try:
        rel = p.relative_to(base).parts
    except ValueError:
        return False
    return len(rel) == 3 and all(x.isdigit() for x in rel)


def print_tree(base: Path) -> None:
    emit("  目录结构（只列目录与每个日期的张数，不逐张列文件名）：")
    emit("")
    emit("  %s/" % base.name)
    years = sorted([p for p in base.iterdir() if p.is_dir() and p.name.isdigit()])
    for y in years:
        emit("  +-- %s/" % y.name)
        months = sorted([p for p in y.iterdir() if p.is_dir() and p.name.isdigit()])
        for idx, mo in enumerate(months):
            branch = "\\--" if idx == len(months) - 1 else "+--"
            emit("  |   %s %s/" % (branch, mo.name))
            days = sorted([p for p in mo.iterdir() if p.is_dir() and p.name.isdigit()])
            for j, day in enumerate(days):
                n = sum(1 for f in day.iterdir()
                        if f.is_file() and f.suffix.lower() in PHOTO_EXTS)
                dbranch = "\\--" if j == len(days) - 1 else "+--"
                pad = "    " if idx == len(months) - 1 else "|   "
                emit("  |   %s   %s %s/  ......... %d 张" % (pad, dbranch, day.name, n))
    others = [p for p in base.iterdir()
              if p.is_dir() and not p.name.isdigit()]
    for o in others:
        n = sum(1 for f in o.rglob("*") if f.is_file())
        emit("  +-- %s/  ......... %d 个文件" % (o.name, n))
    files = [p for p in base.iterdir() if p.is_file()]
    for f in sorted(files):
        emit("  +-- %s" % f.name)
    emit("")


# ---------------------------------------------------------------------------
def main() -> int:
    global COPY_ROOT

    ap = argparse.ArgumentParser(description="用真实文件夹验证照片整理工具（源目录只读）")
    ap.add_argument("--source", default=str(DEFAULT_SOURCE), help="要验证的真实文件夹")
    ap.add_argument("--no-pause", action="store_true", help="不暂停，一次跑完")
    args = ap.parse_args()
    no_pause = args.no_pause
    src = Path(args.source)

    setup_console()

    emit("=" * 78)
    emit("  真实数据验证  |  Photo Date Organizer")
    emit("  源文件夹全程只读；所有操作只在项目内的副本上进行")
    emit("=" * 78)
    emit("  运行时间   : " + dt.datetime.now().strftime("%Y-%m-%d %H:%M:%S"))
    emit("  Python     : " + sys.version.split()[0])
    emit("  项目根目录 : " + str(ROOT))
    emit("  源文件夹   : " + str(src))
    emit("  验证副本   : " + str(COPY_ROOT))

    # ---------------- STAGE 1 ----------------
    stage(1, "只读体检源文件夹（不改动任何文件）")
    if not src.is_dir():
        emit("  [失败] 源文件夹不存在：" + str(src))
        emit("  修复：用 --source 传入一个存在的文件夹路径。")
        return 2
    before = source_snapshot(src)
    photos = [p for p in src.iterdir()
              if p.is_file() and p.suffix.lower() in PHOTO_EXTS]
    subdirs = [p for p in src.iterdir() if p.is_dir()]
    exif_cnt = 0
    for p in photos:
        try:
            if b"eXIf" in p.read_bytes()[:4096]:
                exif_cnt += 1
        except OSError:
            pass
    exp = expected_dates(src)
    years = sorted({k.split("/")[0] for k in exp})
    months = sorted({k.rsplit("/", 1)[0] for k in exp})
    emit("  文件总数           : %d" % len(before))
    emit("  识别为照片         : %d" % len(photos))
    emit("  子文件夹数         : %d" % len(subdirs))
    emit("  含 EXIF 的照片     : %d  （0 表示全部按文件修改时间兜底）" % exif_cnt)
    emit("  预计年份目录       : %d   %s" % (len(years), years))
    emit("  预计月份目录       : %d   %s" % (len(months), months))
    emit("  预计日期目录       : %d" % len(exp))
    if exp:
        ks = sorted(exp)
        emit("  时间跨度           : %s  ~  %s" % (ks[0], ks[-1]))
        emit("  单日最多           : %d 张（%s）" %
             (max(exp.values()), max(exp, key=exp.get)))
    emit("")
    emit("  结论：本次验证覆盖「大规模真实 PNG 截图 + 文件时间兜底 + 年月日分层」；")
    emit("        EXIF 读取与异常日期隔离由 examples/run_demo.py 的合成数据覆盖。")
    hold(no_pause, "截图点 A：体检数字。按回车继续 ...")

    # ---------------- STAGE 2 ----------------
    stage(2, "复制副本（源文件夹只读，副本在 _demo 下且已被 git 忽略）")
    WORK.mkdir(parents=True, exist_ok=True)
    if COPY_ROOT.exists():
        stamp = dt.datetime.now().strftime("%Y%m%d_%H%M%S")
        candidate = WORK / ("联想截图_" + stamp)
        k = 1
        while candidate.exists():
            candidate = WORK / ("联想截图_%s_%d" % (stamp, k))
            k += 1
        COPY_ROOT = candidate
        emit("  检测到已有副本；为不删除任何内容，本次改用新副本：")
        emit("  " + str(COPY_ROOT))
    shutil.copytree(src, COPY_ROOT)
    copied = sum(1 for _ in COPY_ROOT.iterdir() if _.is_file())
    emit("  已复制 %d 个文件到副本；未删除或覆盖任何已有内容。" % copied)
    emit("  源文件夹未发生任何写入；后续所有命令的目标都是副本。")
    emit("  >>> 重要：千万不要把主程序直接指向源文件夹运行（连 --dry-run 也会写运行记录）。")
    hold(no_pause, "截图点 B：复制完成。按回车继续 ...")

    # ---------------- STAGE 3 ----------------
    stage(3, "预演 --dry-run（不创建文件夹、不移动文件）")
    rc = run_main(["--dry-run"])
    emit("")
    emit("  预演退出码: %d  （0 表示正常；此时副本仍是扁平的一堆 PNG）" % rc)
    flat_now = sum(1 for p in COPY_ROOT.iterdir() if p.is_file())
    emit("  副本顶层文件数仍为 %d，目录数 %d —— 确认预演没有改动任何文件。" %
         (flat_now, sum(1 for p in COPY_ROOT.iterdir() if p.is_dir())))
    hold(no_pause, "截图点 C：预演输出（推荐截图）。按回车继续 ...")

    # ---------------- STAGE 4 ----------------
    stage(4, "正式运行（在副本上按 年/月/日 分层归档）")
    rc = run_main([])
    emit("")
    emit("  正式运行退出码: %d" % rc)
    emit("  预期看到：>>> 一共整理了 %d 张照片 <<<" % len(photos))
    hold(no_pause, "截图点 D：整理结果（强烈推荐截图）。按回车继续 ...")

    # ---------------- STAGE 5 ----------------
    stage(5, "实际生成的目录结构 + 自动验收")
    print_tree(COPY_ROOT)

    checks: list[tuple[str, bool, str]] = []

    after = source_snapshot(src)
    checks.append(("源文件夹文件数未被改动", before == after,
                   "%d -> %d" % (len(before), len(after))))
    after_subdirs = [p for p in src.iterdir() if p.is_dir()]
    checks.append(("源文件夹未产生子目录", len(after_subdirs) == len(subdirs),
                   "%d 个子目录" % len(after_subdirs)))

    year_dirs = sorted([p for p in COPY_ROOT.iterdir()
                        if p.is_dir() and p.name.isdigit()])
    month_dirs = sorted([p for y in year_dirs for p in y.iterdir()
                         if p.is_dir() and p.name.isdigit()])
    date_dirs = sorted([p for m in month_dirs for p in m.iterdir()
                        if p.is_dir() and p.name.isdigit()])
    checks.append(("年份目录数符合预期", len(year_dirs) == len(years),
                   "实际 %d / 预期 %d" % (len(year_dirs), len(years))))
    checks.append(("月份目录数符合预期", len(month_dirs) == len(months),
                   "实际 %d / 预期 %d" % (len(month_dirs), len(months))))
    checks.append(("日期目录数符合预期", len(date_dirs) == len(exp),
                   "实际 %d / 预期 %d" % (len(date_dirs), len(exp))))

    empty_dates = [p for p in date_dirs
                   if not any(f.is_file() for f in p.iterdir())]
    checks.append(("没有空的日期目录", len(empty_dates) == 0,
                   "空日期目录 %d 个" % len(empty_dates)))

    all_dirs = [p for p in COPY_ROOT.rglob("*") if p.is_dir()
                and "_待处理" not in p.name]
    empty_any = [p for p in all_dirs
                 if not any(p.iterdir())]
    checks.append(("整棵目录树没有空文件夹", len(empty_any) == 0,
                   "空目录 %d 个" % len(empty_any)))

    moved = [p for p in COPY_ROOT.rglob("*")
             if p.is_file() and p.suffix.lower() in PHOTO_EXTS
             and "_待处理" not in str(p)]
    checks.append(("照片总数一致（无丢失）", len(moved) == len(photos),
                   "实际 %d / 原始 %d" % (len(moved), len(photos))))

    left_flat = [p for p in COPY_ROOT.iterdir()
                 if p.is_file() and p.suffix.lower() in PHOTO_EXTS]
    checks.append(("顶层已无散落照片", len(left_flat) == 0,
                   "剩余 %d 张" % len(left_flat)))

    quarantine = COPY_ROOT / "_待处理_日期异常"
    checks.append(("无异常日期照片（隔离夹未创建）", not quarantine.exists(),
                   "存在" if quarantine.exists() else "未创建"))

    reports = sorted(COPY_ROOT.glob("照片整理报告_*.md"))
    has_tree = False
    if reports:
        has_tree = "实际生成的目录结构" in reports[-1].read_text(encoding="utf-8")
    checks.append(("Markdown 报告含目录树章节", has_tree,
                   reports[-1].name if reports else "未找到报告"))

    emit("  验收结果：")
    emit("  " + "-" * 74)
    passed = 0
    for name, ok, detail in checks:
        passed += int(ok)
        emit("  [%s] %-30s %s" % ("PASS" if ok else "FAIL", name, detail))
    emit("  " + "-" * 74)
    emit("  >>> %d / %d 项通过 <<<" % (passed, len(checks)))
    hold(no_pause, "截图点 E：目录树 + 验收结果（最有说服力，强烈推荐截图）。按回车继续 ...")

    # ---------------- STAGE 6 ----------------
    stage(6, "重复运行（幂等性：不应再搬运任何文件）")
    run_main([])
    emit("")
    emit("  预期：0 张移动、%d 张「已在位」。" % len(photos))
    hold(no_pause, "截图点 F：幂等复跑。按回车继续 ...")

    # ---------------- STAGE 7 ----------------
    stage(7, "验证完成 · 截图与后续指引")
    emit("  建议保存这 3 张截图到项目的 docs/ 目录：")
    emit("    1) STAGE 4  —— 一共整理了 %d 张照片 + 按日期明细" % len(photos))
    emit("    2) STAGE 5  —— 年/月/日 三级目录树 + 验收 %d/%d 通过" % (passed, len(checks)))
    emit("    3) STAGE 6  —— 重复运行 0 移动（幂等）")
    emit("")
    emit("  报告文件：" + (str(reports[-1]) if reports else "未生成"))
    emit("  验证副本：" + str(COPY_ROOT))
    emit("  源文件夹：" + str(src) + "  （全程只读，未被改动）")
    emit("")
    emit("  副本位于 _demo/ 下，已被 .gitignore 排除，不会被提交到 GitHub。")
    emit("  本脚本不删除任何已有内容；再次运行会自动新建一个带时间戳的副本。")
    emit("")
    emit("=" * 78)
    emit("  验证结束：" + ("全部通过" if passed == len(checks) else "存在未通过项，请检查上方 FAIL 行"))
    emit("=" * 78)

    return 0 if passed == len(checks) else 1


if __name__ == "__main__":
    raise SystemExit(main())
