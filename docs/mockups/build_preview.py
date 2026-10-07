"""Wrap the mentor-review fragment as an offline, standalone HTML document.

This renderer has no connection to the synthetic-data generator. Run it from
any working directory after editing the fragment, and commit both HTML files.
"""

from pathlib import Path


MOCKUP_DIR = Path(__file__).resolve().parent
SOURCE = MOCKUP_DIR / "fcip-upload-results.html"
DESTINATION = MOCKUP_DIR / "fcip-upload-results-preview.html"


def main() -> None:
    fragment = SOURCE.read_text(encoding="utf-8")
    prefix = """<!doctype html>
<html lang="id">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<meta name="referrer" content="no-referrer">
<meta http-equiv="Content-Security-Policy" content="default-src 'none'; script-src 'unsafe-inline'; style-src 'unsafe-inline'; img-src data:; connect-src 'none'; object-src 'none'; base-uri 'none'; form-action 'none'">
<title>FCIP — Upload-to-results mentor mockup</title>
<style>
html { color-scheme: light dark; background: light-dark(#f4f6f8, #101722); }
body { margin: 0; }
</style>
</head>
<body>
"""
    DESTINATION.write_text(prefix + fragment + "\n</body>\n</html>\n", encoding="utf-8")
    print(DESTINATION)


if __name__ == "__main__":
    main()
