"""
OEM URL Check engine — pure Python, no UI dependency, no API.

Classifies each product_url's domain as the manufacturer's own site, a known
retailer, another manufacturer's site, or unknown, and compares that with the
record's is_oem_url flag.

Fixes over the original standalone checker:
- "www." is removed as a prefix (lstrip("www.") also ate leading w's).
- Domains are matched by host suffix, so pro.sony, en-us.sennheiser.com and
  amazon.co.uk all work instead of being cut to their last two labels.
- A manufacturer only matches a known key as a whole word (or the same name
  without spaces) — short or blank names no longer match everything.
- Without an is_oem_url column, the domain is still classified; anything
  other than the manufacturer's own site comes back Review, never Fail.
"""

import re
from urllib.parse import urlparse

from openpyxl import Workbook

from tools.common import excel
from tools.common.data import ROW_COL, SOURCE_COL, Columns, clean, find_col

NAME = "OEM URL Check"

KNOWN_RETAILERS = {
    "bhphotovideo.com", "bandh.com", "amazon.com", "amazon.co.uk", "amazon.ca", "amazon.de",
    "amazon.com.au", "adorama.com", "markertek.com", "sweetwater.com", "cdw.com", "cdwg.com",
    "newegg.com", "bestbuy.com", "walmart.com", "fullcompass.com", "musiciansfriend.com",
    "guitarcenter.com", "proaudiostar.com", "videoguys.com", "camcor.com", "vistek.ca",
    "zzounds.com", "ebay.com", "thomann.de", "gear4music.com", "proflixsales.com",
    "videoexpert.eu", "warehouseav.com", "crutchfield.com", "worldwidetelesystems.com",
    "focusedtechnology.com", "insight.com", "connection.com", "provideosupply.com",
    "avnetwork.com", "broadcast.net", "wavgroup.com", "jands.com.au", "storedj.com.au",
    "alliedtelesis.com", "worthpoint.com", "reverb.com",
}

