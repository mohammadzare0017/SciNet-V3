# src/config/download_policy.py
from dataclasses import dataclass
import os

def _flag(name: str, default="0") -> bool:
    return os.getenv(name, default).strip().lower() in {"1", "true", "yes", "on"}

@dataclass(frozen=True)
class DownloadPolicy:
    dry_run: bool = _flag("SCINET_DRYRUN", "0")
    iranpaper_only: bool = _flag("IRANPAPER_ONLY", "0")
    enable_iranpaper: bool = _flag("ENABLE_IRANPAPER", "1")
    enable_gigalib: bool = _flag("ENABLE_GIGALIB", "1")

    def sources(self) -> list[str]:
        if self.iranpaper_only:
            return ["iranpaper"]
        s = []
        if self.enable_iranpaper:
            s.append("iranpaper")
        if self.enable_gigalib:
            s.append("gigalib")
        return s

    def allow_gigalib(self) -> bool:
        return (not self.iranpaper_only) and self.enable_gigalib

_POLICY = None
def get_policy() -> DownloadPolicy:
    global _POLICY
    if _POLICY is None:
        _POLICY = DownloadPolicy()
    return _POLICY
