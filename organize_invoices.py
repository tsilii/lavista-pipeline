"""
🦉 Sova Bistrot — Invoice Organizer
Reads invoice photos with Claude Vision, extracts supplier name,
and organizes them into supplier subfolders.

Usage:
    python organize_invoices.py /path/to/unsorted/photos

Creates:
    /path/to/unsorted/photos/organized/Supplier A/photo1.jpg
    /path/to/unsorted/photos/organized/Supplier B/photo2.jpg
    ...

Requires: ANTHROPIC_API_KEY environment variable
"""

import base64
import io
import json
import os
import shutil
import sys
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime
from pathlib import Path

import anthropic
from PIL import Image

# ── Config ─────────────────────────────────────────────────────────────────────

ANTHROPIC_API_KEY = os.getenv("ANTHROPIC_API_KEY")
MODEL             = os.getenv("INVOICE_MODEL", "claude-sonnet-4-20250514")
MAX_PARALLEL      = int(os.getenv("INVOICE_PARALLEL", "5"))
SUPPORTED_EXTS    = {".jpg", ".jpeg", ".png", ".webp"}

# ── Supplier extraction prompt (lightweight — just the name) ───────────────────

SUPPLIER_PROMPT = """
Look at this document photo carefully. Determine the supplier name, document type, and whether it's a return.

SUPPLIER NAME:
- The company SELLING the goods — usually at the top in large text or in the header/logo.
- Do NOT return the buyer/restaurant name (e.g. "KSENOS FOOD", "SOVA", "SOVA BISTROT", "KSENOS FOOD BEVERAGE LTD").
- If the supplier name is NOT visible at the top, check the ENTIRE document including:
  - Legal text or fine print at the bottom
  - Footer disclaimers (e.g. "η εταιρεία ... Ltd δεν φέρει ευθύνη")
  - Company stamps or signatures
  - Any mention of the issuing company name anywhere on the page

DOCUMENT TYPE — classify as one of:
- "invoice" — a supplier invoice, delivery note, credit invoice, sales invoice, tax invoice (any document with products, quantities, prices, and a total)
- "return" — a document titled "Return Invoice", "Επιστροφή Τιμολόγιο", "ΔΕΛΤΙΟ ΕΠΙΣΤΡΟΦΗΣ", or "ΔΕΛΤΙΟ ΕΠΙΣΤΡΟΦΗΣ ΜΗ ΕΜΠΟΡΕΥΣΙΜΩΝ" (non-merchandise return slip)
- "statement" — a Statement of Account, ΚΑΤΑΣΤΑΣΗ ΛΟΓΑΡΙΑΣΜΟΥ, Aged Balance, or account summary
- "other" — anything else (receipts, notes, unreadable)

If this is page 2+ of an invoice (no header but has totals/items), still try to find the supplier name in the fine print.

Return ONLY a JSON object, nothing else:
{"supplier_name": "string or null", "doc_type": "invoice", "is_return": false}

Examples:
- Regular invoice → {"supplier_name": "MELIS", "doc_type": "invoice", "is_return": false}
- Return invoice → {"supplier_name": "PPD GLOBAL", "doc_type": "return", "is_return": true}
- Non-merchandise return → {"supplier_name": "PPD GLOBAL", "doc_type": "return", "is_return": true}
- Statement of account → {"supplier_name": "PPD GLOBAL", "doc_type": "statement", "is_return": false}
- Cannot determine → {"supplier_name": null, "doc_type": "other", "is_return": false}
"""


def rotate_image(image_bytes: bytes, degrees: int) -> bytes:
    """Rotate image by given degrees (90, 180, 270). Returns new image bytes."""
    try:
        img = Image.open(io.BytesIO(image_bytes))
        # PIL rotate is counter-clockwise, so 90° CW = 270° in PIL
        rotated = img.rotate(-degrees, expand=True)
        buf = io.BytesIO()
        fmt = img.format or "JPEG"
        rotated.save(buf, format=fmt, quality=95)
        return buf.getvalue()
    except Exception:
        return image_bytes


