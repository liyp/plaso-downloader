# Agents Overview

plaso-downloader is organized around a few focused "agents" that cooperate to list courses, discover downloadable resources, and persist them to disk. This document summarizes each agent, what data it owns, and how they interact so you can extend or debug the workflow quickly.

## CLI Orchestrator

`plaso_downloader.main` acts as the command agent. It parses CLI/`.env` options, resolves authentication (reuse cached tokens or log in), and decides which operating mode to enter:

- **Preview** – list groups, packages, tasks, or files without touching the network-heavy download path.
- **Package download** – traverse group → package → day hierarchy, mirror directories, and call the downloader agents.
- **History mode** – call `/liveclassgo/api/v1/history/listRecord` for a date range and download each playback file directly into a history-specific manifest.
- **Report mode** – generate a detailed download status report comparing expected vs actual downloads.

It also maintains the per-package `.download_manifest.json` to skip already downloaded assets.

### CLI Options

| Option | Description |
|--------|-------------|
| `--download` | Actually download files (preview mode by default) |
| `--report` | Generate download status report |
| `--workers N` | Concurrent TS download workers (default: CPU cores × 4) |
| `--keep-ts` | Retain TS segment files after merge for debugging |
| `--history-from/--history-to` | Date range for history mode (YYYY-MM-DD) |

## API Agents

These wrappers live under `plaso_downloader/api` and encapsulate specific endpoints, headers, and throttling handled by `HttpClient`.

| Agent | Responsibility | Key Endpoints |
| ----- | -------------- | ------------- |
| `AuthAPI` | Exchange phone/password (plain or MD5) for an `access-token`. | `/custom/usr/doLogin` |
| `GroupAPI` | Enumerate course groups bound to the current account. | `/yxt/servlet/group/getGroupList` |
| `PackageAPI` | List packages (xFileId) under a group, with optional search/filtering. | `/yxt/servlet/pack/getPackageList` |
| `CourseAPI` | Fetch "Days" for a package via `getXfgTask`, tagging entries that are direct files vs. nested directories. | `/yxt/servlet/bigDir/getXfgTask` |
| `LessonAPI` | For a Day, gather videos/PDFs by calling `getAllContent` or by inlining file entries. | `/yxt/servlet/bigDir/getAllContent` |
| `FileAPI` | Retrieve extra metadata, get STS credentials for OSS access, and sign URLs. | `/yxt/servlet/file/getfileinfo`, `/yxt/servlet/stsHelper/stsInfo`, `/yxt/servlet/ali/getPlayInfo` |
| `HistoryAPI` | Page through archival recordings for history-download mode. | `/liveclassgo/api/v1/history/listRecord` |

All APIs reuse the same "real client" headers provided by `HttpClient`, which also handles rate limiting, retries, and CDN downloads.

## Downloader Agents

### VideoDownloader

- Accepts a `VideoResource`, resolves the best playlist URL based on storage type, parses m3u8 files, downloads TS segments concurrently (configurable workers), merges them, and remuxes to MP4 via `ffmpeg`.
- **Storage Type Detection**:
  - `liveclass` – Multi-segment recordings stored on OSS. Fetches `info.plist` using STS signed URLs to discover all m3u8 segments (a2, a3, a4...).
  - `ossvideo` – Transcoded videos. Calls `getPlayInfo` API to get direct m3u8 URLs with auth_key from `videocdn.plaso.cn`.
- **Multi-segment Support**: For liveclass recordings split across multiple files, parses `info.plist` media array to get all segment paths and downloads them in order.
- **Duration Validation**: After download, uses `ffprobe` to verify actual duration matches expected duration (displays ✓/⚠/✗ status).
- **Error Tolerance**: Uses `ffmpeg` with `-err_detect ignore_err -fflags +discardcorrupt` to handle minor corruption.
- Cleans up `tmp_ts/<video_name>/` directories once the MP4/TS is finalized (unless `--keep-ts`).

### PDFDownloader