# Keys are lowercase manufacturer names (or distinctive words in them);
# values are that manufacturer's own domains.
MFR_DOMAINS = {
    "shure": {"shure.com"},
    "sennheiser": {"sennheiser.com", "en-us.sennheiser.com"},
    "sony": {"sony.com", "pro.sony", "pro.sony.com", "sonypro.com"},
    "canon": {"canon.com", "usa.canon.com", "pro.usa.canon.com"},
    "panasonic": {"panasonic.com", "pro.panasonic.net", "panasonic.net"},
    "cisco": {"cisco.com"},
    "evertz": {"evertz.com"},
    "ross": {"rossvideo.com"},
    "ross video": {"rossvideo.com"},
    "grass valley": {"grassvalley.com"},
    "grassvalley": {"grassvalley.com"},
    "blackmagic": {"blackmagicdesign.com"},
    "blackmagicdesign": {"blackmagicdesign.com"},
    "atem": {"blackmagicdesign.com"},
    "telestream": {"telestream.net"},
    "genelec": {"genelec.com"},
    "pelican": {"pelican.com"},
    "chief": {"chiefmounts.com", "chief.com"},
    "middle atlantic": {"middleatlantic.com"},
    "tripp lite": {"tripplite.com"},
    "tripplite": {"tripplite.com"},
    "kensington": {"kensington.com"},
    "startech": {"startech.com"},
    "whirlwind": {"whirlwindusa.com"},
    "anton bauer": {"antonbauer.com"},
    "rts": {"rtsintercoms.com"},
    "telex": {"rtsintercoms.com"},
    "clear-com": {"clearcom.com"},
    "clearcom": {"clearcom.com"},
    "riedel": {"riedel.net"},
    "imagine": {"imaginecommunications.com"},
    "imagine communications": {"imaginecommunications.com"},
    "avid": {"avid.com"},
    "adobe": {"adobe.com"},
    "microsoft": {"microsoft.com"},
    "apple": {"apple.com"},
    "fujifilm": {"fujifilm.com", "fujinon.com"},
    "fujinon": {"fujinon.com", "fujifilm.com"},
    "jvc": {"jvc.com", "pro.jvc.com"},
    "hitachi": {"hitachi.com", "hitachidtv.com"},
    "ikegami": {"ikegami.com"},
    "marshall": {"marshall.com", "marshall-usa.com"},
    "vinten": {"vinten.com"},
    "sachtler": {"sachtler.com"},
    "oconnor": {"ocon.com"},
    "cartoni": {"cartoni.com"},
    "miller": {"millertripods.com"},
    "libec": {"libec.com"},
    "manfrotto": {"manfrotto.com"},
    "belden": {"belden.com"},
    "canare": {"canare.com"},
    "neutrik": {"neutrik.com"},
    "switchcraft": {"switchcraft.com"},
    "multidyne": {"multidyne.com"},
    "cobalt": {"cobaltdigital.com"},
    "axia": {"telosalliance.com"},
    "telos": {"telosalliance.com"},
    "wheatstone": {"wheatstone.com"},
    "ssl": {"solidstatelogic.com"},
    "solid state logic": {"solidstatelogic.com"},
    "calrec": {"calrec.com"},
    "yamaha": {"yamaha.com", "yamahapro.com", "usa.yamaha.com"},
    "allen & heath": {"allen-heath.com"},
    "allen and heath": {"allen-heath.com"},
    "allen-heath": {"allen-heath.com"},
    "qsc": {"qsc.com"},
    "crown": {"crownaudio.com"},
    "jbl": {"jblpro.com", "jbl.com"},
    "bosch": {"boschsecurity.com", "bosch.com"},
    "pelco": {"pelco.com"},
    "axis": {"axis.com"},
    "arri": {"arri.com"},
    "atomos": {"atomos.com"},
    "aja": {"aja.com"},
    "newtek": {"newtek.com", "vizrt.com"},
    "vizrt": {"vizrt.com"},
    "haivision": {"haivision.com"},
    "harmonic": {"harmonicinc.com"},
    "extron": {"extron.com"},
    "kramer": {"kramerav.com"},
    "atlona": {"atlona.com"},
    "crestron": {"crestron.com"},
    "amx": {"amx.com"},
    "biamp": {"biamp.com"},
    "lectrosonics": {"lectrosonics.com"},
    "audio-technica": {"audio-technica.com"},
    "beyerdynamic": {"beyerdynamic.com"},
    "electro-voice": {"electrovoice.com"},
    "electrovoice": {"electrovoice.com"},
    "drew": {"drewnet.com"},
    "drawmer": {"drawmer.com"},
    "gefen": {"gefen.com"},
    "decimator": {"decimator.com.au"},
    "datavideo": {"datavideo.com"},
    "flanders": {"flandersscientific.com"},
    "flandersscientific": {"flandersscientific.com"},
    "wohler": {"wohler.com"},
    "plura": {"plura.com"},
    "tvlogic": {"tvlogic.co.kr", "tvlogic.kr"},
    "smallhd": {"smallhd.com"},
    "convergent": {"convergent-design.com"},
    "red": {"red.com"},
    "zeiss": {"zeiss.com"},
    "leica": {"leica-camera.com"},
    "logitech": {"logitech.com"},
    "dell": {"dell.com"},
    "hp": {"hp.com"},
    "lenovo": {"lenovo.com"},
    "samsung": {"samsung.com"},
    "lg": {"lg.com"},
    "viewsonic": {"viewsonic.com"},
    "nec": {"nec-display.com", "nec.com"},
    "eizo": {"eizo.com"},
    "focusrite": {"focusrite.com"},
    "rme": {"rme-audio.com"},
    "apogee": {"apogeedigital.com"},
    "universal audio": {"uaudio.com"},
    "uad": {"uaudio.com"},
    "dante": {"audinate.com"},
    "audinate": {"audinate.com"},
}
ALL_OEM_DOMAINS = {d for ds in MFR_DOMAINS.values() for d in ds}

