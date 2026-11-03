"""HTML → PDF: Chrome 헤드리스 --print-to-pdf (설치돼 있는 Chrome 사용, 추가 설치 없음. 확인 2026-10-10)."""

from __future__ import annotations

import os
import shutil
import subprocess
import tempfile
from pathlib import Path

CHROME_CANDIDATES = (
    r"C:\Program Files\Google\Chrome\Application\chrome.exe",
    r"C:\Program Files (x86)\Google\Chrome\Application\chrome.exe",
)
TIMEOUT_SEC = 90


def find_chrome() -> str | None:
    for c in CHROME_CANDIDATES:
        if Path(c).exists():
            return c
    return shutil.which("chrome")


def _kill_tree(proc: subprocess.Popen) -> None:
    if os.name == "nt":
        subprocess.run(["taskkill", "/F", "/T", "/PID", str(proc.pid)], capture_output=True, check=False)
    else:
        proc.kill()
    try:
        proc.communicate(timeout=10)
    except subprocess.TimeoutExpired:
        proc.kill()


def html_to_pdf(html_path: Path, pdf_path: Path) -> None:
    chrome = find_chrome()
    if chrome is None:
        raise RuntimeError("Chrome 을 찾지 못했습니다")
    # 매번 새 프로필: 이전 실행의 캐시·잠금에 영향받지 않는다
    # Chrome 자식 프로세스가 프로필 파일을 잠깐 더 붙잡고 있으면 Windows 에서 폴더 삭제가 실패해, 이미 만든 PDF 가 실패로 기록된다
    with tempfile.TemporaryDirectory(prefix="mv-pdf-", ignore_cleanup_errors=True) as profile:
        proc = subprocess.Popen(
            [chrome, "--headless=new", "--disable-gpu", "--no-first-run", f"--user-data-dir={profile}",
             "--no-pdf-header-footer", f"--print-to-pdf={pdf_path}", html_path.as_uri()],
            stdout=subprocess.PIPE, stderr=subprocess.PIPE)
        try:
            _, stderr = proc.communicate(timeout=TIMEOUT_SEC)
        except subprocess.TimeoutExpired:
            _kill_tree(proc)            # subprocess.run 은 본체만 죽여 자식(렌더러·GPU) 프로세스가 프로필 폴더를 붙잡고 남는다
            raise RuntimeError(f"PDF 생성 시간 초과 ({TIMEOUT_SEC}초)") from None
    if not pdf_path.exists() or pdf_path.stat().st_size < 1000:
        tail = (stderr or b"").decode("utf-8", "replace").strip().splitlines()[-1:] or [""]
        raise RuntimeError(f"PDF 가 만들어지지 않았습니다 (exit {proc.returncode}) {tail[0][:120]}")
