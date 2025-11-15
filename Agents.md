# Agents Overview

plaso-downloader is organized around a few focused “agents” that cooperate to list courses, discover downloadable resources, and persist them to disk. This document summarizes each agent, what data it owns, and how they interact so you can extend or debug the workflow quickly.

## CLI Orchestrator

`plaso_downloader.main` acts as the command agent. It parses CLI/`.env` options, resolves authentication (reuse cached tokens or log in), and decides which operating mode to enter:

- **Preview** – list groups, packages, tasks, or files without touching the network-heavy download path.
- **Package download** – traverse group → package → day hierarchy, mirror directories, and call the downloader agents.
- **History mode** – call `/liveclassgo/api/v1/history/listRecord` for a date range and download each playback file directly into a history-specific manifest.

It also maintains the per-package `.download_manifest.json` to skip already downloaded assets.

## API Agents

These wrappers live under `plaso_downloader/api` and encapsulate specific endpoints, headers, and throttling handled by `HttpClient`.

| Agent | Responsibility | Key Endpoints |
| ----- | -------------- | ------------- |
| `AuthAPI` | Exchange phone/password (plain or MD5) for an `access-token`. | `/custom/usr/doLogin` |
| `GroupAPI` | Enumerate course groups bound to the current account. | `/yxt/servlet/group/getGroupList` |
| `PackageAPI` | List packages (xFileId) under a group, with optional search/filtering. | `/yxt/servlet/pack/getPackageList` |
| `CourseAPI` | Fetch “Days” for a package via `getXfgTask`, tagging entries that are direct files vs. nested directories. | `/yxt/servlet/bigDir/getXfgTask` |
| `LessonAPI` | For a Day, gather videos/PDFs by calling `getAllContent` or by inlining file entries. | `/yxt/servlet/bigDir/getAllContent` |
| `FileAPI` | Retrieve extra metadata (PDF paths) and, for OSS video files (type 20), call `getPlayInfo` to get high/medium bitrate m3u8 URLs. | `/yxt/servlet/file/getfileinfo`, `/yxt/servlet/ali/getPlayInfo` |
| `HistoryAPI` | Page through archival recordings for history-download mode. | `/liveclassgo/api/v1/history/listRecord` |

All APIs reuse the same “real client” headers provided by `HttpClient`, which also handles rate limiting, retries, and CDN downloads.

## Downloader Agents

### VideoDownloader

- Accepts a `VideoResource`, resolves the best playlist URL (history/liveclass/OSS), parses m3u8 files, downloads TS segments concurrently (configurable workers), merges them, and optionally remuxes to MP4 via `ffmpeg`.
- For legacy liveclass recordings it tries `/a2/`, `/a1/`, `/a0/`, `/a/` CDNs. For `ossvideo`/type=20 assets it first calls `getPlayInfo` and prefers the HD URL.
- Cleans up `tmp_ts/<video_name>/` directories once the MP4/TS is finalized.

### PDFDownloader

- Fetches PDFs either via direct filecdn links or via page JPGs, assembles them with Pillow, and writes the final PDF alongside a transient `<pdf_name>_pages/` folder (cleaned afterward).

Both downloaders leverage the manifest so re-running the CLI skips already completed media.

## History Recording Agent

History mode is a special orchestration path activated via `--history-from/--history-to`. It:

1. Calls `HistoryAPI` to list records.
2. Logs a preview (record name, duration, id). Without `--download`, it exits after listing.
3. On download, builds useful filenames combining timestamp, remote name, and record id.
4. Uses the same `VideoDownloader` to fetch the stream (these records are standard liveclass files).
5. Stores its own manifest under `<history-output>/.download_manifest.json`, independent from course packages.

## Supporting Utilities

- **File utils** – sanitize filenames, build directories (group, package, day), and manage temporary TS folders.
- **Manifest** – JSON store keyed by `file_id` or URL, ensuring idempotent downloads.
- **Token cache** – persists `access-token` between runs to avoid repeated login calls.
- **HTTP client** – supplies browser-like headers, throttles requests, and exposes both synchronous and asynchronous CDN download helpers (Requests + aiohttp).

## Typical Flow

1. User supplies credentials, group/package filters, and optional `--download`.
2. CLI agent resolves token, fetches group/package metadata, and filters tasks/day entries.
3. For each day:
   - `LessonAPI` returns structured `LessonResources`.
   - PDF/video agents build sanitized filenames (preferring remote names via `FileAPI`).
   - Manifest is checked and updated after each successful download.
4. If `--history-*` flags are set, the history agent short-circuits the package flow and downloads archived recordings directly.

Use this map when adding new resource types, wiring additional APIs, or debugging downloads—the agents above encapsulate the responsibilities you’ll likely extend.