# Two-label public suffixes, so "shop.example.co.uk" reports example.co.uk.
_TWO_LEVEL_SUFFIXES = {
    "co.uk", "org.uk", "ac.uk", "com.au", "net.au", "org.au", "co.kr", "or.kr", "co.jp",
    "ne.jp", "com.br", "com.cn", "com.sg", "com.mx", "co.nz", "co.in", "co.za", "com.tw", "com.hk",
}

_BLANK = ("", "nan", "none", "#n/a", "n/a")


def missing_columns(df) -> list:
    return [] if Columns(df).url else ["product_url"]


def url_host(url: str) -> str:
    """Lowercase host of a URL without port or a leading "www." — or ""."""
    url = str(url or "").strip()
    if not url.startswith(("http://", "https://")):
        url = "https://" + url
    try:
        host = (urlparse(url).hostname or "").lower().rstrip(".")
    except ValueError:
        return ""
    host = host.removeprefix("www.")
    return host if "." in host else ""


def registrable_domain(host: str) -> str:
    """example.com from shop.example.com, example.co.uk from shop.example.co.uk."""
    parts = host.split(".")
    keep = 3 if len(parts) >= 3 and ".".join(parts[-2:]) in _TWO_LEVEL_SUFFIXES else 2
    return ".".join(parts[-keep:])


def host_matches(host: str, domains) -> str:
    """The domain in `domains` that `host` is, or is a subdomain of — or ""."""
    for d in domains:
        if host == d or host.endswith("." + d):
            return d
    return ""


def mfr_domains(mfr: str) -> set:
    """Known OEM domains for a manufacturer name. A key matches when it
    appears in the name as whole word(s), or equals the name with spaces
    and punctuation removed ("BlackmagicDesign" → blackmagicdesign)."""
    name = re.sub(r"[^a-z0-9 &/-]", "", str(mfr or "").lower()).strip()
    if not name:
        return set()
    compact = re.sub(r"[^a-z0-9]", "", name)
    found = set()
    for key, domains in MFR_DOMAINS.items():
        if re.search(r"(?<![a-z0-9])" + re.escape(key) + r"(?![a-z0-9])", name) \
                or re.sub(r"[^a-z0-9]", "", key) == compact:
            found |= domains
    return found


def check_record(mfr: str, product_url: str, flag) -> dict:
    """flag is True/False from is_oem_url, or None when the file has no
    such column. Returns status, domain, domain_type and note."""
    def res(status, domain, domain_type, note):
        return {"qa_status": status, "domain": domain, "domain_type": domain_type, "qa_note": note}

    url = clean(product_url)
    if url.lower() in _BLANK:
        return res("Error", "", "No URL", "product_url is blank or missing")
    host = url_host(url)
    if not host:
        return res("Error", "", "Malformed URL", f"Cannot parse domain from: {url[:60]}")
    domain = registrable_domain(host)
    no_flag = " (no is_oem_url column to compare against)"

    retailer = host_matches(host, KNOWN_RETAILERS)
    if retailer:
        if flag is None:
            return res("Review", retailer, "Retailer", f"URL is a retailer ({retailer}){no_flag}")
        if flag:
            return res("Fail", retailer, "Retailer", f"URL is a retailer ({retailer}) but is_oem_url is marked TRUE")
        return res("Pass", retailer, "Retailer", f"Correctly identified as retailer ({retailer})")

    own = host_matches(host, mfr_domains(mfr))
    if own:
        if flag is None:
            return res("Pass", own, "OEM", f"URL is this manufacturer's own site ({own}){no_flag}")
        if flag:
            return res("Pass", own, "OEM", f"Correctly identified as OEM ({own})")
        return res("Fail", own, "OEM", f"URL is manufacturer domain ({own}) but is_oem_url is marked FALSE")

    other = host_matches(host, ALL_OEM_DOMAINS)
    if other:
        if flag is False:
            return res("Fail", other, "OEM (different mfr)",
                       f"URL appears to be an OEM domain ({other}) but is_oem_url is marked FALSE")
        return res("Review", other, "OEM (different mfr)",
                   f"URL appears to be an OEM domain ({other}) but doesn't match the recorded "
                   f"manufacturer '{mfr}' — verify" + (no_flag if flag is None else ""))

    return res("Review", domain, "Unknown",
               f"Domain '{domain}' is not in the known manufacturer or retailer lists — manual check needed")


