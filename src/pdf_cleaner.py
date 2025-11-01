# src/pdf_cleaner.py
from __future__ import annotations
import os, re, statistics
from pathlib import Path
import fitz  # PyMuPDF


def _expand_rect(r: fitz.Rect, d: float = 1.0) -> fitz.Rect:
    return fitz.Rect(r.x0 - d, r.y0 - d, r.x1 + d, r.y1 + d)


def _image_rects(page: fitz.Page, xref: int):
    """سازگار با نسخه‌های مختلف PyMuPDF: یا bbox تکی یا چند rect."""
    rects: list[fitz.Rect] = []
    # جدیدترها: get_image_bbox
    try:
        rb = page.get_image_bbox(xref)
        if isinstance(rb, fitz.Rect):
            rects.append(rb)
    except Exception:
        pass
    # قدیمی‌تر/عمومی: get_image_rects
    if not rects:
        try:
            rs = page.get_image_rects(xref) or []
            for r in rs:
                try:
                    rects.append(fitz.Rect(r))
                except Exception:
                    pass
        except Exception:
            rects = []
    return rects


def clean_pdf_watermarks(
    input_path: str,
    output_path: str | None = None,
    *,
    header_height_pt: float = 70,                 # فقط مثل صفحه ۳: نوار بالایی
    keywords: list[str] | None = None,            # اگر متن آلوده در هدر بود
    include_first_page: bool = True,              # چون گفتی همهٔ صفحات لوگو دارند
    remove_images_in_header: bool = True,
    img_max_h_pt: float = 95,                     # لوگوی باریک/کم‌ارتفاع
    img_max_w_ratio: float = 0.85,                # زیاد پهن نباشد
    min_repetition_ratio: float = 0.40,           # تکرار حداقل ۴۰٪ صفحات → «لوگوی هدر»
    always_write: bool | None = None,             # اگر True حتی بدون تغییر ذخیره می‌کند
    overwrite_original: bool = False,             # ← اضافه شد: ذخیره روی خود فایل
) -> str | None:
    """
    فقط باند بالای هر صفحه را تمیز می‌کند (مثل صفحه ۳) و به بقیه محتوا کاری ندارد.
    - لوگوهای تکراری هدر را شناسایی و حذف می‌کند.
    - متن‌های دارای کلیدواژه داخل هدر را رداکت می‌کند.
    اگر چیزی پاک نشود، None برمی‌گرداند (مگر always_write=True).
    """
    p = Path(input_path)

    # پیکربندی always_write و overwrite از محیط (اختیاری)
    if always_write is None:
        always_write = os.getenv("ALWAYS_WRITE_CLEAN", "0") == "1"
    overwrite_original = overwrite_original or (os.getenv("PDFCLEAN_OVERWRITE", "0") == "1")

    # تعیین مسیر خروجی و حالت inplace
    if output_path:
        out = Path(output_path)
        inplace = (out.resolve() == p.resolve())
    elif overwrite_original:
        out = p.with_suffix(".tmp.clean.pdf")   # اول موقت می‌نویسیم، بعد replace
        inplace = True
    else:
        out = p.with_name(p.stem + ".clean.pdf")
        inplace = False

    kw = keywords or ["downloaded from", "iranpaper", "tarjomano", "joopy", "ترجمانو"]
    kw_rx = re.compile("|".join(re.escape(k) for k in kw), re.I)

    doc = fitz.open(str(p))
    try:
        if doc.page_count == 0:
            return None

        # جمع‌آوری نمونه‌نقطه‌های عمودی برای هر xref که در هدر می‌افتد
        header_xref_positions: dict[int, list[float]] = {}
        for pi, page in enumerate(doc):
            if not include_first_page and pi == 0:
                continue
            band = fitz.Rect(0, 0, page.rect.width, header_height_pt)
            if not remove_images_in_header:
                continue

            try:
                imgs = page.get_images(full=True) or []
            except Exception:
                imgs = []
            for img in imgs:
                xref = int(img[0])
                for rect in _image_rects(page, xref):
                    if not isinstance(rect, fitz.Rect):
                        continue
                    if not band.intersects(rect):
                        continue
                    # لوگوی هدر معمولاً کم‌ارتفاع است و خیلی عریض نیست
                    if rect.height <= img_max_h_pt and rect.width <= (page.rect.width * img_max_w_ratio):
                        header_xref_positions.setdefault(xref, []).append((rect.y0 + rect.y1) / 2.0)

        # xrefهایی که واقعاً «لوگوی هدر تکراری» هستند
        min_pages = max(3, int(round(doc.page_count * min_repetition_ratio)))
        header_xrefs: set[int] = set()
        for xref, ys in header_xref_positions.items():
            if len(ys) >= min_pages:
                try:
                    med = statistics.median(ys)
                except statistics.StatisticsError:
                    med = min(ys)
                if med <= header_height_pt * 1.25:
                    header_xrefs.add(xref)

        any_change = False

        # مرحلهٔ رداکت کردن: فقط هدر
        for pi, page in enumerate(doc):
            if not include_first_page and pi == 0:
                continue

            band = fitz.Rect(0, 0, page.rect.width, header_height_pt)
            page_changed = False

            # 1) متن آلوده در هدر (کلیدواژه‌ها)
            try:
                blocks = page.get_text("dict").get("blocks", [])
            except Exception:
                blocks = []
            for b in blocks:
                for ln in b.get("lines", []):
                    for sp in ln.get("spans", []):
                        txt = sp.get("text") or ""
                        bbox = sp.get("bbox")
                        if not txt or not bbox:
                            continue
                        r = fitz.Rect(*bbox)
                        if band.intersects(r) and kw_rx.search(txt):
                            page.add_redact_annot(_expand_rect(r, 1))
                            any_change = True
                            page_changed = True

            # 2) تصاویر هدر تکراری
            if remove_images_in_header and header_xrefs:
                for xref in header_xrefs:
                    for r in _image_rects(page, xref):
                        if not isinstance(r, fitz.Rect):
                            continue
                        if band.intersects(r):
                            page.add_redact_annot(_expand_rect(r, 1))
                            any_change = True
                            page_changed = True

            # اعمال رداکشن فقط اگر در همین صفحه چیزی اضافه شده
            if page_changed:
                img_mode = getattr(fitz, "PDF_REDACT_IMAGE_REMOVE", None)
                try:
                    if img_mode is None:
                        page.apply_redactions()
                    else:
                        page.apply_redactions(images=img_mode)
                except TypeError:
                    page.apply_redactions()

        # ذخیره‌سازی
        if any_change or always_write:
            save_kw = {}
            for k, v in (("garbage", 4), ("deflate", True), ("clean", True)):
                try:
                    save_kw[k] = v
                except Exception:
                    pass

            doc.save(str(out), **save_kw)

            # اگر in-place بود، جایگزین فایل ورودی کن
            if inplace:
                os.replace(out, p)
                return str(p)
            return str(out)

        # بدون تغییر
        return None

    finally:
        try:
            doc.close()
        except Exception:
            pass


async def clean_pdf_watermarks_async(input_path, output_path=None, **kw):
    import asyncio
    return await asyncio.to_thread(clean_pdf_watermarks, input_path, output_path, **kw)