def try_extract(client, image_b64: str, media_type: str) -> dict | None:
    """
    Single Claude Vision call with automatic retry on overload/rate-limit.
    Returns parsed dict on success, None on JSON failure.
    Raises other exceptions.
    """
    max_retries = 3

    for attempt in range(max_retries):
        try:
            message = client.messages.create(
                model=MODEL,
                max_tokens=256,
                messages=[{
                    "role": "user",
                    "content": [
                        {"type": "image", "source": {"type": "base64", "media_type": media_type, "data": image_b64}},
                        {"type": "text",  "text": SUPPLIER_PROMPT},
                    ],
                }],
            )

            raw = message.content[0].text.strip()
            if raw.startswith("```"):
                raw = raw.split("```")[1]
                if raw.startswith("json"):
                    raw = raw[4:]
                raw = raw.strip()

            try:
                return json.loads(raw)
            except json.JSONDecodeError:
                return None

        except Exception as e:
            err_str = str(e).lower()
            if "529" in str(e) or "overloaded" in err_str or "rate_limit" in err_str or "429" in str(e):
                wait = 15 * (attempt + 1)  # 15s, 30s, 45s
                if attempt < max_retries - 1:
                    time.sleep(wait)
                    continue
            raise  # re-raise if not overload or last attempt

    return None


def extract_supplier(image_path: Path) -> dict:
    """
    Send image to Claude Vision. If it fails (Invalid JSON), retry with
    90°, 180°, 270° rotations. Only rotates when the original fails.
    """
    client = anthropic.Anthropic(api_key=ANTHROPIC_API_KEY)

    image_bytes = image_path.read_bytes()

    ext_map = {".jpg": "image/jpeg", ".jpeg": "image/jpeg",
               ".png": "image/png",  ".webp": "image/webp"}
    media_type = ext_map.get(image_path.suffix.lower(), "image/jpeg")

    try:
        # First try: original orientation
        image_b64 = base64.standard_b64encode(image_bytes).decode("utf-8")
        data = try_extract(client, image_b64, media_type)

        if data is not None:
            return {
                "file":      image_path,
                "supplier":  (data.get("supplier_name") or "").strip() or None,
                "is_return": bool(data.get("is_return", False)),
                "doc_type":  data.get("doc_type", "invoice"),
            }

        # Original failed — try rotations with delay between attempts
        for degrees in [90, 180, 270]:
            time.sleep(5)  # pause between rotation attempts
            rotated_bytes = rotate_image(image_bytes, degrees)
            rotated_b64   = base64.standard_b64encode(rotated_bytes).decode("utf-8")
            data = try_extract(client, rotated_b64, media_type)

            if data is not None:
                return {
                    "file":      image_path,
                    "supplier":  (data.get("supplier_name") or "").strip() or None,
                    "is_return": bool(data.get("is_return", False)),
                    "doc_type":  data.get("doc_type", "invoice"),
                    "rotated":   degrees,
                }

        # All rotations failed
        return {"file": image_path, "supplier": None, "error": "Unreadable (tried all rotations)"}

    except Exception as e:
        if "529" in str(e) or "overloaded" in str(e).lower() or "429" in str(e):
            return {"file": image_path, "supplier": None, "error": "API overloaded"}
        return {"file": image_path, "supplier": None, "error": str(e)[:150]}


"""
Supplier name normalization map.
Maps known variations → canonical name.
Add new entries as you discover more variations.
"""
SUPPLIER_ALIASES = {
    # A&K Freshness
    "a&k freshness ltd":        "A&K Freshness Ltd",
    "a&k freshness ltd.":       "A&K Freshness Ltd",
    "a&k freshness":            "A&K Freshness Ltd",
    "a&k fresh":                "A&K Freshness Ltd",
    "ark freshness ltd":        "A&K Freshness Ltd",
    # Attikouris
    "attikouris enterprises":       "Attikouris Enterprises Ltd",
    "attikouris enterprises ltd":   "Attikouris Enterprises Ltd",
    "attikouris enterprises ltd.":  "Attikouris Enterprises Ltd",
    # Coca-Cola
    "coca-cola hbc":            "Coca-Cola HBC Cyprus",
    "coca-cola hbc cyprus":     "Coca-Cola HBC Cyprus",
    "coca cola hbc":            "Coca-Cola HBC Cyprus",
    "coca cola hbc cyprus":     "Coca-Cola HBC Cyprus",
    # Lillytos
    "l.b.m. (lillytos) ltd":       "Lillytos (L.B.M.) Ltd",
    "lillytos business machines":   "Lillytos (L.B.M.) Ltd",
    "lilytos business machines":    "Lillytos (L.B.M.) Ltd",
    "l.b.m. (lillytos) ltd.":      "Lillytos (L.B.M.) Ltd",
    # Melis — lots of misreads
    "melis":                        "Melis",
    "mels":                         "Melis",
    "hells & sons meat market ltd": "Melis",
    "pambos":                       "Melis",
    # BakeArt
    "d.i. bakeart ltd":     "D.I. BakeArt Ltd",
    "d.i. bakeart ltd.":    "D.I. BakeArt Ltd",
    "bakeart":              "D.I. BakeArt Ltd",
    "bake art":             "D.I. BakeArt Ltd",
    # Veni Cook and Dine
    "veni cook and dine ltd":   "Veni Cook And Dine Ltd",
    "uveni":                    "Veni Cook And Dine Ltd",
    "veni":                     "Veni Cook And Dine Ltd",
    # A&K Freshness — Greek misreads
    "εμπορια οφωτοσ & αχανικοσ":    "A&K Freshness Ltd",
    # Vassiliopolous
    "vassiliopolous":       "Vassilopoulos",
    "vassilopoulos":        "Vassilopoulos",
}


