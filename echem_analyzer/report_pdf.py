"""PDF writer for a :class:`~echem_analyzer.report.Report` (Qt rich text -> paged PDF with a footer)."""
from __future__ import annotations

from html import escape
from pathlib import Path

from PySide6.QtCore import QMarginsF, QRectF, QSizeF, Qt, QUrl
from PySide6.QtGui import (
    QColor, QFont, QImage, QPageLayout, QPageSize, QPainter, QPdfWriter, QTextDocument,
)

from . import theme
from .report import FigureBlock, KeyValueBlock, NoteBlock, Report, Section, TableBlock

RES = 150                                   # dpi of the PDF page coordinate system
MARGIN_MM = (16.0, 16.0, 16.0, 18.0)        # left, top, right, bottom
INK, MUTED, ACCENT = "#18212F", "#5B6678", "#2563EB"
RULE, SOFT, WARN_BG, WARN_FG = "#E1E6ED", "#F1F4F8", "#FDF1DC", "#B45309"
FONT = "Helvetica Neue, Helvetica, Arial, sans-serif"


def _kv_table(b: KeyValueBlock, width_pct: int = 100) -> str:
    rows = "".join(
        f'<tr><td style="color:{MUTED}; padding:3px 8px 3px 0; border-bottom:1px solid {RULE};" width="52%">{escape(k)}</td>'
        f'<td style="padding:3px 0; border-bottom:1px solid {RULE}; font-weight:bold;">{escape(v)}</td></tr>'
        for k, v in b.rows)
    cap = (f'<p style="margin:0 0 3px 0; color:{MUTED}; font-size:7.5pt; font-weight:bold;">{escape(b.caption.upper())}</p>'
           if b.caption else "")
    return f'{cap}<table width="{width_pct}%" cellspacing="0" cellpadding="0" style="font-size:8pt;">{rows}</table>'


def _data_table(b: TableBlock) -> str:
    many = len(b.header) > 8
    fs = "6.6pt" if many else "7.6pt"
    head = "".join(
        f'<th style="background-color:{SOFT}; color:{MUTED}; padding:4px 5px; text-align:{"right" if i >= b.align_right_from else "left"}; '
        f'border-bottom:1px solid {RULE}; font-size:{fs};">{escape(h)}</th>' for i, h in enumerate(b.header))
    body = []
    for r in b.rows:
        cells = "".join(
            f'<td style="padding:3px 5px; border-bottom:1px solid {RULE}; text-align:{"right" if i >= b.align_right_from else "left"};">{escape(str(c))}</td>'
            for i, c in enumerate(r))
        body.append(f"<tr>{cells}</tr>")
    cap = (f'<p style="margin:10px 0 3px 0; color:{MUTED}; font-size:7.5pt; font-weight:bold;">{escape(b.caption.upper())}</p>'
           if b.caption else "")
    return (f'{cap}<table width="100%" cellspacing="0" cellpadding="0" style="font-size:{fs};"><tr>{head}</tr>'
            f'{"".join(body)}</table>')


def _note(b: NoteBlock) -> str:
    bg, fg = (WARN_BG, WARN_FG) if b.tone == "warning" else (SOFT, MUTED)
    icon = "⚠ " if b.tone == "warning" else ""
    return (f'<table width="100%" cellspacing="0" cellpadding="0" style="margin:4px 0;"><tr>'
            f'<td style="background-color:{bg}; color:{fg}; padding:6px 9px; font-size:8pt;">{icon}{escape(b.text)}</td>'
            f'</tr></table>')