- Fetches PDFs either via direct filecdn links or via page JPGs, assembles them with Pillow, and writes the final PDF alongside a transient `<pdf_name>_pages/` folder (cleaned afterward).

Both downloaders leverage the manifest so re-running the CLI skips already completed media.

## OSS Authentication Agent (STSCredentials)

Located in `FileAPI`, this handles Alibaba OSS authentication for accessing `liveclass` type videos:

1. Calls `/yxt/servlet/stsHelper/stsInfo` to get temporary STS credentials
2. Uses `oss2` SDK with `StsAuth` for signature generation
3. Generates signed URLs for `info.plist` access via accelerate domain (`file.plaso.com`)
4. Caches credentials until expiration

## History Recording Agent

History mode is a special orchestration path activated via `--history-from/--history-to`. It:

1. Calls `HistoryAPI` to list records.
2. Logs a preview with human-readable duration (e.g., `2h30m55s`).
3. If `--report` is specified, generates a detailed comparison report.
4. On `--download`, builds useful filenames combining timestamp, remote name, and record id.
5. Detects storage type (`liveclass` vs `ossvideo`) and uses appropriate download strategy.
6. Validates downloaded video duration against expected duration.
7. Stores its own manifest under `<history-output>/.download_manifest.json`.

## Report Agent

Activated via `--report` flag in history mode. Generates:

- **📈 总体统计** – Expected/downloaded/missing counts, completion rate, total duration
- **📚 分类统计** – Videos categorized by type (数量关系, 资料分析, 判断推理, 申论/真题, 其他)
- **❌ 缺失视频** – List of videos not yet downloaded
- **⚠️ 时长异常** – Videos with >5% duration discrepancy

## Supporting Utilities

- **File utils** – sanitize filenames, build directories (group, package, day), and manage temporary TS folders.
- **Manifest** – JSON store keyed by `file_id` or URL, ensuring idempotent downloads.
- **Token cache** – persists `access-token` between runs to avoid repeated login calls.
- **HTTP client** – supplies browser-like headers, throttles requests (60s timeout), and exposes both synchronous and asynchronous CDN download helpers (Requests + aiohttp with unlimited connections).
- **M3U8 Parser** – parses m3u8 playlists and extracts TS segment URLs.

## Typical Flow

1. User supplies credentials, group/package filters, and optional `--download`.
2. CLI agent resolves token, fetches group/package metadata, and filters tasks/day entries.
3. For each day:
   - `LessonAPI` returns structured `LessonResources`.
   - PDF/video agents build sanitized filenames (preferring remote names via `FileAPI`).
   - Manifest is checked and updated after each successful download.
4. If `--history-*` flags are set, the history agent short-circuits the package flow:
   - Fetches record list from API
   - For each record, checks `locationPath` to determine storage type
   - For `liveclass`: Gets STS credentials → Signs info.plist URL → Parses media array → Downloads all segments
   - For `ossvideo`: Calls getPlayInfo → Gets direct m3u8 URL → Downloads single stream
   - Validates duration and updates manifest

## Architecture Diagram

```
┌─────────────────┐     ┌─────────────────┐
│   CLI (main)    │────▶│   HistoryAPI    │
└────────┬────────┘     └─────────────────┘
         │
         ▼
┌─────────────────┐     ┌─────────────────┐
│  VideoDownloader│────▶│    FileAPI      │
└────────┬────────┘     └────────┬────────┘
         │                       │
         ▼                       ▼
┌─────────────────┐     ┌─────────────────┐
│  M3U8 Parser    │     │ STSCredentials  │
└────────┬────────┘     │ (oss2 SDK)      │
         │              └─────────────────┘
         ▼
┌─────────────────┐
│  Async Download │
│  (aiohttp)      │
└────────┬────────┘
         │
         ▼
┌─────────────────┐
│  ffmpeg merge   │
│  + duration     │
│    validation   │
└─────────────────┘
```

Use this map when adding new resource types, wiring additional APIs, or debugging downloads—the agents above encapsulate the responsibilities you'll likely extend.
