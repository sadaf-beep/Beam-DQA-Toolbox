"""
Turns the pipeline's per-check results into a short DQA to-do list — pure
Python, no UI.

Only products with a problem appear, one line per problem: priority, the
problem in plain words, the rulebook rule it breaks, and a suggested fix.
Priorities follow the "What the QA tool reports" section of the DQA Product
QA Rulebook (Google Doc):
  High   — wrong manufacturer or model, wrong status, duplicate product,
           broken product page
  Medium — wrong warranty, firmware flags, documents, other field errors
  Low    — couldn't be verified, needs a person to look
"""

from tools.common import claude

HIGH, MEDIUM, LOW = "High", "Medium", "Low"
PRIORITY_ORDER = [HIGH, MEDIUM, LOW]

TODO_COLS = ["Priority", "Manufacturer", "Model", "Product Name", "Check", "Problem",
             "Rule", "Suggested Fix", "Beam Insights URL", "Product ID", "Source File", "File Row"]

_NOT_CHECKED = (LOW, "Couldn't be checked automatically", "Every product is checked",
                "Check by hand, or re-run the check for this product")


def _s(r) -> str:
    return str(r.get("qa_status", "")).strip().lower()


def _n(r) -> str:
    return str(r.get("qa_note", "")).strip()


def _duplicates(r):
    out = []
    types = {p["type"] for p in r.get("pairs", [])}
    note = _n(r)
    if "Exact Duplicate" in types:
        out.append((HIGH, "Duplicate product", "No duplicate products in Insights",
                    f"Tell the team lead which record to keep. {note}"))
    if "Cross-Brand" in types:
        out.append((HIGH, "Same model listed under different manufacturers",
                    "Manufacturer and model must belong together",
                    f"Check which manufacturer is correct. {note}"))
    if "Model Variant (Review)" in types and not out:
        out.append((MEDIUM, "Same model written differently (hyphens/spaces)",
                     "Formatting differences: check case by case, often a model alias",
                     f"Check whether this is a duplicate or a model alias. {note}"))
    if "Near Match" in types and not out:
        out.append((LOW, "Very similar model to another product", "No duplicate products in Insights",
                    f"Check whether these are the same product. {note}"))
    return out


def _oem_url(r):
    s = _s(r)
    if s == "error":
        return [(HIGH, "No product page (Page URL)", "Every product needs a product page", _n(r))]
    if s == "fail":
        return [(MEDIUM, "is_oem_url is wrong", "is_oem_url is TRUE only on the manufacturer's own site", _n(r))]
    if s == "review":
        return [(LOW, "Product page needs a look", "is_oem_url is TRUE only on the manufacturer's own site", _n(r))]
    return []


def _links(r):
    out = []
    page, image = str(r.get("product_status", "")), str(r.get("image_status", ""))
    if "BROKEN" in page:
        out.append((HIGH, f"Product page link is broken ({page})", "Product page must work",
                    "Find the current product page and update Page URL"))
    if "BROKEN" in image:
        out.append((MEDIUM, f"Image link is broken ({image})", "Every product needs a working image",
                    "Copy a new image address from the product page (or use the manufacturer logo)"))
    if not out and _s(r) == "review":
        out.append((LOW, "Link gave an unusual response", "Product page and image must work",
                    f"Open the link and check it. {_n(r)}"))
    return out


def _name_qa(r):
    s = _s(r)
    fix = str(r.get("suggested_corrections", "")).strip() or _n(r)
    if s == "fail":
        return [(HIGH, "Manufacturer, model or name looks wrong",
                 "Manufacturer, model and name must match the manufacturer's listing", fix)]
    if s == "warning":
        return [(MEDIUM, "Product name or model may be slightly off",
                 "Product name must match the manufacturer's listing", fix)]
    return []


def _status(r):
    s = _s(r)
    if s == "mismatch":
        new = str(r.get("Suggested Status", "")).strip() or str(r.get("Verified Status", "")).strip()
        return [(HIGH, f"Status is wrong (recorded: {r.get('DB Status', '')})",
                 "Supported while it still has firmware/software support; discontinued only when support has ended",
                 f"Change status to {new}. {r.get('Reason', '')}".strip())]
    if s == "unknown":
        return [(LOW, "Status couldn't be confirmed online", "Status must be supported or discontinued",
                 "Check the manufacturer's site and support page by hand")]
    return []


