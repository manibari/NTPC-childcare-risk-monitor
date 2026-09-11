"""Batch OCR every nonprofit financial-report PDF into data/ocr/<stem>.jsonl.

Each line: {"page": n, "lines": [{"y":..,"x":..,"w":..,"h":..,"text":..}, ...]}
(y measured from top, 0..1). Resumable: skips stems whose jsonl already exists.
"""
import glob
import json
import multiprocessing as mp
import os
import pathlib
import subprocess
import sys
import tempfile

ROOT = pathlib.Path(__file__).resolve().parent.parent
RAW = ROOT / "raw/資料集/非營利園財報"
OUT = ROOT / "data/ocr"
DPI = 200


def ocr_png(path: str) -> list[dict]:
    import Quartz
    import Vision
    from Foundation import NSURL

    url = NSURL.fileURLWithPath_(path)
    src = Quartz.CGImageSourceCreateWithURL(url, None)
    img = Quartz.CGImageSourceCreateImageAtIndex(src, 0, None)
    req = Vision.VNRecognizeTextRequest.alloc().init()
    req.setRecognitionLevel_(Vision.VNRequestTextRecognitionLevelAccurate)
    req.setRecognitionLanguages_(["zh-Hant", "en"])
    req.setUsesLanguageCorrection_(False)
    handler = Vision.VNImageRequestHandler.alloc().initWithCGImage_options_(img, None)
    ok, err = handler.performRequests_error_([req], None)
    if not ok:
        raise RuntimeError(str(err))
    out = []
    for obs in req.results():
        b = obs.boundingBox()
        out.append({
            "y": round(1 - b.origin.y - b.size.height, 4),
            "x": round(b.origin.x, 4),
            "w": round(b.size.width, 4),
            "h": round(b.size.height, 4),
            "text": obs.topCandidates_(1)[0].string(),
        })
    out.sort(key=lambda r: (r["y"], r["x"]))
    return out


def process_pdf(pdf: str) -> str:
    stem = pathlib.Path(pdf).stem
    target = OUT / f"{stem}.jsonl"
    if target.exists():
        return f"skip {stem}"
    with tempfile.TemporaryDirectory() as td:
        subprocess.run(["pdftoppm", "-r", str(DPI), "-png", pdf, f"{td}/p"], check=True)
        pngs = sorted(glob.glob(f"{td}/p-*.png"))
        tmp = target.with_suffix(".tmp")
        with open(tmp, "w") as f:
            for i, png in enumerate(pngs, 1):
                f.write(json.dumps({"page": i, "lines": ocr_png(png)}, ensure_ascii=False) + "\n")
        tmp.rename(target)
    return f"done {stem} ({len(pngs)}p)"


def main() -> None:
    OUT.mkdir(parents=True, exist_ok=True)
    pdfs = sorted(glob.glob(str(RAW / "*/*.pdf")))
    workers = int(sys.argv[1]) if len(sys.argv) > 1 else 4
    ctx = mp.get_context("spawn")
    with ctx.Pool(workers) as pool:
        for msg in pool.imap_unordered(process_pdf, pdfs):
            print(msg, flush=True)


if __name__ == "__main__":
    main()
