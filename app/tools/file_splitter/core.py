"""
File Splitter engine — pure Python, no UI dependency.

Splits a large CSV/Excel product file into smaller CSV batch files of a
fixed number of rows (e.g. to feed an AI check a manageable chunk at a
time). Every output file keeps the header row.
"""

from pathlib import Path

from tools.common.data import load_df

NAME = "File Splitter"


def split_file(path: str, out_dir: str, rows_per_file: int, prefix: str = "",
               log=None, progress=None) -> list:
    """
    Split one file into CSVs named <prefix>1.csv, <prefix>2.csv, ... in
    out_dir. A blank prefix uses the input file's name (spaces/dashes
    turned into underscores). Returns [{"file": path, "rows": n}, ...].
    progress(done_files, total_files) is called after each file is written.
    """
    if rows_per_file < 1:
        raise ValueError("Rows per file must be at least 1")
    src = Path(path)
    prefix = prefix.strip() or src.stem.replace(" ", "_").replace("-", "_")
    df = load_df(path)
    total = len(df)
    num_files = max(1, (total + rows_per_file - 1) // rows_per_file)
    if log:
        log(f"{src.name}: {total} rows → {num_files} file(s) of up to {rows_per_file} rows", "info")

    Path(out_dir).mkdir(parents=True, exist_ok=True)
    written = []
    for i in range(num_files):
        chunk = df.iloc[i * rows_per_file:(i + 1) * rows_per_file]
        out = Path(out_dir) / f"{prefix}{i + 1}.csv"
        chunk.to_csv(out, index=False, encoding="utf-8")
        written.append({"file": str(out), "rows": len(chunk)})
        if log:
            log(f"  {out.name}  ({len(chunk)} rows)", "ok")
        if progress:
            progress(i + 1, num_files)
    return written
