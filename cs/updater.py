import requests
from PyQt6.QtCore import QThread, pyqtSignal

from cs.config import APP_VERSION


GITHUB_RELEASES_API  = "https://api.github.com/repos/ZAKhan/crypto-scanner/releases/latest"
GITHUB_RELEASES_PAGE = "https://github.com/ZAKhan/crypto-scanner/releases/latest"


class UpdateChecker(QThread):
    """
    Checks GitHub releases API for a newer version tag.
    Runs once in a background thread — never blocks the UI.
    Emits update_available(latest_tag, release_url) if a newer version exists.
    Silently swallows all errors (no internet, rate limit, bad JSON, etc.).
    """
    update_available = pyqtSignal(str, str)   # latest_tag, release_url

    def run(self):
        try:
            resp = requests.get(
                GITHUB_RELEASES_API,
                timeout=8,
                headers={"Accept": "application/vnd.github+json",
                         "User-Agent": f"CryptoScalperScanner/{APP_VERSION}"}
            )
            if resp.status_code != 200:
                return
            data = resp.json()
            latest_tag = data.get("tag_name", "").strip()
            html_url   = data.get("html_url", GITHUB_RELEASES_PAGE).strip()
            if not latest_tag:
                return
            # Normalise tags to comparable tuples: "v2.4.3" -> (2, 4, 3)
            def _parse(tag):
                import re as _re
                nums = _re.findall(r"\d+", tag)
                return tuple(int(n) for n in nums)
            if _parse(latest_tag) > _parse(APP_VERSION):
                self.update_available.emit(latest_tag, html_url)
        except Exception:
            pass   # network error, timeout, parse error — silently ignore
