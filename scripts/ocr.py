"""Local OCR for PDF pages without a text layer and for image files. Nothing is uploaded.

Engines, tried in this order, whichever is installed:
  rapidocr   pip install rapidocr_onnxruntime   (any system; good with Chinese)
  windows    Windows 10/11 built-in OCR          (needs the language's OCR pack; no install)
  tesseract  Tesseract + PyMuPDF                 (needs chi_sim / eng traineddata)
"""

from __future__ import annotations

import json
import os
import re
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path
from typing import Any

RENDER_DPI = 200
WIDE = r"\u3000-\u303f\u4e00-\u9fff\uff00-\uffef"  # CJK characters and full-width punctuation
WIDE_CHAR = re.compile(f"[{WIDE}]")
NEAR_WIDE_SPACE = re.compile(f"(?<=[{WIDE}]) +| +(?=[{WIDE}])")
DIGIT_SPACE = re.compile(r"(?<=[\d,.\uff0c]) (?=[\d,.\uff0c])")
# "12345 Q 6789" -> "12345Q6789": in Chinese lines a single letter or digit split off by spaces belongs to its neighbours.
LONE_ALNUM_SPACE = re.compile(r"(?<=(?<![A-Za-z0-9])[A-Za-z0-9]) (?=[A-Za-z0-9])|(?<=[A-Za-z0-9]) (?=[A-Za-z0-9](?![A-Za-z0-9]))")

WINDOWS_SCRIPT = r"""
param([string]$ListFile)
$ErrorActionPreference = 'Stop'
[Console]::OutputEncoding = [System.Text.Encoding]::UTF8
Add-Type -AssemblyName System.Runtime.WindowsRuntime
$asTask = ([System.WindowsRuntimeSystemExtensions].GetMethods() | Where-Object {
  $_.Name -eq 'AsTask' -and $_.GetParameters().Count -eq 1 -and $_.GetParameters()[0].ParameterType.Name -eq 'IAsyncOperation`1' })[0]
function Await($op, [Type]$type) { $t = $asTask.MakeGenericMethod($type).Invoke($null, @($op)); $t.Wait(-1) | Out-Null; $t.Result }
[Windows.Storage.StorageFile,Windows.Storage,ContentType=WindowsRuntime] | Out-Null
[Windows.Media.Ocr.OcrEngine,Windows.Foundation,ContentType=WindowsRuntime] | Out-Null
[Windows.Graphics.Imaging.BitmapDecoder,Windows.Graphics,ContentType=WindowsRuntime] | Out-Null
$langs = [Windows.Media.Ocr.OcrEngine]::AvailableRecognizerLanguages
$zh = $langs | Where-Object { $_.LanguageTag -like 'zh*' } | Select-Object -First 1
$en = $langs | Where-Object { $_.LanguageTag -like 'en*' } | Select-Object -First 1
$zhEngine = $null; $enEngine = $null
if ($zh) { $zhEngine = [Windows.Media.Ocr.OcrEngine]::TryCreateFromLanguage($zh) }
if ($en) { $enEngine = [Windows.Media.Ocr.OcrEngine]::TryCreateFromLanguage($en) }
if (-not $zhEngine -and -not $enEngine) { $enEngine = [Windows.Media.Ocr.OcrEngine]::TryCreateFromUserProfileLanguages() }
if ($ListFile -eq 'PROBE') { if ($zhEngine -or $enEngine) { 'ok' } else { 'none' }; exit 0 }
function Read-Text($engine, $bitmap) {
  if (-not $engine) { return '' }
  $result = Await ($engine.RecognizeAsync($bitmap)) ([Windows.Media.Ocr.OcrResult])
  return (($result.Lines | ForEach-Object { $_.Text }) -join "`n")
}
foreach ($path in Get-Content -LiteralPath $ListFile -Encoding UTF8) {
  try {
    $file = Await ([Windows.Storage.StorageFile]::GetFileFromPathAsync($path)) ([Windows.Storage.StorageFile])
    $stream = Await ($file.OpenAsync([Windows.Storage.FileAccessMode]::Read)) ([Windows.Storage.Streams.IRandomAccessStream])
    $decoder = Await ([Windows.Graphics.Imaging.BitmapDecoder]::CreateAsync($stream)) ([Windows.Graphics.Imaging.BitmapDecoder])
    $bitmap = Await ($decoder.GetSoftwareBitmapAsync()) ([Windows.Graphics.Imaging.SoftwareBitmap])
    $text = Read-Text $zhEngine $bitmap
    $cjk = ([regex]::Matches($text, '[\u4e00-\u9fff]')).Count
    # Mostly Latin text reads better with the English recognizer (keeps word spacing).
    if ($enEngine -and ($cjk -lt [Math]::Max(3, $text.Length * 0.1))) {
      $english = Read-Text $enEngine $bitmap
      if ($english.Trim().Length -ge ($text.Trim().Length * 0.6)) { $text = $english }
    }
    $stream.Dispose()
    [pscustomobject]@{ path = [string]$path; text = [string]$text } | ConvertTo-Json -Compress
  } catch {
    [pscustomobject]@{ path = [string]$path; error = [string]$_.Exception.Message } | ConvertTo-Json -Compress
  }
}
"""

