from __future__ import annotations

import sys
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[1]
SRC = PROJECT_ROOT / "src"
if str(SRC) not in sys.path:
    sys.path.insert(0, str(SRC))

from lftp_kb.recovery_verification import RecoveryVerifier, configured_audio_root


def main() -> int:
    audio_root = configured_audio_root(PROJECT_ROOT)
    if audio_root is None:
        print(
            "No local audio archive is configured. Open Sources & providers in the app, save "
            "the archive path, and run this shortcut again."
        )
        return 2
    if not audio_root.is_dir():
        print(f"The configured local audio archive does not exist: {audio_root}")
        return 2
    verifier = RecoveryVerifier(PROJECT_ROOT, audio_root)
    payload = verifier.run()
    failures = sum(
        result["classification"] in {"source-download-failed", "comparison-failed"}
        for result in payload["results"]
    )
    print()
    print("Focused verification is complete. No master audio was changed.")
    print(f"Report: {verifier.output / 'RECOVERY_VERIFICATION.md'}")
    if failures:
        print(f"{failures} source(s) could not be checked; their errors are preserved for retry.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