def _firmware(r):
    out = []
    if str(r.get("flag_has_firmware", "")).lower() == "flag":
        out.append((MEDIUM, "has_firmware is wrong", "has_firmware is TRUE when the manufacturer offers firmware",
                    f"Correct has_firmware. {r.get('firmware_source', '')}".strip()))
    fa = str(r.get("flag_firmware_added", "")).lower()
    if "contradiction" in fa:
        out.append((MEDIUM, "Firmware Added is TRUE but has_firmware is FALSE",
                    "Firmware can only be added to a product that has firmware", "Fix whichever flag is wrong"))
    elif fa == "flag":
        out.append((MEDIUM, "Firmware exists but hasn't been added", "Firmware Added is TRUE once the team adds it",
                    "Add the firmware to Beam"))
    if str(r.get("flag_documents", "")).lower() == "flag":
        out.append((MEDIUM, "Documents exist but haven't been added", "Official documents should be uploaded",
                    f"Upload the documents. {r.get('documents_source', '')}".strip()))
    if not out and _s(r) in ("warning", "unknown"):
        out.append((LOW, "Firmware/documents couldn't be confirmed", "has_firmware must be correct",
                    f"Check the manufacturer's downloads page by hand. {_n(r)}".strip()))
    return out


def _warranty(r):
    s = _s(r)
    rule = "Product's own warranty, or the manufacturer's standard; never blank"
    if s.startswith("flag"):
        return [(MEDIUM, "Warranty is blank", rule, _n(r).replace("Warranty is blank. ", ""))]
    if s == "fail":
        verified = str(r.get("verified_warranty_days", "")).strip()
        desc = str(r.get("verified_warranty_description", "")).strip()
        fix = f"Change to {verified} days ({desc})." if verified else ""
        return [(MEDIUM, "Warranty is wrong", rule, f"{fix} {_n(r)}".strip())]
    if s == "error":
        return [(MEDIUM, "Warranty value isn't a number", rule, _n(r))]
    if s in ("warning", "unknown"):
        return [(LOW, "Warranty couldn't be confirmed", rule, _n(r))]
    return []


def _dimensions(r):
    s = _s(r)
    if s.startswith("fail") or s.startswith("flag"):
        return [(MEDIUM, f"Dimensions: {r.get('qa_status')}",
                 "Dimensions in the manufacturer's unit and format (W x H x D for electronics)", _n(r))]
    if s in ("warning", "unknown"):
        return [(LOW, "Dimensions couldn't be confirmed", "Dimensions must match the manufacturer's specs", _n(r))]
    return []


_BUILDERS = {
    "duplicates": _duplicates, "oem_url": _oem_url, "links": _links, "name_qa": _name_qa,
    "status": _status, "firmware": _firmware, "warranty": _warranty, "dimensions": _dimensions,
}


def items_for(stage_key: str, result: dict) -> list:
    """(priority, problem, rule, fix) tuples for one product's result in one check."""
    if not result:
        return []
    if _s(result) == claude.NEEDS_REVIEW.lower():
        return [_NOT_CHECKED]
    return _BUILDERS[stage_key](result)


def build_rows(master_rows: list, stage_results: dict, stages: list) -> list:
    """To-do rows across all checks, highest priority first. Each master
    row carries its "_row" id (see core.build_master_rows)."""
    out = []
    for m in master_rows:
        for stage in stages:
            res = stage_results.get(stage.key, {}).get(m["_row"])
            for priority, problem, rule, fix in items_for(stage.key, res):
                out.append({
                    "_row": m["_row"], "Priority": priority, "Manufacturer": m["Manufacturer"], "Model": m["Model"],
                    "Product Name": m["Product Name"], "Check": stage.label, "Problem": problem,
                    "Rule": rule, "Suggested Fix": fix, "Beam Insights URL": m.get("Beam Insights URL", ""),
                    "Product ID": m.get("Product ID", ""), "Source File": m["Source File"],
                    "File Row": m["File Row"],
                })
    out.sort(key=lambda r: PRIORITY_ORDER.index(r["Priority"]))
    return out