def _flag(val):
    """is_oem_url cell → True/False. Blank counts as FALSE, as before."""
    return clean(val).upper() in ("TRUE", "1", "YES", "1.0")


def run(df, log, progress=None) -> dict:
    cols = Columns(df)
    flag_col = find_col(df, "is_oem_url", "is oem url")
    if flag_col is None:
        log("No is_oem_url column — classifying domains only (non-OEM URLs come back Review)", "warn")

    results, total = {}, len(df)
    for i, (_, row) in enumerate(df.iterrows(), 1):
        flag = _flag(row.get(flag_col)) if flag_col else None
        r = check_record(clean(row.get(cols.mfr)) if cols.mfr else "", row.get(cols.url), flag)
        r["recorded_flag"] = clean(row.get(flag_col)) if flag_col else ""
        results[row[ROW_COL]] = r
        if progress and (i % 500 == 0 or i == total):
            progress(i, total)

    fails = sum(1 for r in results.values() if r["qa_status"] in ("Fail", "Error"))
    log(f"{total} URL(s) checked, {fails} fail/error", "warn" if fails else "ok")
    return results


def summarize(results: dict) -> list:
    status = lambda s: sum(1 for r in results.values() if r["qa_status"] == s)
    dtype = lambda t: sum(1 for r in results.values() if r["domain_type"] == t)
    return [
        ("Total records checked", len(results), None),
        ("", "", None),
        ("By result:", "", None),
        ("  Pass", status("Pass"), excel.GOOD),
        ("  Fail — flag mismatch", status("Fail"), excel.BAD),
        ("  Review — unknown or other domain", status("Review"), excel.WARN),
        ("  Error — missing/malformed URL", status("Error"), excel.INFO),
        ("", "", None),
        ("By domain type:", "", None),
        ("  OEM / manufacturer", dtype("OEM"), excel.GOOD),
        ("  Retailer / reseller", dtype("Retailer"), excel.WARN),
        ("  OEM of a different manufacturer", dtype("OEM (different mfr)"), excel.WARN),
        ("  Unknown domain", dtype("Unknown"), excel.INFO),
    ]


def save_excel(df, results: dict, path: str) -> None:
    cols = Columns(df)
    columns = ["Source File", "Manufacturer", "Model", "Product Name", "Product URL",
               "is_oem_url (DB)", "Detected Domain", "Domain Type", "Status", "Note"]
    rows = []
    for _, row in df.iterrows():
        r = results.get(row[ROW_COL])
        if r is None:
            continue
        p = cols.product(row)
        rows.append({
            "Source File": row.get(SOURCE_COL, ""), "Manufacturer": p["mfr"], "Model": p["model"],
            "Product Name": p["product_name"], "Product URL": clean(row.get(cols.url)) if cols.url else "",
            "is_oem_url (DB)": r["recorded_flag"], "Detected Domain": r["domain"],
            "Domain Type": r["domain_type"], "Status": r["qa_status"], "Note": r["qa_note"],
        })
    wb = Workbook()
    excel.write_sheet(wb, "OEM URL Check", columns, rows,
                      widths=[16, 18, 22, 40, 50, 14, 24, 20, 10, 60],
                      status_cols=["Status"], row_height=44)
    excel.write_summary(wb, "Summary", summarize(results), heading=f"{NAME} — Summary")
    wb.save(path)
