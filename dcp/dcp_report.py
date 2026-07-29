"""
Discovery Capital Partners house-style A4 report engine.

Reproduces the geometry, palette and type scale of the DCP teaser/report format
(A4 portrait, navy footer band, DCP mark bottom-left, subject-company logo top-right).

Usage
-----
    import sys; sys.path.insert(0, "<skill>/scripts")
    from dcp_report import DCPReport

    r = DCPReport("out.pdf", subject_logo="ari_logo.png")   # or subject_logo=None
    r.cover(title="Arika Resources", subtitle="Equity Research",
            blurb="...", image="cover.jpg")
    r.page("Investment Case", style="band")
    r.text("Body copy here.")
    r.bullets(["Point one", "Point two"])
    r.closing(contacts=DEFAULT_CONTACTS)
    r.save()

All coordinates in this module are INCHES measured from the TOP-LEFT of the page.
The class converts to ReportLab points internally.
"""

import os

from reportlab.lib.colors import HexColor
from reportlab.lib.enums import TA_LEFT
from reportlab.lib.styles import ParagraphStyle
from reportlab.pdfbase import pdfmetrics
from reportlab.pdfbase.ttfonts import TTFont
from reportlab.pdfgen import canvas as rl_canvas
from reportlab.platypus import Paragraph, Table, TableStyle

# --------------------------------------------------------------------------
# Constants
# --------------------------------------------------------------------------

IN = 72.0
PAGE_W = 8.2639 * IN          # A4 portrait
PAGE_H = 11.6944 * IN

NAVY = HexColor("#002B56")    # primary. ALL body text is navy, never black
GREY = HexColor("#E6E7E8")    # panel / card fill
WHITE = HexColor("#FFFFFF")
TEAL = HexColor("#00313C")
GOLD = HexColor("#BA9C67")    # accent rules only
SLATE = HexColor("#6F86A2")
ICE = HexColor("#C9E4FF")

M_L = 0.30                    # left margin (in)
M_R = 0.30                    # right margin (in)
CONTENT_W = 8.2639 - M_L - M_R

BAND_TOP = 0.64               # header band y (in, from top)
BAND_H = 0.46
TITLE_TOP = 0.24              # plain-title header y

CONTENT_TOP_BAND = 1.24       # first content y when a band header is used
CONTENT_TOP_TITLE = 1.00      # first content y when a plain title is used

# DCP mark on the cover, in the top right of the navy block. Cover text is wrapped to
# the column beside it whenever a block would otherwise reach into this rectangle.
COVER_LOGO_X = 6.07
COVER_LOGO_Y = 5.89
COVER_LOGO_W = 2.04
COVER_LOGO_H = 1.02
COVER_LOGO_GAP = 0.18         # clear space between cover text and the mark
CONTENT_BOTTOM = 10.72        # hard floor: nothing below this on interior pages

FOOTER_TOP = 10.94
FOOTER_H = 0.75

ASSETS = os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "assets")


class LayoutError(Exception):
    """Raised when strict=True and content will not fit its container."""


DEFAULT_CONTACTS = [
    {"name": "Adam Miethke", "role": "Managing Director",
     "mobile": "+61 (0) 420 383 733", "email": "am@discoverycapital.com.au"},
    {"name": "Kale Pervan", "role": "Director",
     "mobile": "+61 (0) 419 861 005", "email": "kp@discoverycapital.com.au"},
    {"name": "Darcy Frazer", "role": "Associate",
     "mobile": "+61 (0) 400 970 950", "email": "df@discoverycapital.com.au"},
]

GENERAL_ADVICE = (
    "This document contains general information only and does not take into account "
    "the objectives, financial situation or needs of any particular person. Before "
    "acting on it, recipients should consider its appropriateness having regard to "
    "their own objectives, financial situation and needs, and seek independent advice. "
    "Confirm current AFSL details with compliance before external use."
)

DISCLAIMER = (
    "This document was prepared by Discovery Capital Partners Pty Ltd exclusively for the "
    "benefit of the recipient. It does not purport to contain all information that may be "
    "required to evaluate the subject matter, and each recipient should conduct its own "
    "independent analysis of the information contained or referred to herein. This document "
    "is proprietary to Discovery and may not be disclosed to any third party or used for any "
    "other purpose without the prior written consent of Discovery.\n"
    "The information in this document reflects prevailing conditions and the views of Discovery "
    "as of this date, which are accordingly subject to change. In preparing this document, "
    "Discovery has relied upon and assumed, without independent verification, the accuracy and "
    "completeness of all information available from public sources or provided to it. All dollar "
    "figures are in AUD unless otherwise indicated."
)


