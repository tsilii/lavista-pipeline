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
import json
import os
import shutil
import sys
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from pathlib import Path

import anthropic

# ── Config ─────────────────────────────────────────────────────────────────────

ANTHROPIC_API_KEY = os.getenv("ANTHROPIC_API_KEY")
MODEL             = os.getenv("INVOICE_MODEL", "claude-sonnet-4-20250514")
MAX_PARALLEL      = int(os.getenv("INVOICE_PARALLEL", "5"))
SUPPORTED_EXTS    = {".jpg", ".jpeg", ".png", ".webp"}

# ── Supplier extraction prompt (lightweight — just the name) ───────────────────

SUPPLIER_PROMPT = """
Look at this invoice/delivery note photo.
Extract ONLY the supplier name — the company SELLING the goods.
Their name is usually at the top of the invoice in large text.

Do NOT return the buyer/restaurant name (e.g. "KSENOS FOOD", "SOVA", "SOVA BISTROT").

Return ONLY a JSON object, nothing else:
{"supplier_name": "string or null"}

If you cannot determine the supplier, return:
{"supplier_name": null}
"""


def extract_supplier(image_path: Path) -> dict:
    """Send one image to Claude Vision, get supplier name back."""
    client = anthropic.Anthropic(api_key=ANTHROPIC_API_KEY)

    image_bytes = image_path.read_bytes()
    image_b64   = base64.standard_b64encode(image_bytes).decode("utf-8")

    ext_map = {".jpg": "image/jpeg", ".jpeg": "image/jpeg",
               ".png": "image/png",  ".webp": "image/webp"}
    media_type = ext_map.get(image_path.suffix.lower(), "image/jpeg")

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

        data = json.loads(raw)
        return {
            "file":     image_path,
            "supplier": (data.get("supplier_name") or "").strip() or None,
        }

    except json.JSONDecodeError:
        return {"file": image_path, "supplier": None, "error": "Invalid JSON"}
    except Exception as e:
        if "529" in str(e) or "overloaded" in str(e).lower():
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
    # Melis
    "melis":    "Melis",
    "mels":     "Melis",
    # BakeArt
    "d.i. bakeart ltd":     "D.I. BakeArt Ltd",
    "d.i. bakeart ltd.":    "D.I. BakeArt Ltd",
    "bakeart":              "D.I. BakeArt Ltd",
    "bake art":             "D.I. BakeArt Ltd",
    # Vassiliopolous — fix common typo
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
        sys.exit(1)

    source_dir = Path(sys.argv[1]).resolve()
    if not source_dir.is_dir():
        print(f"Error: '{source_dir}' is not a directory.")
        sys.exit(1)

    if not ANTHROPIC_API_KEY:
        print("Error: ANTHROPIC_API_KEY environment variable is not set.")
        print("Set it with:  export ANTHROPIC_API_KEY='sk-ant-api03-...'")
        sys.exit(1)

    # ── Find image files ───────────────────────────────────────────────────────

    image_files = sorted([
        f for f in source_dir.iterdir()
        if f.is_file() and f.suffix.lower() in SUPPORTED_EXTS
    ])

    if not image_files:
        print(f"No image files found in '{source_dir}'")
        print(f"Supported formats: {', '.join(SUPPORTED_EXTS)}")
        sys.exit(1)

    output_dir = source_dir / "organized"
    output_dir.mkdir(exist_ok=True)

    print(f"""
🦉 Sova Invoice Organizer
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
                status = f"  ✓ [{done}/{total}] {fname} → {supplier}"
            else:
                status = f"  ? [{done}/{total}] {fname} → Unknown Supplier"

            print(status)

    elapsed = time.time() - start

    # ── Organize files into folders ────────────────────────────────────────────

    print(f"\n{'─' * 40}")
    print(f"Organizing files into folders...\n")

    supplier_counts = {}
    unknown_count   = 0
    error_count     = 0

    for result in results:
        src_file = result["file"]
        supplier = result.get("supplier")
        error    = result.get("error")

        if error:
            # Put errors in a special folder
            folder_name = "_errors"
            error_count += 1
        elif supplier:
            folder_name = sanitize_folder_name(supplier)
        else:
            folder_name = "_unknown_supplier"
            unknown_count += 1

        dest_folder = output_dir / folder_name
        dest_folder.mkdir(exist_ok=True)

        dest_file = dest_folder / src_file.name

        # Handle duplicate filenames
        if dest_file.exists():
            stem = src_file.stem
            suffix = src_file.suffix
            counter = 1
            while dest_file.exists():
                dest_file = dest_folder / f"{stem}_{counter}{suffix}"
                counter += 1

        shutil.copy2(src_file, dest_file)

        if folder_name not in ("_errors", "_unknown_supplier"):
            supplier_counts[folder_name] = supplier_counts.get(folder_name, 0) + 1

    # ── Summary ────────────────────────────────────────────────────────────────

    print(f"{'─' * 40}")
    print(f"✅ Done in {elapsed:.1f}s\n")
    print(f"📁 Organized into: {output_dir}\n")

    print(f"Suppliers found ({len(supplier_counts)}):")
    for name, count in sorted(supplier_counts.items()):
        print(f"  📂 {name}: {count} invoice{'s' if count > 1 else ''}")

    if unknown_count:
        print(f"\n  ⚠️  _unknown_supplier: {unknown_count} file(s)")
    if error_count:
        print(f"  ❌  _errors: {error_count} file(s)")

    print(f"\n  Total: {len(results)} files processed")
    print(f"\n💡 Originals are untouched in: {source_dir}")
    print(f"   Organized copies are in:    {output_dir}")


if __name__ == "__main__":
    main()