def normalize_supplier(raw_name: str) -> str:
    """Look up the canonical supplier name. Falls back to original if not mapped."""
    if not raw_name:
        return raw_name
    key = raw_name.strip().lower().rstrip(".")
    return SUPPLIER_ALIASES.get(key, raw_name.strip())


def sanitize_folder_name(name: str) -> str:
    """Normalize supplier name, then make it safe for use as a folder name."""
    name = normalize_supplier(name)
    # Remove characters that are problematic in folder names
    bad_chars = '<>:"/\\|?*'
    clean = name.strip()
    for c in bad_chars:
        clean = clean.replace(c, "")
    # Collapse multiple spaces
    clean = " ".join(clean.split())
    return clean.strip() if clean else "Unknown Supplier"


def main():
    # ── Validate args ──────────────────────────────────────────────────────────

    if len(sys.argv) < 2:
        print("Usage: python organize_invoices.py /path/to/photos")
        print("\nOrganizes invoice photos into supplier subfolders using Claude Vision.")
        print("Tip: point it at an _errors folder to retry failed photos.")
        sys.exit(1)

    source_dir = Path(sys.argv[1]).resolve()
    if not source_dir.is_dir():
        print(f"Error: '{source_dir}' is not a directory.")
        sys.exit(1)

    if not ANTHROPIC_API_KEY:
        print("Error: ANTHROPIC_API_KEY environment variable is not set.")
        print("Set it with:  export ANTHROPIC_API_KEY='sk-ant-api03-...'")
        sys.exit(1)

    # ── Detect retry mode ──────────────────────────────────────────────────────
    # If we're pointed at an _errors folder inside an organized/ folder,
    # switch to retry mode: successes go back to the parent organized/ folder,
    # failures stay in _errors, and successfully moved files are removed from _errors.

    retry_mode = False
    organized_root = None

    if source_dir.name == "_errors":
        parent = source_dir.parent
        # Check if parent looks like an organized folder (has supplier subfolders)
        if parent.name == "organized" or any(
            d.is_dir() and not d.name.startswith("_") for d in parent.iterdir()
        ):
            retry_mode = True
            organized_root = parent
            print("🔄 Retry mode detected — successes will go back to organized folders")

    # ── Find image files ───────────────────────────────────────────────────────

    image_files = sorted([
        f for f in source_dir.iterdir()
        if f.is_file() and f.suffix.lower() in SUPPORTED_EXTS
    ])

    if not image_files:
        print(f"No image files found in '{source_dir}'")
        print(f"Supported formats: {', '.join(SUPPORTED_EXTS)}")
        sys.exit(1)

    if retry_mode:
        output_dir = organized_root
    else:
        output_dir = source_dir / "organized"
        output_dir.mkdir(exist_ok=True)

    print(f"""
🦉 Sova Invoice Organizer{"  (RETRY MODE)" if retry_mode else ""}
{'─' * 40}
Source:    {source_dir}
Output:    {output_dir}
Files:     {len(image_files)}
Model:     {MODEL}
Parallel:  {MAX_PARALLEL}
{'─' * 40}
""")

    # ── Process all images ─────────────────────────────────────────────────────

    results  = []
    done     = 0
    total    = len(image_files)
    start    = time.time()

    with ThreadPoolExecutor(max_workers=MAX_PARALLEL) as executor:
        futures = {executor.submit(extract_supplier, f): f for f in image_files}

        for future in as_completed(futures):
            done += 1
            result = future.result()
            results.append(result)

            fname    = result["file"].name
            supplier = result.get("supplier")
            error    = result.get("error")

            if error:
                status = f"  ✗ [{done}/{total}] {fname} — ERROR: {error}"
            elif supplier:
                doc_type = result.get("doc_type", "invoice")
                ret_tag = " ↩ RETURN" if result.get("is_return") else ""
                stmt_tag = " 📄 STATEMENT" if doc_type == "statement" else ""
                rot_tag = f" 🔄 {result['rotated']}°" if result.get("rotated") else ""
                status = f"  ✓ [{done}/{total}] {fname} → {supplier}{ret_tag}{stmt_tag}{rot_tag}"
            else:
                status = f"  ? [{done}/{total}] {fname} → Unknown Supplier"

            print(status)

    elapsed = time.time() - start

    # ── Organize files into folders ────────────────────────────────────────────

    print(f"\n{'─' * 40}")
    print(f"Organizing files into folders...\n")

    supplier_counts  = {}
    return_counts    = {}
    statement_counts = {}
    unknown_count    = 0
    error_count      = 0
    recovered_count  = 0

    for result in results:
        src_file  = result["file"]
        supplier  = result.get("supplier")
        error     = result.get("error")
        is_return = result.get("is_return", False)
        doc_type  = result.get("doc_type", "invoice")

        if error:
            error_count += 1
            if retry_mode:
                continue
            else:
                dest_folder = output_dir / "_errors"
        elif supplier:
            folder_name = sanitize_folder_name(supplier)
            if doc_type == "statement":
                dest_folder = output_dir / "_statements" / folder_name
                statement_counts[folder_name] = statement_counts.get(folder_name, 0) + 1
            elif is_return or doc_type == "return":
                dest_folder = output_dir / "_returns" / folder_name
                return_counts[folder_name] = return_counts.get(folder_name, 0) + 1
            else:
                dest_folder = output_dir / folder_name
                supplier_counts[folder_name] = supplier_counts.get(folder_name, 0) + 1
            if retry_mode:
                recovered_count += 1
        else:
            unknown_count += 1
            dest_folder = output_dir / "_unknown_supplier"

        dest_folder.mkdir(parents=True, exist_ok=True)

        dest_file = dest_folder / src_file.name

        # Handle duplicate filenames
        if dest_file.exists():
            stem = src_file.stem
            suffix = src_file.suffix
            counter = 1
            while dest_file.exists():
                dest_file = dest_folder / f"{stem}_{counter}{suffix}"
                counter += 1

        if retry_mode:
            # Move file out of _errors into the correct supplier folder
            shutil.move(str(src_file), str(dest_file))
        else:
            shutil.copy2(src_file, dest_file)

    # ── Summary ────────────────────────────────────────────────────────────────

    print(f"{'─' * 40}")
    print(f"✅ Done in {elapsed:.1f}s\n")
    print(f"📁 Organized into: {output_dir}\n")

    if retry_mode and recovered_count:
        print(f"🔄 Recovered {recovered_count} file(s) — moved to supplier folders")

    if supplier_counts:
        print(f"\nSuppliers found ({len(supplier_counts)}):")
        for name, count in sorted(supplier_counts.items()):
            print(f"  📂 {name}: {count} invoice{'s' if count > 1 else ''}")

    if return_counts:
        print(f"\nReturn invoices ({len(return_counts)} suppliers):")
        for name, count in sorted(return_counts.items()):
            print(f"  ↩  {name}: {count} return{'s' if count > 1 else ''}")

    if statement_counts:
        print(f"\nStatements of account ({len(statement_counts)} suppliers):")
        for name, count in sorted(statement_counts.items()):
            print(f"  📄 {name}: {count} statement{'s' if count > 1 else ''}")

    if unknown_count:
        print(f"\n  ⚠️  _unknown_supplier: {unknown_count} file(s)")
    if error_count:
        print(f"  ❌  Still failed: {error_count} file(s){' (remaining in _errors)' if retry_mode else ''}")

    print(f"\n  Total: {len(results)} files processed")

    # ── Archive originals (disabled during testing) ────────────────────────────
    # Uncomment the block below when ready to auto-archive after each run:
    #
    # today_str   = datetime.now().strftime("%Y-%m-%d")
    # archive_dir = source_dir.parent / "archive" / today_str
    # print(f"\n📦 Archiving originals to: {archive_dir}")
    # archive_dir.mkdir(parents=True, exist_ok=True)
    # archived = 0
    # for img_file in image_files:
    #     dest = archive_dir / img_file.name
    #     if dest.exists():
    #         stem = img_file.stem
    #         suffix = img_file.suffix
    #         counter = 1
    #         while dest.exists():
    #             dest = archive_dir / f"{stem}_{counter}{suffix}"
    #             counter += 1
    #     shutil.move(str(img_file), str(dest))
    #     archived += 1
    # print(f"   ✅ {archived} files moved to archive")
    # print(f"   📂 {source_dir} is now clean and ready for the next batch")

    print(f"\n💡 Originals untouched in: {source_dir}")
    print(f"   Organized copies in:    {output_dir}")


if __name__ == "__main__":
    main()