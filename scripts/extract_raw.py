"""Re-extract the organiser's zips into raw/ with Big5 (cp950) filename decoding."""
import pathlib
import zipfile

DOWNLOADS = pathlib.Path.home() / "Downloads"
ZIPS = ["E_教育局-資料集.zip", "E_教育局-命題文件.zip"]
OUT = pathlib.Path(__file__).resolve().parent.parent / "raw"


def decode(name: str) -> str:
    try:
        return name.encode("cp437").decode("cp950")
    except (UnicodeEncodeError, UnicodeDecodeError):
        return name


def main() -> None:
    for z in ZIPS:
        with zipfile.ZipFile(DOWNLOADS / z) as zf:
            for info in zf.infolist():
                target = OUT / decode(info.filename)
                if info.is_dir():
                    target.mkdir(parents=True, exist_ok=True)
                    continue
                target.parent.mkdir(parents=True, exist_ok=True)
                target.write_bytes(zf.read(info))
    print("extracted to", OUT)


if __name__ == "__main__":
    main()