def _html(report: Report, doc: QTextDocument, content_w: int) -> str:
    images: dict[str, QImage] = {}

    def image(fig: FigureBlock, frac: float) -> str:
        name = f"fig{len(images)}"
        images[name] = QImage.fromData(fig.png)
        w = int(content_w * (96.0 / RES) * frac)          # <img width> is in 96-dpi logical px, content_w in device px
        return (f'<img src="{name}" width="{w}"><br>'
                f'<span style="color:{MUTED}; font-size:7pt;">{escape(fig.caption)}</span>')

    parts = [f'<html><body style="font-family:{FONT}; font-size:9pt; color:{INK};">']

    # ---- cover band
    meta = "".join(
        f'<tr><td style="color:{MUTED}; padding:2px 14px 2px 0;">{escape(k)}</td>'
        f'<td style="padding:2px 0; font-weight:bold;">{escape(v)}</td></tr>' for k, v in report.meta_rows)
    images["logo"] = theme.app_logo(64).toImage()
    parts.append(
        f'<table width="100%" cellspacing="0" cellpadding="0"><tr>'
        f'<td bgcolor="{ACCENT}" style="padding:16px 20px;">'
        f'<span style="font-size:21pt; color:#FFFFFF; font-weight:bold;">{escape(report.title)}</span><br>'
        f'<span style="font-size:10pt; color:#DCE8FF;">Electrochemical analysis report</span></td></tr></table>'
        f'<table cellspacing="0" cellpadding="0" style="margin-top:10px; font-size:9pt;">{meta}'
        f'<tr><td style="color:{MUTED}; padding:2px 14px 2px 0;">Generated</td>'
        f'<td style="padding:2px 0;">{escape(report.generated)} with Echem Analyzer {escape(report.version)}</td></tr></table>')

    def ca_layout(sec: Section) -> str:
        """Setup on the left; results and calibration on the right; the trace across the page."""
        kvs = [b for b in sec.blocks if isinstance(b, KeyValueBlock)]
        figs = {b.path.rsplit("__", 1)[-1]: b for b in sec.blocks if isinstance(b, FigureBlock)}
        out = [_note(n) for n in sec.blocks if isinstance(n, NoteBlock)]
        left = _kv_table(kvs[0]) if kvs else ""
        right = _kv_table(kvs[1]) if len(kvs) > 1 else ""
        if "calibration.png" in figs:
            right += f'<p style="margin:12px 0 0 0;">{image(figs["calibration.png"], 0.5)}</p>'
        out.append(f'<table width="100%" cellspacing="0" cellpadding="0" style="margin-top:6px;"><tr>'
                   f'<td width="46%" valign="top" style="padding-right:18px;">{left}</td>'
                   f'<td width="54%" valign="top">{right}</td></tr></table>')
        if "trace.png" in figs:
            out.append(f'<p style="margin:10px 0 0 0;">{image(figs["trace.png"], 1.0)}</p>')
        return "".join(out)

    for sec in report.sections:
        brk = "page-break-before:always;" if sec.page_break else ""
        parts.append(f'<p style="{brk} font-size:14pt; font-weight:bold; color:{INK}; margin:18px 0 0 0;">{escape(sec.title)}</p>')
        if sec.subtitle:
            parts.append(f'<p style="margin:0 0 6px 0; color:{MUTED}; font-size:8.5pt;">{escape(sec.subtitle)}</p>')
        parts.append(f'<hr style="color:{RULE};">')
        if sec.kind == "ca":
            parts.append(ca_layout(sec))
            continue

        blocks, i = sec.blocks, 0
        while i < len(blocks):
            b = blocks[i]
            if isinstance(b, KeyValueBlock):                      # up to two key/value tables side by side
                group = [b]
                while len(group) < 2 and i + len(group) < len(blocks) and isinstance(blocks[i + len(group)], KeyValueBlock):
                    group.append(blocks[i + len(group)])
                cols = "".join(f'<td width="{100 // len(group)}%" valign="top" style="padding-right:{14 if k < len(group) - 1 else 0}px;">'
                               f'{_kv_table(x)}</td>' for k, x in enumerate(group))
                parts.append(f'<table width="100%" cellspacing="0" cellpadding="0" style="margin-top:6px;"><tr>{cols}</tr></table>')
                i += len(group)
            elif isinstance(b, FigureBlock):                      # figures that share a row
                group, total = [b], b.fraction
                while (i + len(group) < len(blocks) and isinstance(blocks[i + len(group)], FigureBlock)
                       and total + blocks[i + len(group)].fraction <= 1.02 and b.fraction < 1.0):
                    group.append(blocks[i + len(group)])
                    total += group[-1].fraction
                if len(group) == 1:
                    parts.append(f'<p style="margin:8px 0 0 0;">{image(b, min(1.0, b.fraction))}</p>')
                else:
                    cols = "".join(f'<td valign="top" width="{int(100 * x.fraction / total)}%">{image(x, x.fraction / total * 0.97)}</td>'
                                   for x in group)
                    parts.append(f'<table width="100%" cellspacing="0" cellpadding="0" style="margin-top:8px;"><tr>{cols}</tr></table>')
                i += len(group)
            elif isinstance(b, TableBlock):
                parts.append(_data_table(b))
                i += 1
            else:
                parts.append(_note(b))
                i += 1
    parts.append("</body></html>")
    for name, img in images.items():
        doc.addResource(QTextDocument.ImageResource, QUrl(name), img)
    return "".join(parts)


def write_pdf(report: Report, path: str | Path) -> int:
    """Write ``report`` to ``path`` as an A4 PDF; returns the number of pages."""
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    writer = QPdfWriter(str(path))
    from .prefs import prefs
    writer.setPageSize(QPageSize(QPageSize.Letter if prefs.get("page_size") == "Letter" else QPageSize.A4))
    writer.setPageMargins(QMarginsF(0, 0, 0, 0), QPageLayout.Millimeter)
    writer.setResolution(RES)
    writer.setTitle(report.title)
    writer.setCreator(f"Echem Analyzer {report.version}")

    px = RES / 25.4
    ml, mt, mr, mb = (m * px for m in MARGIN_MM)
    page_w, page_h = writer.width(), writer.height()
    content_w, content_h = int(page_w - ml - mr), int(page_h - mt - mb)

    doc = QTextDocument()
    doc.documentLayout().setPaintDevice(writer)
    doc.setDocumentMargin(0)
    doc.setPageSize(QSizeF(content_w, content_h))
    base = QFont("Helvetica Neue")
    base.setStyleHint(QFont.SansSerif)
    doc.setDefaultFont(base)
    doc.setHtml(_html(report, doc, content_w))

    painter = QPainter(writer)
    n_pages = max(1, doc.pageCount())
    foot_font = QFont(base)
    foot_font.setPointSizeF(7.5)
    for i in range(n_pages):
        if i:
            writer.newPage()
        painter.save()
        painter.translate(ml, mt - i * content_h)
        clip = QRectF(0, i * content_h, content_w, content_h)
        painter.setClipRect(clip)
        doc.drawContents(painter, clip)
        painter.restore()

        painter.save()
        painter.setFont(foot_font)
        painter.setPen(QColor(RULE))
        painter.drawLine(int(ml), int(page_h - mb + 3 * px), int(page_w - mr), int(page_h - mb + 3 * px))
        painter.setPen(QColor(MUTED))
        box = QRectF(ml, page_h - mb + 5 * px, content_w, 6 * px)
        painter.drawText(box, int(Qt.AlignLeft | Qt.AlignVCenter), f"{report.title}  ·  Echem Analyzer {report.version}")
        painter.drawText(box, int(Qt.AlignRight | Qt.AlignVCenter), f"Page {i + 1} of {n_pages}")
        painter.restore()
    painter.end()
    return n_pages