def _register_fonts():
    """Register bundled Open Sans. Falls back to Helvetica if assets are missing."""
    fdir = os.path.join(ASSETS, "fonts")
    pairs = [("OpenSans", "OpenSans-Regular.ttf"),
             ("OpenSans-Bold", "OpenSans-Bold.ttf"),
             ("OpenSans-Semi", "OpenSans-SemiBold.ttf"),
             ("OpenSans-Italic", "OpenSans-Italic.ttf")]
    ok = True
    for name, fn in pairs:
        path = os.path.join(fdir, fn)
        if not os.path.exists(path):
            ok = False
            break
        try:
            pdfmetrics.registerFont(TTFont(name, path))
        except Exception:
            ok = False
            break
    if not ok:
        return "Helvetica", "Helvetica-Bold", "Helvetica-Bold", "Helvetica-Oblique"
    pdfmetrics.registerFontFamily(
        "OpenSans", normal="OpenSans", bold="OpenSans-Bold", italic="OpenSans-Italic")
    return "OpenSans", "OpenSans-Bold", "OpenSans-Semi", "OpenSans-Italic"


F_REG, F_BOLD, F_SEMI, F_ITAL = _register_fonts()


class DCPReport:
    """A4 portrait report in Discovery Capital Partners house style."""

    def __init__(self, path, subject_logo=None, footer_page_numbers=True, strict=False):
        self.path = path
        self.subject_logo = subject_logo if (subject_logo and os.path.exists(subject_logo)) else None
        self.footer_page_numbers = footer_page_numbers
        self.strict = strict
        self.issues = []          # layout problems found while drawing
        self.warnings = []        # handled automatically, worth an eye
        self._regions = []        # occupied blocks on the current page
        self.c = rl_canvas.Canvas(path, pagesize=(PAGE_W, PAGE_H))
        self.c.setTitle(os.path.splitext(os.path.basename(path))[0])
        self.page_no = 0
        self.y = CONTENT_TOP_BAND
        self.col_x = M_L
        self.col_w = CONTENT_W
        self._open = False

    # ---------------- containment ----------------

    def _flag(self, msg, warn=False):
        """Record a layout problem.

        warn=True records something the engine handled correctly but the author should
        know about, such as cover text wrapped to clear the DCP mark. Warnings are
        printed by save() but never raise under strict=True, because nothing is broken.
        """
        entry = "p%d: %s" % (self.page_no, msg)
        if warn:
            self.warnings.append(entry)
            return entry
        self.issues.append(entry)
        if self.strict:
            raise LayoutError(entry)
        return entry

    def _check_within(self, what, top, bottom, box_top, box_bottom, tol=0.01):
        """Assert drawn content sits inside its container, in inches."""
        if bottom > box_bottom + tol:
            self._flag("%s overflows its box by %.3f in (content ends %.3f, box ends %.3f)"
                       % (what, bottom - box_bottom, bottom, box_bottom))
        if top < box_top - tol:
            self._flag("%s starts above its box by %.3f in" % (what, box_top - top))

    def _claim(self, what, x0, y0, x1, y1, tol=0.03):
        """Register an occupied block and report if it lands on top of another.

        Absolute placement is how side-by-side layouts get built, and it is also how a
        block ends up on top of the paragraph above it. The engine tracks what it has
        already drawn so that mistake is reported at build time rather than found in a
        screenshot.
        """
        for (ox0, oy0, ox1, oy1, olabel) in self._regions:
            ix = min(x1, ox1) - max(x0, ox0)
            iy = min(y1, oy1) - max(y0, oy0)
            if ix > tol and iy > tol:
                area = ix * iy
                smaller = min((x1 - x0) * (y1 - y0), (ox1 - ox0) * (oy1 - oy0))
                if smaller > 0 and area / smaller > 0.06:
                    self._flag("%s overlaps %s by %.2f x %.2f in"
                               % (what, olabel, ix, iy))
                    break
        self._regions.append((x0, y0, x1, y1, what))

    def _check_page(self, what, bottom):
        if bottom > CONTENT_BOTTOM + 0.01:
            self._flag("%s runs past the content floor by %.3f in "
                       "(ends %.3f, floor %.3f)" % (what, bottom - CONTENT_BOTTOM,
                                                    bottom, CONTENT_BOTTOM))

    def _fit_para(self, text, width_in, max_h_in, size=11, bold=False, color=NAVY,
                  align=TA_LEFT, italic=False, min_size=6.0, step=0.5):
        """Build a paragraph, shrinking the font until it fits max_h_in.

        Returns (paragraph, height_in, final_size). If it still will not fit at
        min_size the paragraph is returned anyway and the caller should flag it.
        """
        s = size
        while True:
            font = F_ITAL if italic else (F_BOLD if bold else F_REG)
            st = self._style(s, bold, color, s * 1.28, align=align, font=font)
            p = Paragraph(text.replace("\n", "<br/>"), st)
            _, h = p.wrap(width_in * IN, 40 * IN)
            if h / IN <= max_h_in or s <= min_size:
                return p, h / IN, s
            s -= step

    # ---------------- coordinate helpers ----------------

    @staticmethod
    def _ty(y_in):
        """Top-origin inches -> ReportLab bottom-origin points."""
        return PAGE_H - y_in * IN

    def _style(self, size=11, bold=False, color=NAVY, leading=None, align=TA_LEFT,
               font=None, space_after=0):
        return ParagraphStyle(
            "s", fontName=font or (F_BOLD if bold else F_REG), fontSize=size,
            leading=leading or size * 1.28, textColor=color, alignment=align,
            spaceAfter=space_after, allowWidows=1, allowOrphans=1)

    # ---------------- page furniture ----------------

    def _footer(self):
        c = self.c
        c.setFillColor(NAVY)
        c.rect(0, self._ty(FOOTER_TOP + FOOTER_H), PAGE_W, FOOTER_H * IN, stroke=0, fill=1)
        logo = os.path.join(ASSETS, "logo-dcp-white.png")
        if os.path.exists(logo):
            c.drawImage(logo, 0.25 * IN, self._ty(10.96 + 0.63),
                        1.26 * IN, 0.63 * IN, mask="auto")
        if self.footer_page_numbers and self.page_no > 1:
            c.setFont(F_REG, 7)
            c.setFillColor(WHITE)
            c.drawRightString((8.2639 - 0.32) * IN, self._ty(11.42), str(self.page_no))

    def _subject_logo(self):
        """Subject-company logo, top right. Blank if none supplied."""
        if not self.subject_logo:
            return
        from reportlab.lib.utils import ImageReader
        iw, ih = ImageReader(self.subject_logo).getSize()
        max_w, max_h = 0.72 * IN, 0.42 * IN
        scale = min(max_w / iw, max_h / ih)
        w, h = iw * scale, ih * scale
        x = (8.2639 - M_R) * IN - w
        self.c.drawImage(self.subject_logo, x, self._ty(0.16) - h, w, h, mask="auto")

    def _new_page(self):
        if self._open:
            self._footer()
            self.c.showPage()
        self.page_no += 1
        self._open = True
        self._regions = []
        self.c.setFillColor(NAVY)

    # ---------------- pages ----------------

    def cover(self, title, subtitle=None, blurb=None, image=None,
              confidential="Private & Confidential", tint=0.30):
        """Cover: full-bleed image on the top half, navy block below."""
        self._new_page()
        c = self.c
        img = image or os.path.join(ASSETS, "cover-default.jpg")
        split = 5.85
        if img and os.path.exists(img):
            c.drawImage(img, 0, self._ty(split), PAGE_W, split * IN,
                        mask="auto", preserveAspectRatio=False)
            if tint:
                c.saveState()
                c.setFillColor(NAVY)
                c.setFillAlpha(tint)
                c.rect(0, self._ty(split), PAGE_W, split * IN, stroke=0, fill=1)
                c.restoreState()
        c.setFillColor(NAVY)
        c.rect(0, 0, PAGE_W, self._ty(split), stroke=0, fill=1)

        logo = os.path.join(ASSETS, "logo-dcp-white.png")
        if os.path.exists(logo):
            c.drawImage(logo, COVER_LOGO_X * IN, self._ty(COVER_LOGO_Y + COVER_LOGO_H),
                        COVER_LOGO_W * IN, COVER_LOGO_H * IN, mask="auto")

        # The DCP mark sits in the top right of the navy block, over the same band the
        # title and subtitle occupy. Any cover text that reaches into that band is
        # wrapped to the narrower column beside the mark rather than running under it.
        narrow = COVER_LOGO_X - M_L - COVER_LOGO_GAP

        def place(txt, style, y_top, full_w, max_h=6.0):
            """Draw a cover paragraph, avoiding the mark. Returns the new cursor y."""
            p = Paragraph(txt, style)
            _, h = p.wrap(narrow * IN, max_h * IN)
            clears = (y_top + h / IN <= COVER_LOGO_Y
                      or y_top >= COVER_LOGO_Y + COVER_LOGO_H)
            if clears and full_w > narrow:
                # nothing of this block falls beside the mark, so use the full width
                p = Paragraph(txt, style)
                _, h = p.wrap(full_w * IN, max_h * IN)
            p.drawOn(c, M_L * IN, self._ty(y_top) - h)
            if not clears and full_w > narrow:
                self._flag("cover text '%s' sits beside the DCP mark and was wrapped to "
                           "%.2f in" % (txt[:28].replace("\n", " "), narrow), warn=True)
            return y_top + h / IN

        y = 6.10
        y = place(title, self._style(28, bold=True, color=WHITE, leading=33), y, 6.0) + 0.10

        if subtitle:
            y = place(subtitle, self._style(14, color=WHITE, leading=18), y, 7.6) + 0.16

        if confidential:
            c.setFont(F_BOLD, 9)
            c.setFillColor(ICE)
            c.drawString(M_L * IN, self._ty(y + 0.11), confidential.upper())
            y += 0.34

        if blurb:
            place(blurb.replace("\n", "<br/>"),
                  self._style(11, color=WHITE, leading=15.5), y, 7.66)

    def page(self, title, subtitle=None, style="band"):
        """Start an interior page.

        style="band"  navy band with white 18pt label   (overview / section pages)
        style="title" navy 18pt title + 12pt subtitle    (detail pages)
        """
        self._new_page()
        c = self.c
        if style == "band":
            c.setFillColor(NAVY)
            c.rect(M_L * IN, self._ty(BAND_TOP + BAND_H), CONTENT_W * IN, BAND_H * IN,
                   stroke=0, fill=1)
            c.setFont(F_BOLD, 18)
            c.setFillColor(WHITE)
            c.drawString((M_L + 0.15) * IN, self._ty(BAND_TOP + BAND_H - 0.135), title)
            self.y = CONTENT_TOP_BAND
            if subtitle:
                self.y = BAND_TOP + BAND_H + 0.18
                self.text(subtitle, size=12, bold=True, space_after=0.16)
        else:
            p = Paragraph(title, self._style(18, bold=True, leading=21))
            w, h = p.wrap(7.0 * IN, 1 * IN)
            p.drawOn(c, M_L * IN, self._ty(TITLE_TOP) - h)
            yy = TITLE_TOP + h / IN + 0.04
            if subtitle:
                p = Paragraph(subtitle, self._style(12, bold=True, leading=15))
                w, h = p.wrap(7.0 * IN, 1 * IN)
                p.drawOn(c, M_L * IN, self._ty(yy) - h)
                yy += h / IN
            self.y = max(yy + 0.16, CONTENT_TOP_TITLE)
        self.col_x, self.col_w = M_L, CONTENT_W
        self._subject_logo()

    # ---------------- content ----------------

    def column(self, x, w):
        """Set the active column. Coordinates in inches."""
        self.col_x, self.col_w = x, w
        return self

    def at(self, y):
        """Move the cursor to an absolute y (inches from top)."""
        self.y = y
        return self

    def text(self, txt, size=11, bold=False, color=NAVY, leading=None,
             space_after=0.10, x=None, w=None, italic=False):
        """Flow a paragraph at the cursor. Returns the new cursor y."""
        font = F_ITAL if italic else (F_BOLD if bold else F_REG)
        st = self._style(size, bold, color, leading, font=font)
        p = Paragraph(txt.replace("\n", "<br/>"), st)
        wid = (w or self.col_w) * IN
        _, h = p.wrap(wid, 20 * IN)
        xx = x if x is not None else self.col_x
        top = self.y
        p.drawOn(self.c, xx * IN, self._ty(self.y) - h)
        self.y += h / IN + space_after
        label = "text '%s'" % txt[:28].replace("\n", " ")
        self._claim(label, xx, top, xx + (w or self.col_w), top + h / IN)
        self._check_page(label, self.y - space_after)
        return self.y

    def bullets(self, items, size=11, color=NAVY, leading=None, gap=0.07,
                space_after=0.10, x=None, w=None, bullet="\u2022", indent=0.16):
        """Bulleted list. Each item may contain <b> tags."""
        st = self._style(size, False, color, leading)
        st.leftIndent = indent * IN
        st.bulletIndent = 0
        st.bulletFontName = F_REG
        st.bulletFontSize = size
        xx = (x if x is not None else self.col_x) * IN
        wid = (w or self.col_w) * IN
        for it in items:
            p = Paragraph(it, st, bulletText=bullet)
            _, h = p.wrap(wid, 20 * IN)
            p.drawOn(self.c, xx, self._ty(self.y) - h)
            self.y += h / IN + gap
            self._check_page("bullet '%s'" % it[:34], self.y - gap)
        self.y += space_after - gap
        return self.y

    def _card_parts(self, heading, body, bullets, inner, heading_size, body_size):
        parts = []
        if heading:
            parts.append(Paragraph(heading, self._style(heading_size, True, NAVY,
                                                        heading_size * 1.2)))
        if body:
            parts.append(Paragraph(body.replace("\n", "<br/>"),
                                   self._style(body_size, False, NAVY)))
        if bullets:
            bst = self._style(body_size, False, NAVY)
            bst.leftIndent = 0.16 * IN
            bst.bulletFontName = F_REG
            bst.bulletFontSize = body_size
            for b in bullets:
                parts.append(Paragraph(b, bst, bulletText="\u2022"))
        heights = []
        for p in parts:
            _, ph = p.wrap(inner * IN, 40 * IN)
            heights.append(ph)
        return parts, heights

    def card_height(self, heading, body=None, bullets=None, w=None, pad=0.14,
                    heading_size=14, body_size=11):
        """Height a card would need, without drawing it. Used to equalise a row."""
        ww = w if w is not None else self.col_w
        parts, heights = self._card_parts(heading, body, bullets, ww - 2 * pad,
                                          heading_size, body_size)
        if not parts:
            return 0.4
        return (sum(heights) + (len(parts) - 1) * 0.06 * IN + 2 * pad * IN) / IN

    def card(self, heading, body=None, bullets=None, x=None, y=None, w=None, h=None,
             pad=0.14, fill=GREY, heading_size=14, body_size=11):
        """Grey panel with a navy heading. Auto-heights when h is None."""
        xx = x if x is not None else self.col_x
        yy = y if y is not None else self.y
        ww = w if w is not None else self.col_w
        inner = ww - 2 * pad

        parts, heights = self._card_parts(heading, body, bullets, inner,
                                          heading_size, body_size)
        need = (sum(heights) + (len(parts) - 1) * 0.06 * IN + 2 * pad * IN) if parts \
            else 0.4 * IN
        # h is a MINIMUM, never a cap: content must not be clipped by the panel
        hh = max(h * IN, need) if h else need
        if h and need > h * IN + 0.5:
            self._flag("card '%s' needed %.2f in but was given h=%.2f, box grown to fit"
                       % ((heading or "")[:40], need / IN, h))

        self.c.setFillColor(fill)
        self.c.rect(xx * IN, self._ty(yy) - hh, ww * IN, hh, stroke=0, fill=1)

        cy = self._ty(yy + pad)
        for p, ph in zip(parts, heights):
            cy -= ph
            p.drawOn(self.c, (xx + pad) * IN, cy)
            cy -= 0.06 * IN

        bottom = yy + hh / IN
        content_bottom = yy + (need - pad * IN) / IN
        label = "card '%s'" % (heading or "")[:32]
        self._check_within(label, yy, content_bottom, yy, bottom)
        self._claim(label, xx, yy, xx + ww, bottom)
        self._check_page(label, bottom)
        if y is None:
            self.y = bottom + 0.14
        return bottom

    def cards_row(self, cards, y=None, gap=0.22, x=None, w=None, fill=GREY,
                  heading_size=14, body_size=11, equalise=True):
        """Lay out cards side by side: same top, same height, evenly divided width.

        Placing each card by hand is how they end up misaligned and how one lands on the
        paragraph above it. Pass a list of dicts with heading, body and bullets keys.

            r.cards_row([{"heading": "Gold Stream", "bullets": [...]},
                         {"heading": "Equity", "bullets": [...]}])
        """
        if not cards:
            return self.y
        xx = x if x is not None else M_L
        ww = w if w is not None else CONTENT_W
        yy = y if y is not None else self.y
        n = len(cards)
        cw = (ww - gap * (n - 1)) / n

        tallest = None
        if equalise:
            # measure without drawing, then draw every card once at the tallest height
            tallest = max(self.card_height(c.get("heading"), body=c.get("body"),
                                           bullets=c.get("bullets"), w=cw,
                                           heading_size=heading_size,
                                           body_size=body_size) for c in cards)

        bottom = yy
        for i, c in enumerate(cards):
            cx = xx + i * (cw + gap)
            b = self.card(c.get("heading"), body=c.get("body"), bullets=c.get("bullets"),
                          x=cx, y=yy, w=cw, h=tallest, fill=fill,
                          heading_size=heading_size, body_size=body_size)
            bottom = max(bottom, b)
        self.y = bottom + 0.16
        return self.y

    def stat(self, value, label, x, y, w, h=None, fill=GREY, pad=0.10,
             value_size=20, label_size=8):
        """Large stat callout: big navy number over a small label.

        The box grows to fit its content. Passing h sets a MINIMUM height, never a
        cap, so a long label can never spill below the panel.
        """
        inner = w - 2 * 0.08
        # the headline value must sit on ONE line: shrink to fit the width, never wrap
        vs = value_size
        while vs > 8 and pdfmetrics.stringWidth(str(value), F_BOLD, vs) > inner * IN:
            vs -= 0.5
        pv, hv, _ = self._fit_para(value, inner, 1.2, size=vs, bold=True, align=1)
        pl, hl, _ = self._fit_para(label, inner, 1.2, size=label_size, align=1)
        need = pad + hv + 0.05 + hl + pad
        hh = max(h or 0.0, need)

        self.c.setFillColor(fill)
        self.c.rect(x * IN, self._ty(y + hh), w * IN, hh * IN, stroke=0, fill=1)

        pv.drawOn(self.c, (x + 0.08) * IN, self._ty(y + pad) - hv * IN)
        label_top = y + hh - pad - hl
        pl.drawOn(self.c, (x + 0.08) * IN, self._ty(label_top) - hl * IN)

        self._check_within("stat '%s' label" % value, y + pad, label_top + hl, y, y + hh)
        self._claim("stat '%s'" % value, x, y, x + w, y + hh)
        self._check_page("stat '%s'" % value, y + hh)
        return y + hh

    def stats_row(self, stats, y=None, gap=0.22, x=None, w=None, fill=GREY):
        """Evenly divided stat callouts on one row. stats = [(value, label), ...]."""
        if not stats:
            return self.y
        xx = x if x is not None else M_L
        ww = w if w is not None else CONTENT_W
        yy = y if y is not None else self.y
        n = len(stats)
        cw = (ww - gap * (n - 1)) / n
        bottom = yy
        for i, (value, label) in enumerate(stats):
            bottom = max(bottom, self.stat(value, label, xx + i * (cw + gap), yy, cw,
                                           fill=fill))
        self.y = bottom + 0.16
        return self.y

    def image(self, path, x=None, y=None, w=None, h=None, caption=None):
        """Place an image, preserving aspect ratio if only one dimension is given."""
        from reportlab.lib.utils import ImageReader
        xx = x if x is not None else self.col_x
        yy = y if y is not None else self.y
        iw, ih = ImageReader(path).getSize()
        if w and not h:
            h = w * ih / iw
        elif h and not w:
            w = h * iw / ih
        elif not w and not h:
            w = self.col_w
            h = w * ih / iw
        self.c.drawImage(path, xx * IN, self._ty(yy + h), w * IN, h * IN, mask="auto")
        bottom = yy + h
        if caption:
            p = Paragraph(caption, self._style(8, False, NAVY, 10))
            _, ph = p.wrap(w * IN, 3 * IN)
            p.drawOn(self.c, xx * IN, self._ty(bottom + 0.06) - ph)
            bottom += 0.06 + ph / IN
        self._claim("image %s" % os.path.basename(path), xx, yy, xx + w, bottom)
        self._check_page("image %s" % os.path.basename(path), bottom)
        if y is None:
            self.y = bottom + 0.14
        return bottom

    def table(self, data, col_widths, x=None, y=None, header=True, size=9,
              align=None, row_h=None, centre=False, fit=False):
        """DCP-styled table. col_widths in inches. data[0] is the header row.

        centre=True   centres the table in the active column
        fit=True      scales the columns proportionally to fill the active column width
        """
        xx = x if x is not None else self.col_x
        yy = y if y is not None else self.y
        if fit:
            target = self.col_w if x is None else (8.2639 - M_R - xx)
            scale = target / float(sum(col_widths))
            col_widths = [c * scale for c in col_widths]
        if centre and x is None:
            xx = self.col_x + (self.col_w - sum(col_widths)) / 2.0
        cw = [c * IN for c in col_widths]
        st = [
            ("FONTNAME", (0, 0), (-1, -1), F_REG),
            ("FONTSIZE", (0, 0), (-1, -1), size),
            ("TEXTCOLOR", (0, 0), (-1, -1), NAVY),
            ("VALIGN", (0, 0), (-1, -1), "MIDDLE"),
            ("LEFTPADDING", (0, 0), (-1, -1), 5),
            ("RIGHTPADDING", (0, 0), (-1, -1), 5),
            ("TOPPADDING", (0, 0), (-1, -1), 3.5),
            ("BOTTOMPADDING", (0, 0), (-1, -1), 3.5),
            ("LINEBELOW", (0, 0), (-1, -1), 0.4, WHITE),
        ]
        if header:
            st += [("BACKGROUND", (0, 0), (-1, 0), NAVY),
                   ("TEXTCOLOR", (0, 0), (-1, 0), WHITE),
                   ("FONTNAME", (0, 0), (-1, 0), F_BOLD)]
            for i in range(1, len(data)):
                if i % 2 == 1:
                    st.append(("BACKGROUND", (0, i), (-1, i), GREY))
        else:
            for i in range(len(data)):
                if i % 2 == 0:
                    st.append(("BACKGROUND", (0, i), (-1, i), GREY))
        if align:
            for col, a in align.items():
                st.append(("ALIGN", (col, 0), (col, -1), a))

        total_w = sum(col_widths)
        if xx + total_w > 8.2639 - M_R + 0.01:
            self._flag("table is %.2f in wide at x=%.2f, which runs past the right margin"
                       % (total_w, xx))
        t = Table(data, colWidths=cw, rowHeights=row_h)
        t.setStyle(TableStyle(st))
        tw, th = t.wrap(sum(cw), 40 * IN)
        t.drawOn(self.c, xx * IN, self._ty(yy) - th)
        bottom = yy + th / IN
        label = "table (%d rows)" % len(data)
        self._claim(label, xx, yy, xx + total_w, bottom)
        self._check_page(label, bottom)
        if y is None:
            self.y = bottom + 0.14
        return bottom

    def rule(self, y=None, x=None, w=None, color=GOLD, width=0.8):
        yy = y if y is not None else self.y
        xx = x if x is not None else self.col_x
        ww = w if w is not None else self.col_w
        self.c.setStrokeColor(color)
        self.c.setLineWidth(width)
        self.c.line(xx * IN, self._ty(yy), (xx + ww) * IN, self._ty(yy))
        if y is None:
            self.y = yy + 0.12
        return self.y

    def chart(self, kind, *args, **kwargs):
        """Render a DCP-styled chart and place it at the cursor.

        kind is one of 'line', 'bar', 'waterfall', 'grouped'. Frame width defaults to
        the active column, and the caption is placed beneath at 8pt.

            r.chart("line", series, title="Share price (A$)", caption="Source: FactSet")
            r.chart("bar", ["HRN","GBR"], [75,103], highlight="GBR")
        """
        import dcp_charts as ch
        caption = kwargs.pop("caption", None)
        w = kwargs.pop("w", self.col_w)
        h = kwargs.pop("h", 2.4)
        y = kwargs.pop("y", None)
        fn = {"line": ch.line_chart, "bar": ch.bar_chart,
              "waterfall": ch.waterfall, "grouped": ch.grouped_bar}[kind]
        path = fn(*args, w=w, h=h, **kwargs)
        return self.image(path, x=kwargs.get("x"), y=y, w=w, caption=caption)

    def caption(self, txt, x=None, w=None, space_after=0.10):
        return self.text(txt, size=8, space_after=space_after, x=x, w=w)

    def source(self, txt):
        """Source line pinned just above the footer band."""
        return self.text("Source: " + txt, size=7, x=M_L, w=CONTENT_W,
                         space_after=0) if self.y < CONTENT_BOTTOM else None

    # ---------------- closing page ----------------

    def closing(self, summary=None, summary_title="Opportunity Overview",
                contacts=None, disclaimer=None,
                intro=None, subject=None, general_advice=False):
        """Final page: optional summary band, contacts, disclaimer.

        Set general_advice=True on research and commentary, which appends the general
        advice warning to the transaction disclaimer.
        """
        contacts = contacts if contacts is not None else DEFAULT_CONTACTS
        self.page(summary_title, style="band")
        if summary:
            self.card("", body=summary, w=CONTENT_W, body_size=11)

        self.y = max(self.y + 0.20, 6.90)
        self.text("Additional Information and Contacts", size=18, bold=True, space_after=0.10)
        who = subject or "The Company"
        self.text(
            intro or (
                f"{who} has engaged Discovery Capital Partners (\u201cDiscovery\u201d) as its "
                "exclusive Corporate Adviser. Any communications or enquiries related to this "
                "process should be directed to one of the representatives of Discovery below."),
            size=9, space_after=0.06)
        self.text(f"Under no circumstances should {who} or any of its affiliates, customers, "
                  "suppliers or regulators be contacted directly.", size=9, space_after=0.22)

        top = self.y
        n = max(1, len(contacts))
        colw = min(2.9, CONTENT_W / n)
        for i, ct in enumerate(contacts):
            x = M_L + i * (CONTENT_W / n)
            self.y = top
            self.text(ct["name"], size=13, bold=True, x=x, w=colw, space_after=0.04)
            self.text(ct["role"], size=9, bold=True, x=x, w=colw, space_after=0.02)
            if ct.get("mobile"):
                self.text("Mobile&nbsp;&nbsp;&nbsp;" + ct["mobile"], size=9, x=x, w=colw,
                          space_after=0.01)
            if ct.get("email"):
                self.text("Email&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;" + ct["email"], size=9, x=x,
                          w=colw, space_after=0.01)
        bottom = self.y

        self.y = max(bottom + 0.30, 9.40)
        self.rule(color=GOLD)
        self.y += 0.06
        logo = os.path.join(ASSETS, "logo-dcp-dark.png")
        if os.path.exists(logo):
            self.c.drawImage(logo, M_L * IN, self._ty(self.y + 0.60), 1.20 * IN, 0.60 * IN,
                             mask="auto")
        body = disclaimer if disclaimer is not None else DISCLAIMER
        if general_advice:
            body = body + "\n" + GENERAL_ADVICE
        # the disclaimer is the last thing on the page, so it fits the space that remains
        avail = CONTENT_BOTTOM - self.y
        dw = CONTENT_W - 1.45
        para, dh, dsize = self._fit_para(body, dw, avail, size=7, min_size=5.0, step=0.25)
        para.drawOn(self.c, (M_L + 1.45) * IN, self._ty(self.y) - dh * IN)
        self._check_page("disclaimer", self.y + dh)
        self.y += dh

    def save(self, quiet=False):
        """Write the PDF and report any layout problems found while drawing."""
        if self._open:
            self._footer()
            self.c.showPage()
        self.c.save()
        if self.issues and not quiet:
            print("LAYOUT ISSUES (%d):" % len(self.issues))
            for i in self.issues:
                print("  " + i)
        elif not quiet:
            print("Layout clean: %d pages, no overflow." % self.page_no)
        if self.warnings and not quiet:
            print("LAYOUT NOTES (%d):" % len(self.warnings))
            for w in self.warnings:
                print("  " + w)
        return self.path