_available: list[str] | None = None


def _powershell() -> str | None:
    if sys.platform != "win32":
        return None
    for name in ("powershell.exe", "powershell"):
        found = shutil.which(name)
        if found:
            return found
    guess = Path(os.environ.get("SystemRoot", r"C:\Windows")) / "System32" / "WindowsPowerShell" / "v1.0" / "powershell.exe"
    return str(guess) if guess.exists() else None


def _script() -> Path:
    path = Path(tempfile.gettempdir()) / "audit-enterprise-knowledge-ocr.ps1"
    if not path.exists() or path.read_text(encoding="utf-8-sig") != WINDOWS_SCRIPT:
        path.write_text(WINDOWS_SCRIPT, encoding="utf-8-sig")  # BOM: Windows PowerShell reads it as UTF-8
    return path


_RAPID: Any = None
NO_ENGINE = "本机没有可用的 OCR（可装 rapidocr_onnxruntime，或在 Windows 设置里给中文/英文添加“光学字符识别”）"
MAX_SIDE = 8000  # Windows OCR refuses images larger than about 10000 px


def _rapid() -> Any:
    global _RAPID
    if _RAPID is not None:
        return _RAPID
    for module in ("rapidocr_onnxruntime", "rapidocr"):
        try:
            _RAPID = __import__(module, fromlist=["RapidOCR"]).RapidOCR()
            return _RAPID
        except Exception:
            continue
    return None


def _fitz() -> Any:
    try:
        import pymupdf as fitz  # type: ignore
    except ImportError:
        try:
            import fitz  # type: ignore
        except ImportError:
            return None
    return fitz


def engines() -> list[str]:
    """Which OCR engines work on this computer (checked once per run)."""
    global _available
    if _available is not None:
        return _available
    found = []
    if os.environ.get("AUDIT_OCR", "").lower() == "off":
        _available = []
        return _available
    if _rapid() is not None:
        found.append("rapidocr")
    shell = _powershell()
    if shell:
        try:
            probe = subprocess.run([shell, "-NoProfile", "-ExecutionPolicy", "Bypass", "-File", str(_script()), "PROBE"],
                                   capture_output=True, text=True, encoding="utf-8", timeout=60)
            if probe.stdout.strip().endswith("ok"):
                found.append("windows")
        except (OSError, subprocess.TimeoutExpired):
            pass
    if shutil.which("tesseract") and _fitz() is not None:
        found.append("tesseract")
    _available = found
    return found


def clean(text: str) -> str:
    """Chinese OCR output puts spaces between characters and digits ("2 0 0 8 年"); join them back."""
    lines = []
    for line in (text or "").splitlines():
        if WIDE_CHAR.search(line):
            line = NEAR_WIDE_SPACE.sub("", line)
            line = DIGIT_SPACE.sub("", line)
            line = LONE_ALNUM_SPACE.sub("", line)
            line = re.sub(r"(?<=\d)，(?=\d{3})", ",", line)
        line = line.strip()
        if line:
            lines.append(line)
    return "\n".join(lines)


def _ocr_rapid(images: list[Path]) -> dict[str, str]:
    engine = _rapid()
    out = {}
    for image in images:
        result = engine(str(image))
        if hasattr(result, "txts"):  # rapidocr >= 2
            texts = list(result.txts or [])
        else:
            rows = result[0] if isinstance(result, tuple) else result
            texts = [row[1] for row in rows or []]
        out[str(image)] = "\n".join(texts)
    return out


