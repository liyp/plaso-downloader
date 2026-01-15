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

    def __init__(self, http_client: HttpClient, workers: int = 16, keep_ts: bool = False) -> None:
        self.workers = workers
        self.keep_ts = keep_ts
        self._http_client = http_client
        self._parser = M3U8Parser(http_client)

    def download(self, video: VideoResource, output_file: str, file_api: FileAPI, 
                 expected_duration: int | None = None) -> None:
        # Debug: Log video resource info
        logging.info("[DEBUG] VideoResource: file_id=%s, location_path=%s, location=%s", 
                     video.file_id, video.location_path, video.location)
        logging.info("[DEBUG] VideoResource m3u8_url=%s", video.m3u8_url)
        
        # Get file info to check locationPath
        location_path = video.location_path
        location = video.location
        file_info = None
        if video.file_id:
            try:
                file_info = file_api.get_file_info(video.file_id)
                location_path = file_info.get("locationPath") or location_path
                location = file_info.get("location") or location
            except Exception:
                pass
        
        m3u8_urls = []
        base_url = None  # Initialize to avoid UnboundLocalError
        
        # Check if this is an ossvideo type - use getPlayInfo API directly
        if location_path == "ossvideo" and video.file_id and location:
            logging.info("[DEBUG] Detected ossvideo type, using getPlayInfo API")
            try:
                # The location field contains the record ID needed for getPlayInfo
                play_info = file_api.get_play_info(location, video.file_id)
                if play_info:
                    # Prefer HD, then SD, then LD
                    hd_url = play_info.get("hdPlayUrl")
                    sd_url = play_info.get("sdPlayUrl") 
                    ld_url = play_info.get("ldPlayUrl")
                    
                    if hd_url:
                        m3u8_urls.append(hd_url)
                        logging.info("[DEBUG] Got HD m3u8 from getPlayInfo: %s", hd_url[:80] + "..." if len(hd_url) > 80 else hd_url)
                    elif sd_url:
                        m3u8_urls.append(sd_url)
                        logging.info("[DEBUG] Got SD m3u8 from getPlayInfo: %s", sd_url[:80] + "..." if len(sd_url) > 80 else sd_url)
                    elif ld_url:
                        m3u8_urls.append(ld_url)
                        logging.info("[DEBUG] Got LD m3u8 from getPlayInfo: %s", ld_url[:80] + "..." if len(ld_url) > 80 else ld_url)
            except Exception as exc:
                logging.warning("[DEBUG] getPlayInfo failed: %s", exc)
        
        # For liveclass type or if ossvideo didn't work, try info.plist approach
        if not m3u8_urls:
            # Resolve base path for constructing m3u8 URLs  
            base_url = self._resolve_base_url(video, file_api)
            logging.info("[DEBUG] Resolved base URL: %s", base_url)
            
            # Try to get multi-segment info from info.plist
            media_segments = self._get_media_segments_from_plist(base_url, file_api, location)
            if media_segments:
                logging.info("[DEBUG] Found %d media segments in info.plist", len(media_segments))
                for seg in media_segments:
                    m3u8_urls.append(f"{base_url}/{seg['path']}")
        
        # Fallback to single m3u8 URL resolution if no multi-segment info
        if not m3u8_urls:
            logging.warning("Falling back to scanning all candidate m3u8 URLs. This may Result in duplicates if multiple qualities exist.")
            candidates = self._resolve_m3u8_urls(video, file_api)
            logging.info("[DEBUG] Candidate m3u8 URLs (%d total):", len(candidates))
            for i, c in enumerate(candidates[:10]):
                logging.info("[DEBUG]   [%d] %s", i, c)
            
            for candidate in candidates:
                try:
                    candidate_info = self._parser.parse(candidate)
                    if candidate_info.ts_urls:
                        m3u8_urls.append(candidate)
                        logging.info("[DEBUG] Found valid m3u8 candidate: %s (contains %d segments)", 
                                     candidate, len(candidate_info.ts_urls))
                except Exception as exc:
                    logging.debug("Failed to parse playlist %s: %s", candidate, exc)
                    continue
            
            if m3u8_urls:
                logging.info("[DEBUG] Found %d valid m3u8 playlists from candidates. Merging all in order.", len(m3u8_urls))
        
        if not m3u8_urls:
            raise ValueError("Unable to resolve m3u8 playlist for the provided video")
        
        logging.info("[DEBUG] Will download %d m3u8 playlist(s)", len(m3u8_urls))
        
        # Collect all TS URLs from all m3u8 playlists
        all_ts_urls: List[str] = []
        for url in m3u8_urls:
            try:
                info = self._parser.parse(url)
                if info.ts_urls:
                    logging.info("[DEBUG] Playlist %s has %d segments", url.split('/')[-2], len(info.ts_urls))
                    all_ts_urls.extend(info.ts_urls)
            except Exception as exc:
                logging.warning("Failed to parse m3u8 %s: %s", url, exc)
        
        if not all_ts_urls:
            raise ValueError("No TS segments found in any of the playlists")
        
        logging.info("[DEBUG] Total TS segments to download: %d", len(all_ts_urls))
        
        # Create a combined M3U8Info
        combined_info = M3U8Info(base_url=base_url or "", ts_urls=all_ts_urls)

        ensure_directory(os.path.dirname(os.path.abspath(output_file)) or ".")
        tmp_segment_dir = build_tmp_segment_dir(output_file)
        segment_plan = self._build_segment_plan(combined_info, tmp_segment_dir)
        ts_output = output_file if output_file.endswith(".ts") else f"{output_file}.ts"

        # Debug: Log segment plan summary
        logging.info("[DEBUG] Segment plan: %d segments, tmp_dir=%s", len(segment_plan), tmp_segment_dir)
        if segment_plan:
            logging.debug("[DEBUG] First 3 segments: %s", segment_plan[:3])
            logging.debug("[DEBUG] Last 3 segments: %s", segment_plan[-3:])

        try:
            asyncio.run(self._download_segments(segment_plan))
            # Cleanup async session to prevent "Unclosed client session" warnings
            asyncio.run(self._http_client._shutdown_cdn_session())
            # Debug: Verify downloaded segments
            self._verify_segments(segment_plan)
            self._merge_segments(segment_plan, ts_output)
            if ts_output != output_file:
                self._convert_ts_to_mp4(ts_output, output_file)
            
            # Validate duration if expected duration is provided
            if expected_duration and os.path.exists(output_file):
                self._validate_duration(output_file, expected_duration)
        except Exception:
            if os.path.exists(output_file):
                os.remove(output_file)
            if ts_output != output_file and os.path.exists(ts_output):
                os.remove(ts_output)
            raise
        else:
            if self.keep_ts:
                logging.info("[DEBUG] Keeping TS segments at: %s", tmp_segment_dir)
            else:
                cleanup_directory(tmp_segment_dir)
                parent_tmp = os.path.dirname(tmp_segment_dir)
                if os.path.isdir(parent_tmp) and not os.listdir(parent_tmp):
                    shutil.rmtree(parent_tmp, ignore_errors=True)

    def _resolve_base_url(self, video: VideoResource, file_api: FileAPI) -> str | None:
        """Resolve the base CDN URL for constructing m3u8 and info.plist paths."""
        location_path = video.location_path
        location = video.location
        
        # Try to get from file info if we have a file_id
        if video.file_id:
            try:
                info = file_api.get_file_info(video.file_id)
                location_path = info.get("locationPath") or location_path
                location = info.get("location") or location
            except Exception:
                pass
        
        location_path = (location_path or "").strip("/")
        location_value = (location or "").strip("/")
        
        if not location_path or not location_value:
            return None
        
        base = location_path
        if base.startswith("liveclass") and "plaso" not in base.split("/"):
            base = f"{base}/plaso"
        
        return f"https://filecdn.plaso.com/{base}/{location_value}"

    def _get_media_segments_from_plist(
        self, base_url: str | None, file_api: FileAPI, location: str | None
    ) -> List[dict]:
        """Fetch info.plist and parse the media array to get all m3u8 segments."""
        plist_url = None
        
        # Try to get signed URL first
        if location:
            plist_url = file_api.get_signed_plist_url(location)
            if plist_url:
                logging.debug("[DEBUG] Using signed plist URL: %s", plist_url[:100] + "...")
        
        # Fallback to unsigned URL (will likely fail with 403)
        if not plist_url and base_url:
            plist_url = f"{base_url}/info.plist"
            logging.debug("[DEBUG] Using unsigned plist URL: %s", plist_url)
        
        if not plist_url:
            return []
        
        try:
            text = self._http_client.fetch_cdn_text(plist_url)
            import json
            data = json.loads(text)
            media = data.get("media") or []
            
            # media format: [[start_ms, type, duration_ms, "a2/a.m3u8"], ...]
            segments = []
            for item in media:
                if isinstance(item, list) and len(item) >= 4:
                    segments.append({
                        "start_ms": item[0],
                        "type": item[1],
                        "duration_ms": item[2],
                        "path": item[3]
                    })
            
            # Sort by start time to ensure correct order
            segments.sort(key=lambda x: x["start_ms"])
            return segments
        except Exception as exc:
            logging.debug("Failed to fetch or parse info.plist: %s", exc)
            return []

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
            prefix = f"https://filecdn.plaso.com/{base}/{location_value}"
            for quality in ("a", "a0", "a1", "a2", "a3", "a4"):
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
            # Debug: Log each segment URL for the first few and last few
            if index < 3 or index >= len(info.ts_urls) - 3:
                logging.debug("[DEBUG] Segment #%05d: %s -> %s", index, url, os.path.basename(dest_path))
        logging.info("[DEBUG] Built segment plan with %d segments from m3u8", len(plan))
        return plan

    def _verify_segments(self, plan: SegmentPlan) -> None:
        """Verify all segments were downloaded and log size info."""
        missing = []
        empty = []
        sizes = []
        for index, url, dest_path in plan:
            if not os.path.exists(dest_path):
                missing.append(index)
            else:
                size = os.path.getsize(dest_path)
                sizes.append(size)
                if size == 0:
                    empty.append(index)
        
        total_size = sum(sizes)
        logging.info("[DEBUG] Segment verification: total=%d, missing=%d, empty=%d, total_size=%.2fMB",
                     len(plan), len(missing), len(empty), total_size / (1024 * 1024))
        
        if missing:
            logging.error("[DEBUG] Missing segments: %s", missing[:20])
        if empty:
            logging.warning("[DEBUG] Empty segments (0 bytes): %s", empty[:20])
        
        # Log size distribution
        if sizes:
            avg_size = sum(sizes) / len(sizes)
            min_size = min(sizes)
            max_size = max(sizes)
            logging.info("[DEBUG] Segment sizes: avg=%.1fKB, min=%.1fKB, max=%.1fKB",
                         avg_size / 1024, min_size / 1024, max_size / 1024)

    async def _download_segments(self, plan: SegmentPlan) -> None:
        if not plan:
            return

        total = len(plan)
        completed = [0]  # Use list for mutation in nested function
        
        async def download_with_progress(index: int, url: str, dest: str) -> None:
            await self._download_single(sem, index, url, dest)
            completed[0] += 1
            if completed[0] % 50 == 0 or completed[0] == total:
                logging.info("Progress: %d/%d segments (%.1f%%)", 
                           completed[0], total, 100.0 * completed[0] / total)

        sem = asyncio.Semaphore(self.workers)
        logging.info("Starting download of %d segments with %d workers...", total, self.workers)
        tasks = [download_with_progress(index, url, dest) for index, url, dest in plan]
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

        # Commands with error tolerance flags for handling corrupted segments
        commands = [
            [ffmpeg_bin, "-loglevel", "error", "-err_detect", "ignore_err", 
             "-fflags", "+discardcorrupt", "-y", "-i", ts_path, "-c", "copy", mp4_path],
            [ffmpeg_bin, "-loglevel", "error", "-err_detect", "ignore_err",
             "-fflags", "+discardcorrupt", "-y", "-i", ts_path, "-c:v", "copy", "-c:a", "aac", mp4_path],
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

    def _validate_duration(self, video_path: str, expected_duration: int) -> None:
        """Validate video duration matches expected duration."""
        ffprobe_bin = shutil.which("ffprobe")
        if not ffprobe_bin:
            logging.warning("ffprobe not found, skipping duration validation")
            return
        
        def format_hms(seconds: float) -> str:
            """Format seconds as HH:MM:SS."""
            h = int(seconds // 3600)
            m = int((seconds % 3600) // 60)
            s = int(seconds % 60)
            if h > 0:
                return f"{h}h{m:02d}m{s:02d}s"
            elif m > 0:
                return f"{m}m{s:02d}s"
            else:
                return f"{s}s"
        
        try:
            result = subprocess.run(
                [ffprobe_bin, "-v", "error", "-show_entries", "format=duration",
                 "-of", "default=noprint_wrappers=1:nokey=1", video_path],
                capture_output=True, text=True, check=True
            )
            actual_duration = float(result.stdout.strip())
            diff = actual_duration - expected_duration
            diff_pct = (diff / expected_duration) * 100 if expected_duration else 0
            
            actual_hms = format_hms(actual_duration)
            expected_hms = format_hms(expected_duration)
            diff_hms = format_hms(abs(diff))
            sign = "+" if diff >= 0 else "-"
            
            if abs(diff_pct) <= 1:  # Within 1%
                logging.info("✓ Duration OK: %s (%.0fs) | expected %s (%ds) | diff %s%s (%.1f%%)",
                           actual_hms, actual_duration, expected_hms, expected_duration, 
                           sign, diff_hms, diff_pct)
            elif abs(diff_pct) <= 5:  # Within 5%
                logging.warning("⚠ Duration slightly off: %s (%.0fs) | expected %s (%ds) | diff %s%s (%.1f%%)",
                              actual_hms, actual_duration, expected_hms, expected_duration,
                              sign, diff_hms, diff_pct)
            else:
                logging.error("✗ Duration mismatch: %s (%.0fs) | expected %s (%ds) | diff %s%s (%.1f%%)",
                            actual_hms, actual_duration, expected_hms, expected_duration,
                            sign, diff_hms, diff_pct)
        except subprocess.CalledProcessError as exc:
            logging.warning("ffprobe failed: %s", exc)
        except ValueError:
            logging.warning("Could not parse duration from ffprobe output")
