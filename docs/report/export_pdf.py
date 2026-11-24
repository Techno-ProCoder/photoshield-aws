"""Exports a report .docx to PDF with Microsoft Word, then puts the full-resolution figures
back into the PDF (Word downsamples pictures on export) and prints the page count.

Usage: python docs/report/export_pdf.py [path/to/report.docx]   (default: the public report)
"""
import os
import sys

import pymupdf
import win32com.client
from PIL import Image

HERE = os.path.dirname(os.path.abspath(__file__))
DOCS = os.path.dirname(HERE)
src = os.path.abspath(sys.argv[1]) if len(sys.argv) > 1 else os.path.join(HERE, "PhotoShield_Report.docx")
base = os.path.splitext(src)[0]
tmp = base + ".word.pdf"
dst = base + ".pdf"
FIGURES = [
    os.path.join(DOCS, "architecture.png"),
    os.path.join(DOCS, "results", "screenshot-result-crop.png"),
    os.path.join(DOCS, "results", "scaling.png"),
]

word = win32com.client.DispatchEx("Word.Application")
word.Visible = False
try:
    doc = word.Documents.Open(src, ReadOnly=True)
    pages = doc.ComputeStatistics(2)  # wdStatisticPages
    doc.ExportAsFixedFormat(OutputFileName=tmp, ExportFormat=17, OptimizeFor=0,
                            CreateBookmarks=1, DocStructureTags=True, BitmapMissingFonts=True)
    doc.Close(False)
finally:
    word.Quit()

ratios = {path: Image.open(path).size for path in FIGURES}
pdf = pymupdf.open(tmp)
replaced = []
for page in pdf:
    for xref, _smask, width, height, *_ in page.get_images(full=True):
        # Match each embedded picture to its original by aspect ratio.
        best = min(FIGURES, key=lambda p: abs(ratios[p][0] / ratios[p][1] - width / height))
        if abs(ratios[best][0] / ratios[best][1] - width / height) < 0.01:
            page.replace_image(xref, filename=best)
            replaced.append(f"p{page.number + 1}: {os.path.basename(best)} {width}x{height} -> {ratios[best][0]}x{ratios[best][1]}")
pdf.save(dst, garbage=3, deflate=True)
pdf.close()
os.remove(tmp)
print(f"Pages: {pages}")
print("\n".join(replaced))
print(f"Saved {dst}")