def _ocr_windows(images: list[Path]) -> dict[str, str]:
    handle, name = tempfile.mkstemp(suffix=".txt", prefix="audit-ocr-")
    os.close(handle)  # Windows cannot delete a file that is still open
    listing = Path(name)
    listing.write_text("\n".join(str(p) for p in images), encoding="utf-8-sig")
    try:
        done = subprocess.run([_powershell(), "-NoProfile", "-ExecutionPolicy", "Bypass", "-File", str(_script()), str(listing)],
                              capture_output=True, text=True, encoding="utf-8", timeout=120 + 10 * len(images))
    finally:
        listing.unlink(missing_ok=True)
    out = {}
    for line in done.stdout.splitlines():
        line = line.strip()
        if line.startswith("{"):
            try:
                row = json.loads(line)
            except json.JSONDecodeError:
                continue
            out[row.get("path", "")] = row.get("text") or ""
    return out


def _ocr_tesseract(images: list[Path]) -> dict[str, str]:
    fitz = _fitz()
    out = {}
    for image in images:
        document = fitz.open(str(image))
        page = fitz.open("pdf", document.convert_to_pdf())[0]
        for language in ("chi_sim+eng", "eng"):
            try:
                out[str(image)] = page.get_textpage_ocr(language=language, dpi=RENDER_DPI, full=True).extractText()
                break
            except Exception:
                continue
    return out


def _run(images: list[Path]) -> tuple[dict[str, str], str, str]:
    """Try each engine in turn on all images; the first that reads real text wins."""
    problems = []
    for engine in engines():
        try:
            raw = {"rapidocr": _ocr_rapid, "windows": _ocr_windows, "tesseract": _ocr_tesseract}[engine](images)
        except Exception as error:  # try the next engine
            problems.append(f"{engine}: {type(error).__name__}")
            continue
        texts = {key: clean(value) for key, value in raw.items()}
        if any(len(t) >= 20 for t in texts.values()):
            return texts, engine, ""
        problems.append(f"{engine}: 没识别出文字")
    return {}, "", "OCR 后仍读不出文字" + (f"（{'；'.join(problems)}）" if problems else "")


def _normalized(source: Path, target: Path) -> Path:
    """RGB PNG no larger than MAX_SIDE, so every engine can open it; falls back to the original."""
    fitz = _fitz()
    if fitz is None:
        return source
    try:
        pixmap = fitz.Pixmap(str(source))
        if pixmap.alpha:
            pixmap = fitz.Pixmap(pixmap, 0)
        if pixmap.colorspace is None or pixmap.colorspace.n != 3:
            pixmap = fitz.Pixmap(fitz.csRGB, pixmap)
        while max(pixmap.width, pixmap.height) > MAX_SIDE:
            pixmap.shrink(1)
        pixmap.save(str(target))
        return target
    except Exception:
        return source


def ocr_images(paths: list[Path]) -> tuple[dict[str, str], str, str]:
    """OCR image files. Returns ({original path: text}, engine used, problem if nothing worked)."""
    if not engines():
        return {}, "", NO_ENGINE
    work = Path(tempfile.mkdtemp(prefix="audit-ocr-"))
    try:
        prepared = {_normalized(path, work / f"i{number:05d}.png"): path for number, path in enumerate(paths)}
        texts, engine, problem = _run(list(prepared))
        return {str(prepared[Path(key)]): text for key, text in texts.items() if Path(key) in prepared}, engine, problem
    finally:
        shutil.rmtree(work, ignore_errors=True)


def ocr_pdf_pages(pdf: Path, pages: list[int]) -> tuple[dict[int, str], str, str]:
    """OCR the given 1-based PDF pages. Returns ({page: text}, engine used, problem if nothing worked)."""
    if not engines():
        return {}, "", NO_ENGINE
    fitz = _fitz()
    if fitz is None:
        return {}, "", "要先装 PyMuPDF 才能把 PDF 页转成图片做 OCR"
    work = Path(tempfile.mkdtemp(prefix="audit-ocr-"))
    try:
        document = fitz.open(str(pdf))
        images = {}
        for number in pages:
            image = work / f"p{number:04d}.png"
            document[number - 1].get_pixmap(dpi=RENDER_DPI).save(str(image))
            images[str(image)] = number
        document.close()
        texts, engine, problem = _run([Path(k) for k in images])
        return {images[str(Path(key))]: text for key, text in texts.items() if str(Path(key)) in images}, engine, problem
    finally:
        shutil.rmtree(work, ignore_errors=True)
