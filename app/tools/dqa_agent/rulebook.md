# DQA Product QA Rulebook (agent copy)

Source of truth: the "DQA Product QA Rulebook" Google Doc in the DQA Drive
folder. Keep this copy in step with it when a rule changes.

## Core principles
- Exact match only: a product matches only when the model number (MPN) matches exactly. Never match to a similar model, a series or a newer version.
- Identified is the only identification status. Approximate Match, Unidentified and Internal are retired; an item that can't be matched exactly goes to Can't Match with one reason.
- Never guess. Every value comes from a trusted source. If it can't be found, say so.
- End of Life is a status, not a reason to skip: a discontinued product is still identified and still gets its details.
- When unsure, a person decides — identification and status changes affect customer data and linked apps.

## Trusted sources
- Manufacturer's own site first: product page, support page, manual, datasheet, archived page (Wayback Machine).
- B&H and Adorama are acceptable as the product page when the manufacturer has no page; other authorised dealers (CDW, Sweetwater, Markertek) as extra sources.
- eBay and second-hand sites are never a source for product details; they only count as evidence of End of Life.
- Use US versions of sites; the model number should be the one sold in the USA.

## Field rules
- Manufacturer: must exist in Insights (new ones are added by Sadaf or Riad). Spelling variants of one company are the same manufacturer. Manufacturer and model from different companies = wrong record.
- Model (MPN): manufacturer part number or SKU; software may have a blank model. Case-only differences are the same model. Differences in hyphens, spaces, slashes or dots usually mean the same model, but check case by case: they are often a model alias rather than a duplicate. Region/colour suffixes and revisions (MK2 vs MK3) are different models. A retailer's or customer's internal SKU is not a model number unless the manufacturer links it to the real MPN.
- Product name: matches the manufacturer's listing, no typos or extra characters, and does not repeat the manufacturer name.
- Description: markdown, a short overview paragraph then a bullet list of key features; no HTML tags, no spelling mistakes.
- Category: set by product type, never UNKNOWN.
- Image: a product photo from its page; the manufacturer logo if no photo exists.
- Page URL / is_oem_url: is_oem_url is TRUE only when the Page URL is on the manufacturer's own website. If the product isn't listed on the manufacturer's site and a third-party page is used, is_oem_url is FALSE and that is not a QA error.
- Product status: the export uses supported and discontinued (discontinued = End of Life). A product that still gets software or firmware updates or official support is supported even if no longer sold; it becomes discontinued only when that support has ended. Discontinued needs evidence. Unknown and N/A are no longer used, except when there is no way to tell.
- Warranty: recorded in days (1 year = 365, 2 years = 730, 3 years = 1095, 5 years = 1825). -1 and 36500 both mean lifetime. Use the product's own warranty; if it has none, use the manufacturer's standard warranty. An identified product's warranty is never blank. A difference of 15 days or less is not an error.
- Dimensions: in the manufacturer's unit, not converted. Electronics, TVs, monitors, computer and AV equipment use Width x Height x Depth; boxes, packaging and furniture use Length x Width x Height. A TV's screen diagonal is not a dimension. Add package dimensions when provided. The export stores width, height, depth, weight and an optional note.
- Firmware: has_firmware is TRUE when the manufacturer offers firmware or software updates (if it can be updated, configured or powered on by itself it is equipment). Firmware Added is TRUE once the team has added it. has_firmware TRUE with Firmware Added FALSE = firmware still to add; has_firmware FALSE with Firmware Added TRUE = contradiction. Document/Resource/Update URL columns are not checked.
- Documents: uploaded into Beam manually. Wanted: manuals/user guides, datasheets, spec sheets, technical sheets, quick start guides, dimension charts. No generic company papers, no duplicates.

## Can't Match reasons (ask in order, stop at the first yes)
1. Duplicate — the same item is already in the queue or import.
2. Not Equipment — not a physical product (labour, shipping, fees, support contracts, standalone software or licences).
3. Custom Build — physical but not a catalogue product.
4. Accessory — no firmware and no function of its own.
5. Insufficient Info — customer data too weak to search.
6. Discontinued — data complete but no source with the exact model exists.
7. Other — none of the above; a note is required.

## Duplicates in the database
Never edit or delete a duplicate yourself. The team lead decides which record to keep (the most complete one from the most reliable source).

## Priorities
- High: wrong manufacturer or model, wrong status, duplicate product, broken product page.
- Medium: wrong warranty, firmware flags, documents, description/category/dimension errors.
- Low: couldn't be verified, needs a person to look.
