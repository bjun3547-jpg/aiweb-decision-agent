# -*- coding: utf-8 -*-
"""把 docs/ch*.md 拼成一份可打印的技术文档 HTML。

为什么需要这个脚本：**公式和图注各有一个坑，只有用对参数才不翻车。**

1. 公式：第 5 章有 5 处 `$$...$$` 和若干行内 `$x_{e,d,s}$`。
   如果走 Python-Markdown（只有 tables/fenced_code 扩展），LaTeX 源码会**原样打印**，
   数学模型那一节变成一堆 `\\sum_{e,d} w_{e,d}`。必须用 pandoc 的 `--mathml`。
2. 图注：pandoc 默认开启 implicit_figures，会把 `![图 5 ...](x.png)` 的 alt
   变成 `<figcaption>`，而正文下面还有一行 `<p align="center"><i>图 5 ...</i></p>`，
   于是同一张图出现两遍图注。用 `-f markdown-implicit_figures` 关掉。

用法（仓库根目录执行）：

    .\\.venv\\Scripts\\python.exe scripts\\build_doc.py

产出 `docs/dist/AIWeb技术文档.html`，用 Chrome 打开 → Ctrl+P →
目标「另存为 PDF」、边距「默认」、勾选「背景图形」、取消「页眉和页脚」。

只想看各章字数 / 检查 pandoc 是否装了：

    .\\.venv\\Scripts\\python.exe scripts\\build_doc.py --check
"""
from __future__ import annotations

import re
import shutil
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
DOCS = ROOT / "docs"
DIST = DOCS / "dist"

# 与测量页数时用的完全一致：A4、25mm 页边距、小四宋体、1.5 倍行距。
# 改这里会让页数和之前实测的对不上，改前先想清楚。
CSS = """
@page { size: A4; margin: 25mm; }
body { font-family: "SimSun","宋体",serif; font-size: 12pt; line-height: 1.5; color: #000; }
h1 { font-family: "SimHei","黑体",sans-serif; font-size: 16pt; text-align: center; margin: 0 0 12pt; }
h2 { font-family: "SimHei","黑体",sans-serif; font-size: 14pt; margin: 12pt 0 6pt; }
h3 { font-family: "SimHei","黑体",sans-serif; font-size: 12.5pt; margin: 9pt 0 4pt; }
p { margin: 0 0 6pt; text-align: justify; }
blockquote { margin: 0 0 8pt; padding: 5pt 8pt; border-left: 3pt solid #ccc;
             background: #f7f7f7; font-size: 10.5pt; }
pre { font-family: Consolas,monospace; font-size: 7pt; line-height: 1.1;
      background: #fafafa; border: 0.5pt solid #ddd; padding: 3pt; margin: 5pt 0;
      white-space: pre-wrap; }
code { font-family: Consolas,monospace; font-size: 10pt; background: #f2f2f2; }
table { border-collapse: collapse; width: 100%; margin: 5pt 0; font-size: 10pt; }
th, td { border: 0.5pt solid #999; padding: 2.5pt 4pt; }
th { background: #eee; font-family: "SimHei",sans-serif; }
img { display: block; width: 100%; margin: 4pt auto 2pt; }
p[align="center"] { text-align: center; font-size: 10pt; margin: 0 0 6pt; }
hr { display: none; }
math { font-size: 11pt; }
.pagebreak { page-break-after: always; }
"""


def chapters() -> list[Path]:
    """按章号自然排序收集 docs/ch*.md，缺章不报错（第 8–10 章还没写）。"""
    found = []
    for p in DOCS.glob("ch*.md"):
        m = re.match(r"ch(\d+)", p.name)
        if m:
            found.append((int(m.group(1)), p))
    return [p for _, p in sorted(found)]


def cjk_len(text: str) -> int:
    return len(re.findall(r"[\u4e00-\u9fff]", text))


def main() -> int:
    check_only = "--check" in sys.argv

    files = chapters()
    if not files:
        print("没有找到 docs/ch*.md")
        return 1

    print(f"共 {len(files)} 章：")
    total = 0
    for p in files:
        n = cjk_len(p.read_text(encoding="utf-8"))
        total += n
        print(f"  {p.name:32} {n:>5} 中文字")
    print(f"  {'合计':32} {total:>5} 中文字")

    pandoc = shutil.which("pandoc")
    if not pandoc:
        print("\n✗ 没找到 pandoc。公式需要它才能渲染。")
        print("  Windows: winget install --id JohnMacFarlane.Pandoc")
        print("  macOS:   brew install pandoc")
        return 1
    ver = subprocess.run([pandoc, "--version"], capture_output=True, text=True).stdout.splitlines()[0]
    print(f"\n✓ {ver}")

    if check_only:
        return 0

    DIST.mkdir(exist_ok=True)
    parts: list[str] = []
    for i, p in enumerate(files):
        # -f markdown-implicit_figures：关掉 alt 自动变图注，否则和正文的
        # <p align="center"> 图注重复（实测同一张图出现两遍「图 5 ...」）
        # --mathml：把 LaTeX 转成 MathML，Chrome 原生支持，不需要 JS
        # --wrap=none：不按 72 列硬折行，否则中文表格会被切碎
        html = subprocess.run(
            [pandoc, str(p), "-f", "markdown-implicit_figures", "-t", "html",
             "--mathml", "--wrap=none"],
            capture_output=True, text=True, encoding="utf-8",
        )
        if html.returncode != 0:
            print(f"✗ {p.name} 转换失败：{html.stderr[:300]}")
            return 1
        parts.append(html.stdout)
        if i < len(files) - 1:
            parts.append('<div class="pagebreak"></div>')

    out = DIST / "AIWeb技术文档.html"
    out.write_text(
        '<!DOCTYPE html><html lang="zh-CN"><head><meta charset="utf-8">'
        f"<title>技术文档</title><style>{CSS}</style></head><body>"
        + "\n".join(parts)
        + "</body></html>",
        encoding="utf-8",
    )
    print(f"\n✓ 已生成 {out.relative_to(ROOT)}（{out.stat().st_size / 1024:.0f} KB）")
    print("  用 Chrome 打开 → Ctrl+P → 另存为 PDF")
    print("  边距选「默认」，勾选「背景图形」，取消「页眉和页脚」")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
