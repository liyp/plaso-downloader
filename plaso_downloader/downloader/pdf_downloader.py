"""Enhanced PDF downloader that also fetches converted page images when available."""

from __future__ import annotations

import logging
import os
import shutil
import time
from typing import List, Tuple

import requests
from PIL import Image

from ..api.file_api import FileAPI
from ..models import PDFResource
from ..utils.file_utils import ensure_directory, sanitize_filename
from ..utils.http_client import HttpClient


class PDFDownloader:
    """Downloads PDF files and optional page-level JPEGs."""

    def __init__(self, http_client: HttpClient, timeout: int = 10) -> None:
        self._http_client = http_client
        self.timeout = timeout

    def download(self, resource: PDFResource, output_file: str, file_api: FileAPI) -> None:
        ensure_directory(os.path.dirname(os.path.abspath(output_file)) or ".")
        file_info = None
        if resource.file_id:
            try:
                file_info = file_api.get_file_info(resource.file_id)
            except Exception:
                file_info = None

        pdf_location = (file_info or {}).get("pdfLocation")
        page_count = (file_info or {}).get("pdfConvertPages")

        if pdf_location and page_count:
            image_files, pages_directory = self._download_pdf_pages(pdf_location, page_count, resource, output_file)
            if image_files:
                self._assemble_pdf(output_file, image_files)
                shutil.rmtree(pages_directory, ignore_errors=True)
                return
            logging.warning("Falling back to direct PDF download for %s", resource.name)

        self._download_direct_pdf(resource, output_file, file_info)

    def _download_direct_pdf(self, resource: PDFResource, output_file: str, file_info: dict | None) -> None:
        attempts = 3
        download_url = resource.download_url
        if (not download_url or download_url == "") and file_info:
            location_path = file_info.get("locationPath")
            location = file_info.get("location")
            if location_path and location:
                download_url = f"https://filecdn.plaso.cn/{location_path}/{location}"
        for attempt in range(1, attempts + 1):
            try:
                if not download_url:
                    raise RuntimeError("No download URL available for PDF")
                self._http_client.download_cdn_file(download_url, output_file)
                logging.info("Saved PDF to %s", output_file)
                return
            except Exception as exc:  # pragma: no cover - network errors
                logging.warning(
                    "PDF download failed (attempt %s/%s) from %s: %s",
                    attempt,
                    attempts,
                    download_url or resource.download_url,
                    exc,
                )
                time.sleep(min(2 * attempt, 5))
        raise RuntimeError(f"Failed to download PDF from {download_url or resource.download_url}")

    def _download_pdf_pages(
        self,
        pdf_location: str,
        page_count: int | str,
        resource: PDFResource,
        output_file: str,
    ) -> Tuple[List[str], str]:
        pages_directory = os.path.join(
            os.path.dirname(output_file),
            f"{sanitize_filename(resource.name)}_pages",
        )
        ensure_directory(pages_directory)
        total_pages = int(page_count)
        image_files: List[str] = []
        logging.info("Downloading %s PDF pages for %s", total_pages, resource.name)
        for page in range(1, total_pages + 1):
            page_url = f"https://filecdn.plaso.cn/teaching/{pdf_location}/{page}.jpg"
            page_file = os.path.join(pages_directory, f"page_{page:03d}.jpg")
            if os.path.exists(page_file):
                image_files.append(page_file)
                continue
            try:
                with requests.get(page_url, stream=True, timeout=self.timeout) as resp:
                    if resp.status_code == 404:
                        logging.debug("PDF page %s not found (%s)", page, page_url)
                        break
                    resp.raise_for_status()
                    with open(page_file, "wb") as handle:
                        for chunk in resp.iter_content(chunk_size=1 << 14):
                            if chunk:
                                handle.write(chunk)
                image_files.append(page_file)
            except requests.RequestException as exc:  # pragma: no cover
                logging.warning("Failed to download PDF page %s from %s: %s", page, page_url, exc)
                break
        return image_files, pages_directory

    def _assemble_pdf(self, output_file: str, image_files: List[str]) -> None:
        if not image_files:
            raise RuntimeError("No PDF pages were downloaded to assemble")
        images: List[Image.Image] = []
        for image_path in image_files:
            with Image.open(image_path) as img:
                images.append(img.convert("RGB"))
        first, *rest = images
        first.save(output_file, "PDF", save_all=True, append_images=rest)
        logging.info("Assembled PDF from %s pages at %s", len(images), output_file)
