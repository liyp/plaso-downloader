"""Asynchronous downloader that converts m3u8 playlists into MP4 files."""

from __future__ import annotations

import asyncio
import logging
import os
import shutil
import subprocess
from typing import List, Tuple
from urllib.parse import urlparse

from ..api.file_api import FileAPI
from ..models import M3U8Info, VideoResource
from ..utils.file_utils import build_tmp_segment_dir, cleanup_directory, ensure_directory
from ..utils.http_client import HttpClient
from .m3u8_parser import M3U8Parser

SegmentPlan = List[Tuple[int, str, str]]


class VideoDownloader:
    """Downloads TS segments concurrently and merges them into an MP4."""

    def __init__(self, http_client: HttpClient, workers: int = 16) -> None:
        self.workers = workers
        self._http_client = http_client
        self._parser = M3U8Parser(http_client)

    def download(self, video: VideoResource, output_file: str, file_api: FileAPI) -> None:
        info = None
        m3u8_url = None
        last_error: Exception | None = None
        for candidate in self._resolve_m3u8_urls(video, file_api):
            try:
                candidate_info = self._parser.parse(candidate)
            except Exception as exc:  # pragma: no cover - network errors
                last_error = exc
                logging.debug("Failed to parse playlist %s: %s", candidate, exc)
                continue
            if not candidate_info.ts_urls:
                last_error = ValueError(f"Playlist {candidate} does not contain ts segments")
                logging.debug("Playlist %s had no TS segments", candidate)
                continue
            info = candidate_info
            m3u8_url = candidate
            break

        if not info or not m3u8_url:
            if last_error:
                raise last_error
            raise ValueError("Unable to resolve m3u8 playlist for the provided video")

        ensure_directory(os.path.dirname(os.path.abspath(output_file)) or ".")
        tmp_segment_dir = build_tmp_segment_dir(output_file)
        segment_plan = self._build_segment_plan(info, tmp_segment_dir)
        ts_output = output_file if output_file.endswith(".ts") else f"{output_file}.ts"

        try:
            asyncio.run(self._download_segments(segment_plan))
            self._merge_segments(segment_plan, ts_output)
            if ts_output != output_file:
                self._convert_ts_to_mp4(ts_output, output_file)
        except Exception:
            if os.path.exists(output_file):
                os.remove(output_file)
            if ts_output != output_file and os.path.exists(ts_output):
                os.remove(ts_output)
            raise
        else:
            cleanup_directory(tmp_segment_dir)
            parent_tmp = os.path.dirname(tmp_segment_dir)
            if os.path.isdir(parent_tmp) and not os.listdir(parent_tmp):
                shutil.rmtree(parent_tmp, ignore_errors=True)

    def _resolve_m3u8_urls(self, video: VideoResource, file_api: FileAPI) -> List[str]:
        candidates: List[str] = []
        play_info_urls = self._get_play_info_urls(video, file_api)
        if play_info_urls:
            candidates.extend(play_info_urls)

        if video.file_id and not play_info_urls:
            try:
                info = file_api.get_file_info(video.file_id)
            except Exception:
                info = None
            if info:
                location_path = info.get("locationPath") or video.location_path
                location = info.get("location") or video.location
            else:
                location_path = video.location_path
                location = video.location
        else:
            location_path = video.location_path
            location = video.location

        location_path = (location_path or "").strip("/")
        location_value = (location or "").strip("/")

        if location_path and location_value:
            base = location_path
            if base.startswith("liveclass") and "plaso" not in base.split("/"):
                base = f"{base}/plaso"
            prefix = f"https://filecdn.plaso.cn/{base}/{location_value}"
            for quality in ("a2", "a1", "a0", "a"):
                candidates.append(f"{prefix}/{quality}/a.m3u8")

        if video.m3u8_url:
            candidates.append(video.m3u8_url)

        unique_candidates: List[str] = []
        seen = set()
        for candidate in candidates:
            if candidate and candidate not in seen:
                unique_candidates.append(candidate)
                seen.add(candidate)
        return unique_candidates

    def _get_play_info_urls(self, video: VideoResource, file_api: FileAPI) -> List[str]:
        if not video.requires_play_info or not video.file_id:
            return []
        try:
            info = file_api.get_play_info(video.file_id)
        except Exception:
            return []

        urls: List[str] = []
        hd = info.get("hdPlayUrl")
        sd = info.get("sdPlayUrl")
        ld = info.get("ldPlayUrl")
        od = info.get("odPlayUrl")
        for url in (hd, sd, ld, od):
            if url:
                urls.append(url)

        play_urls = info.get("playUrls") or []
        for url in play_urls:
            if url:
                urls.append(url)

        play_urls_v2 = info.get("playUrlsV2") or []
        for group in play_urls_v2:
            if not isinstance(group, dict):
                continue
            for key in ("hd", "sd", "ld", "od"):
                value = group.get(key)
                if value:
                    urls.append(value)
        return urls

    def _build_segment_plan(self, info: M3U8Info, tmp_dir: str) -> SegmentPlan:
        ensure_directory(tmp_dir)
        plan: SegmentPlan = []
        for index, url in enumerate(info.ts_urls):
            parsed = urlparse(url)
            basename = os.path.basename(parsed.path) or f"segment_{index}.ts"
            dest_path = os.path.join(tmp_dir, f"{index:05d}_{basename}")
            plan.append((index, url, dest_path))
        return plan

    async def _download_segments(self, plan: SegmentPlan) -> None:
        if not plan:
            return

        sem = asyncio.Semaphore(self.workers)
        tasks = [self._download_single(sem, index, url, dest) for index, url, dest in plan]
        await asyncio.gather(*tasks)

    async def _download_single(
        self,
        sem: asyncio.Semaphore,
        index: int,
        url: str,
        dest_path: str,
    ) -> None:
        if os.path.exists(dest_path) and os.path.getsize(dest_path) > 0:
            logging.debug("Skipping existing TS #%s", index)
            return

        attempts = 3
        for attempt in range(1, attempts + 1):
            try:
                async with sem:
                    ensure_directory(os.path.dirname(dest_path))
                    await self._http_client.download_cdn_stream(url, dest_path)
                logging.debug("Downloaded TS #%s", index)
                return
            except Exception as exc:  # pragma: no cover - network errors
                logging.warning("TS #%s download failed (attempt %s/%s): %s", index, attempt, attempts, exc)
                await asyncio.sleep(min(2 * attempt, 5))

        raise RuntimeError(f"Failed to download segment {index} from {url}")

    def _merge_segments(self, plan: SegmentPlan, output_file: str) -> None:
        ordered_paths = [dest for index, _, dest in sorted(plan, key=lambda item: item[0])]
        logging.info("Merging %s segments into %s", len(ordered_paths), output_file)
        with open(output_file, "wb") as merged:
            for segment_path in ordered_paths:
                if not os.path.exists(segment_path):
                    raise FileNotFoundError(f"Missing TS segment: {segment_path}")
                with open(segment_path, "rb") as segment_file:
                    merged.write(segment_file.read())
        logging.info("Saved video to %s", output_file)

    def _convert_ts_to_mp4(self, ts_path: str, mp4_path: str) -> None:
        ffmpeg_bin = shutil.which("ffmpeg")
        if not ffmpeg_bin:
            logging.warning("ffmpeg not found. Keeping TS file at %s", ts_path)
            if not mp4_path.endswith(".ts"):
                shutil.move(ts_path, mp4_path)
            return

        commands = [
            [ffmpeg_bin, "-loglevel", "error", "-y", "-i", ts_path, "-c", "copy", mp4_path],
            [ffmpeg_bin, "-loglevel", "error", "-y", "-i", ts_path, "-c:v", "copy", "-c:a", "aac", mp4_path],
        ]

        for cmd in commands:
            logging.info("Converting TS to MP4 via ffmpeg: %s", " ".join(cmd))
            try:
                subprocess.run(cmd, check=True)
                os.remove(ts_path)
                return
            except subprocess.CalledProcessError as exc:
                logging.error("ffmpeg remux failed: %s", exc)

        logging.warning("All ffmpeg remux attempts failed; keeping TS file")
        if not mp4_path.endswith(".ts"):
            shutil.move(ts_path, f"{mp4_path}.ts")
