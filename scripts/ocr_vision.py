"""OCR a PNG with macOS Vision (zh-Hant + en). Usage: ocr_vision.py <png> [more.png ...]"""
import sys
import Quartz
import Vision
from Foundation import NSURL


def ocr(path: str) -> list[str]:
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
        raise RuntimeError(err)
    lines = []
    for obs in req.results():
        box = obs.boundingBox()
        y = 1 - box.origin.y - box.size.height
        lines.append((round(y, 2), box.origin.x, obs.topCandidates_(1)[0].string()))
    lines.sort()
    return [f"{y:.2f}\t{x:.2f}\t{s}" for y, x, s in lines]


if __name__ == "__main__":
    for p in sys.argv[1:]:
        print(f"===== {p}")
        print("\n".join(ocr(p)))
