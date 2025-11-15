from __future__ import annotations

import argparse
import logging
import os
from datetime import datetime, timezone

from dotenv import load_dotenv

from .api.auth_api import AuthAPI
from .api.course_api import CourseAPI
from .api.file_api import FileAPI
from .api.group_api import GroupAPI
from .api.history_api import HistoryAPI
from .api.lesson_api import LessonAPI
from .api.package_api import PackageAPI
from .downloader.pdf_downloader import PDFDownloader
from .downloader.video_downloader import VideoDownloader
from .models import CoursePackageInfo, DayEntry, GroupInfo, VideoResource
from .utils.file_utils import (
    build_day_directory,
    build_package_directory,
    ensure_directory,
    sanitize_filename,
)
from .utils.manifest import DownloadManifest
from .utils.http_client import AuthenticationError, HttpClient
from .utils.token_cache import (
    DEFAULT_CACHE_PATH,
    clear_cached_token,
    load_cached_token,
    save_cached_token,
)

load_dotenv()


def _env_str(name: str) -> str | None:
    value = os.getenv(name)
    if value is None or value == "":
        return None
    return value


def _env_int(name: str) -> int | None:
    value = _env_str(name)
    if value is None:
        return None
    try:
        return int(value)
    except ValueError:
        return None


def _env_bool(name: str) -> bool:
    value = _env_str(name)
    if value is None:
        return False
    return value.strip().lower() in {"1", "true", "yes", "on"}


def _env_list(name: str) -> list[str] | None:
    raw = _env_str(name)
    if not raw:
        return None
    items = [item.strip() for item in raw.split(",") if item.strip()]
    return items or None


def _csv_arg(value: str) -> list[str]:
    return [item.strip() for item in value.split(",") if item.strip()]


def _filter_days_by_ids(days: list[DayEntry], task_ids: list[str] | None) -> list[DayEntry]:
    if not task_ids:
        return days
    allowed = {task_id for task_id in task_ids}
    filtered = [day for day in days if day.id in allowed]
    if not filtered:
        logging.warning("Task filter removed all lessons; check provided --task-ids values.")
    return filtered


def _parse_history_range(args: argparse.Namespace) -> tuple[int, int] | None:
    if not args.history_from or not args.history_to:
        return None
    try:
        start_dt = datetime.fromisoformat(args.history_from)
        end_dt = datetime.fromisoformat(args.history_to)
    except ValueError:
        logging.error("Invalid history date format. Use YYYY-MM-DD")
        return None
    start_ts = int(start_dt.replace(tzinfo=timezone.utc).timestamp() * 1000)
    end_ts = int(end_dt.replace(tzinfo=timezone.utc).timestamp() * 1000)
    return start_ts, end_ts


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Download plaso course videos and PDFs.")
    parser.add_argument("--access-token", default=_env_str("ACCESS_TOKEN"), help="Existing access token captured from the plaso client")
    parser.add_argument("--login-phone", default=_env_str("LOGIN_PHONE"), help="Phone number / login account for automatic login")
    parser.add_argument("--login-password", default=_env_str("LOGIN_PASSWORD"), help="Password (plain text by default) for automatic login")
    parser.add_argument(
        "--login-password-md5",
        action="store_true",
        default=_env_bool("LOGIN_PASSWORD_MD5"),
        help="Treat --login-password as an already hashed MD5 string",
    )
    parser.add_argument("--group-id", type=int, default=_env_int("GROUP_ID"), help="Group id from the course payload")
    parser.add_argument("--course-id", default=_env_str("COURSE_ID"), help="Directory id (legacy manual mode)")
    parser.add_argument("--xfile-id", default=_env_str("XFILE_ID"), help="xFileId (legacy manual mode)")
    parser.add_argument("--package-id", default=_env_str("PACKAGE_ID"), help="Package xFileId to download under the group")
    parser.add_argument("--package-search", default=_env_str("PACKAGE_SEARCH"), help="Filter packages by keyword when listing")
    parser.add_argument("--all-packages", action="store_true", default=_env_bool("ALL_PACKAGES"), help="Download every package in the group")
    parser.add_argument("--package-limit", type=int, default=_env_int("PACKAGE_LIMIT"), help="Optional cap on number of packages to download")
    parser.add_argument("--list-groups", action="store_true", default=_env_bool("LIST_GROUPS"), help="List available groups and exit")
    parser.add_argument("--list-packages", action="store_true", default=_env_bool("LIST_PACKAGES"), help="List packages for the given group and exit")
    parser.add_argument(
        "--list-tasks",
        action="store_true",
        default=_env_bool("LIST_TASKS"),
        help="List Day/Task entries for the selected packages",
    )
    parser.add_argument(
        "--list-files",
        action="store_true",
        default=_env_bool("LIST_FILES"),
        help="Within each selected task, list contained file entries",
    )
    parser.add_argument("--history-from", default=_env_str("HISTORY_FROM"), help="History mode start date (YYYY-MM-DD)")
    parser.add_argument("--history-to", default=_env_str("HISTORY_TO"), help="History mode end date (YYYY-MM-DD)")
    parser.add_argument("--history-output", default=_env_str("HISTORY_OUTPUT"), help="Output directory for history downloads")
    parser.add_argument(
        "--task-ids",
        type=_csv_arg,
        default=_env_list("TASK_IDS"),
        help="Comma-separated Day IDs to download (filters tasks).",
    )
    parser.add_argument(
        "--download",
        action="store_true",
        default=_env_bool("DOWNLOAD"),
        help="Perform actual downloads instead of preview-only output",
    )
    parser.add_argument("--output-dir", default=_env_str("OUTPUT_DIR") or "downloads", help="Directory to store downloaded assets")
    parser.add_argument("--workers", type=int, default=_env_int("WORKERS") or 1, help="Number of concurrent TS download workers")
    parser.add_argument(
        "--max-tasks",
        type=int,
        default=_env_int("MAX_TASKS"),
        help="Optional limit for how many Day lessons to process",
    )
    token_cache_env = _env_str("TOKEN_CACHE")
    parser.add_argument(
        "--token-cache",
        default=os.path.expanduser(token_cache_env) if token_cache_env else DEFAULT_CACHE_PATH,
        help="File to persist access-token between runs",
    )
    return parser.parse_args()


def configure_logging() -> None:
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s [%(levelname)s] %(message)s",
    )


def resolve_access_token(args: argparse.Namespace) -> str | None:
    if args.access_token:
        return args.access_token

    cached_token = load_cached_token(args.token_cache)
    if cached_token:
        return cached_token

    if args.login_phone and args.login_password:
        logging.info("Attempting login for %s", args.login_phone)
        with HttpClient(access_token="previewToken") as auth_client:
            auth_api = AuthAPI(auth_client)
            try:
                login_result = auth_api.login(
                    phone=args.login_phone,
                    password=args.login_password,
                    password_is_md5=args.login_password_md5,
                )
            except Exception as exc:
                logging.error("Login failed: %s", exc)
                return None
        save_cached_token(args.token_cache, login_result.access_token)
        return login_result.access_token

    logging.error("Either --access-token, cached token, or --login-phone/--login-password must be provided.")
    return None


def print_groups(groups: list[GroupInfo]) -> None:
    if not groups:
        logging.info("No groups available for this account.")
        return
    logging.info("%-10s | %-40s", "Group ID", "Name")
    logging.info("%s", "-" * 60)
    for group in groups:
        logging.info("%-10s | %s", group.id, group.name)


def print_packages(packages: list[CoursePackageInfo]) -> None:
    if not packages:
        logging.info("No packages found for this group.")
        return
    logging.info("%-36s | %-5s | %s", "xFileId", "Tasks", "Title")
    logging.info("%s", "-" * 80)
    for pkg in packages:
        logging.info("%-36s | %-5s | %s", pkg.xfile_id, pkg.task_num, pkg.title)


def select_packages(
    args: argparse.Namespace,
    package_api: PackageAPI,
    group_id: int,
) -> list[CoursePackageInfo]:
    packages = package_api.get_packages(group_id, search=args.package_search or "")

    def matches(pkg: CoursePackageInfo) -> bool:
        if args.package_id and pkg.xfile_id == args.package_id:
            return True
        if args.package_id and pkg.dir_id == args.package_id:
            return True
        if args.course_id and args.xfile_id:
            return pkg.dir_id == args.course_id and pkg.xfile_id == args.xfile_id
        if args.package_search:
            return args.package_search.lower() in pkg.title.lower()
        return False

    if args.package_id or (args.course_id and args.xfile_id) or args.package_search:
        filtered = [pkg for pkg in packages if matches(pkg)]
    elif args.all_packages:
        filtered = packages
    else:
        filtered = []

    if args.package_limit is not None:
        filtered = filtered[: args.package_limit]

    return filtered


def _list_task_files(lesson_api: LessonAPI, day: DayEntry, group_id: int, xfile_id: str) -> None:
    try:
        entries = lesson_api.list_files(day, group_id, xfile_id)
    except Exception as exc:
        logging.error("Failed to list files for day %s: %s", day.id, exc)
        return
    if not entries:
        logging.info("    (no files)")
        return
    for entry in entries:
        file_type = entry.get("type")
        file_name = entry.get("name")
        file_id = entry.get("_id") or entry.get("myid")
        duration = entry.get("duration")
        logging.info("    - type=%s id=%s name=%s duration=%s", file_type, file_id, file_name, duration)


def _resolve_remote_name(file_api: FileAPI, file_id: str | None, fallback: str) -> str:
    if not file_id:
        return fallback
    try:
        info = file_api.get_file_info(file_id)
        remote_name = info.get("name")
        if remote_name:
            return remote_name
    except Exception:
        pass
    return fallback


def _build_video_filename(day_dir: str, video: VideoResource, index: int, file_api: FileAPI) -> str:
    raw_name = _resolve_remote_name(file_api, video.file_id, video.name)
    safe_name = sanitize_filename(raw_name, default=f"video_{index}")
    if not safe_name.lower().endswith(".mp4"):
        safe_name = f"{safe_name}.mp4"
    return os.path.join(day_dir, safe_name)


def _build_pdf_filename(day_dir: str, pdf: PDFResource, index: int, file_api: FileAPI) -> str:
    raw_name = _resolve_remote_name(file_api, pdf.file_id, pdf.name)
    safe_name = sanitize_filename(raw_name, default=f"pdf_{index}.pdf")
    if not safe_name.lower().endswith(".pdf"):
        safe_name = f"{safe_name}.pdf"
    return os.path.join(day_dir, safe_name)


def _resource_key(file_id: str | None, fallback: str) -> str:
    return file_id or fallback


def _history_display_name(record: dict) -> str:
    file_common = record.get("fileCommon") or {}
    return (
        file_common.get("name")
        or record.get("shortDesc")
        or record.get("longDesc")
        or record.get("_id")
        or "history_record"
    )


def _history_record_to_video(record: dict) -> VideoResource | None:
    file_common = record.get("fileCommon") or {}
    file_id = file_common.get("_id") or record.get("fileId")
    location_path = file_common.get("locationPath") or record.get("locationPath")
    location = file_common.get("location") or record.get("location")
    m3u8_url = record.get("playUrl") or record.get("m3u8Url") or ""
    if not (file_id or (location_path and location) or m3u8_url):
        return None
    name = _history_display_name(record)
    return VideoResource(
        name=name,
        m3u8_url=m3u8_url,
        location_path=location_path,
        location=location,
        file_id=file_id,
    )


def _format_history_timestamp(timestamp_ms: int | None) -> str | None:
    if not timestamp_ms:
        return None
    dt = datetime.fromtimestamp(timestamp_ms / 1000, tz=timezone.utc)
    return dt.strftime("%Y%m%d_%H%M")


def _history_record_label(record: dict) -> str:
    timestamp_prefix = _format_history_timestamp(record.get("createTime"))
    record_id = record.get("_id")
    title = _history_display_name(record)
    parts = [part for part in (timestamp_prefix, title) if part]
    label = " ".join(parts) if parts else title
    if record_id:
        label = f"{label} ({record_id})"
    return label


def _build_history_filename(
    output_dir: str,
    record: dict,
    video: VideoResource,
    index: int,
    file_api: FileAPI,
) -> str:
    remote_name = _resolve_remote_name(file_api, video.file_id, video.name)
    stem, _ = os.path.splitext(remote_name)
    timestamp_prefix = _format_history_timestamp(record.get("createTime"))
    record_id = record.get("_id")
    pieces = [piece for piece in (timestamp_prefix, stem, record_id) if piece]
    raw_name = " ".join(pieces) if pieces else f"history_{index}"
    safe_name = sanitize_filename(raw_name, default=f"history_{index}")
    if not safe_name.lower().endswith(".mp4"):
        safe_name = f"{safe_name}.mp4"
    return os.path.join(output_dir, safe_name)


def main() -> None:
    args = parse_args()
    configure_logging()

    access_token = resolve_access_token(args)
    if not access_token:
        return

    with HttpClient(access_token=access_token) as http_client:
        file_api = FileAPI(http_client)

        history_range = _parse_history_range(args)
        if history_range:
            history_api = HistoryAPI(http_client)
            video_downloader = VideoDownloader(http_client, workers=args.workers)
            start_ts, end_ts = history_range
            logging.info(
                "Fetching history recordings from %s to %s",
                args.history_from,
                args.history_to,
            )
            try:
                records = history_api.list_records(start_ts, end_ts)
            except AuthenticationError as exc:
                clear_cached_token(args.token_cache)
                logging.error("%s", exc)
                return
            except Exception as exc:
                logging.error("Failed to fetch history recordings: %s", exc)
                return

            history_output = args.history_output or os.path.join(args.output_dir, "history_records")
            ensure_directory(history_output)
            manifest = DownloadManifest(os.path.join(history_output, ".download_manifest.json"))

            if not records:
                logging.info("No history recordings found for the given date range.")
                return

            logging.info("Found %s history recordings.", len(records))
            for record in records:
                logging.info(
                    "  - %s | duration=%ss",
                    _history_record_label(record),
                    record.get("duration"),
                )

            if not args.download:
                logging.info("Preview complete. Re-run with --download to fetch the recordings.")
                return

            for index, record in enumerate(records, start=1):
                video = _history_record_to_video(record)
                if not video:
                    logging.warning(
                        "Skipping history record %s: missing file metadata",
                        record.get("_id") or index,
                    )
                    continue
                video_filename = _build_history_filename(history_output, record, video, index, file_api)
                file_key = _resource_key(video.file_id, record.get("_id") or video.m3u8_url or video.name)
                if manifest.is_downloaded(file_key, video_filename):
                    logging.info("Skipping %s (already downloaded)", _history_record_label(record))
                    continue
                logging.info("Downloading %s ...", _history_record_label(record))
                try:
                    video_downloader.download(video, video_filename, file_api)
                    manifest.mark_downloaded(file_key, video_filename)
                    logging.info("Done %s", os.path.basename(video_filename))
                except Exception as exc:
                    logging.error("History record %s failed: %s", _history_record_label(record), exc)
            return

        ensure_directory(args.output_dir)

        group_api = GroupAPI(http_client)
        package_api = PackageAPI(http_client)
        course_api = CourseAPI(http_client)
        lesson_api = LessonAPI(http_client)

        if args.list_groups:
            try:
                print_groups(group_api.get_groups())
            except AuthenticationError as exc:
                clear_cached_token(args.token_cache)
                logging.error("%s", exc)
            return

        if not args.group_id:
            logging.error("--group-id is required unless --list-groups is specified")
            return

        if args.list_packages:
            try:
                packages = package_api.get_packages(args.group_id, search=args.package_search or "")
            except AuthenticationError as exc:
                clear_cached_token(args.token_cache)
                logging.error("%s", exc)
                return
            print_packages(packages)
            return

        try:
            packages = select_packages(args, package_api, args.group_id)
        except AuthenticationError as exc:
            clear_cached_token(args.token_cache)
            logging.error("%s", exc)
            return

        manual_package = None
        if not packages and args.course_id and args.xfile_id:
            manual_package = CoursePackageInfo(
                id=args.xfile_id,
                title=f"Package_{args.course_id}",
                group_id=args.group_id,
                xfile_id=args.xfile_id,
                dir_id=args.course_id,
                task_num=0,
            )
            packages = [manual_package]

        if not packages:
            logging.error(
                "No packages selected. Use --package-id, --package-search, or --all-packages (or legacy --course-id/--xfile-id)."
            )
            return

        if args.list_tasks or args.list_files:
            for package in packages:
                try:
                    days = course_api.get_days(package.dir_id, package.group_id, package.xfile_id)
                except AuthenticationError as exc:
                    clear_cached_token(args.token_cache)
                    logging.error("%s", exc)
                    return
                except Exception as exc:
                    logging.error("Failed to fetch tasks for %s: %s", package.title, exc)
                    continue
                days = _filter_days_by_ids(days, args.task_ids)
                logging.info("Tasks for package %s (%s)", package.title, package.xfile_id)
                for idx, day in enumerate(days, start=1):
                    logging.info("  Day%02d %s (%s)", idx, day.name, day.id)
                    if args.list_files:
                        _list_task_files(lesson_api, day, package.group_id, package.xfile_id)
            if not args.list_tasks and not args.download:
                return
            if args.list_tasks and not args.download:
                return

        if not args.download:
            logging.info("Preview mode: matching packages listed below. Use --download to fetch resources.")
            print_packages(packages)
            return

        group_info = None
        try:
            for group in group_api.get_groups():
                if group.id == args.group_id:
                    group_info = group
                    break
        except AuthenticationError as exc:
            clear_cached_token(args.token_cache)
            logging.error("%s", exc)
            return
        except Exception:
            logging.warning("Unable to fetch group metadata; proceeding without name.")

        group_name = group_info.name if group_info else f"group_{args.group_id}"

        video_downloader = VideoDownloader(http_client, workers=args.workers)
        pdf_downloader = PDFDownloader(http_client)

        for package in packages:
            logging.info("Processing package %s (xFileId=%s)", package.title, package.xfile_id)
            package_dir = build_package_directory(args.output_dir, group_name, package.title)
            manifest = DownloadManifest(os.path.join(package_dir, ".download_manifest.json"))

            try:
                days = course_api.get_days(package.dir_id, package.group_id, package.xfile_id)
            except AuthenticationError as exc:
                clear_cached_token(args.token_cache)
                logging.error("%s", exc)
                return
            except Exception as exc:
                logging.error("Failed to fetch course structure for %s: %s", package.title, exc)
                continue

            days = _filter_days_by_ids(days, args.task_ids)

            if args.max_tasks:
                days = days[: args.max_tasks]

            if not days:
                logging.warning("Package %s has no lessons to download", package.title)
                continue

            for index, day in enumerate(days, start=1):
                day_label = f"[{package.title} - Day{index}]"
                day_dir = build_day_directory(package_dir, day.name)

                logging.info("%s Processing %s", day_label, sanitize_filename(day.name))

                try:
                    lesson = lesson_api.get_lesson_resources(day, package.group_id, package.xfile_id)
                except AuthenticationError as exc:
                    clear_cached_token(args.token_cache)
                    logging.error("%s %s", day_label, exc)
                    return
                except Exception as exc:
                    logging.error("%s Failed to fetch lesson: %s", day_label, exc)
                    continue

                if args.list_files and not args.download:
                    _list_task_files(lesson_api, day, package.group_id, package.xfile_id)
                    continue

                logging.info(
                    "%s Found %s video, %s pdf.",
                    day_label,
                    len(lesson.videos),
                    len(lesson.pdfs),
                )

                for video_index, video in enumerate(lesson.videos, start=1):
                    video_filename = _build_video_filename(day_dir, video, video_index, file_api)
                    video_key = _resource_key(video.file_id, video.m3u8_url)
                    if manifest.is_downloaded(video_key, video_filename):
                        logging.info("%s Skipping video_%s (already downloaded)", day_label, video_index)
                        continue
                    logging.info("%s Downloading video_%s ...", day_label, video_index)
                    try:
                        video_downloader.download(video, video_filename, file_api)
                        logging.info("%s Done %s", day_label, os.path.basename(video_filename))
                        manifest.mark_downloaded(video_key, video_filename)
                    except Exception as exc:
                        logging.error("%s Video %s failed: %s", day_label, video_index, exc)

                for pdf_index, pdf in enumerate(lesson.pdfs, start=1):
                    pdf_filename = _build_pdf_filename(day_dir, pdf, pdf_index, file_api)
                    pdf_key = _resource_key(pdf.file_id, pdf.download_url)
                    if manifest.is_downloaded(pdf_key, pdf_filename):
                        logging.info("%s Skipping pdf_%s (already downloaded)", day_label, pdf_index)
                        continue
                    logging.info("%s Downloading pdf_%s ...", day_label, pdf_index)
                    try:
                        pdf_downloader.download(pdf, pdf_filename, file_api)
                        logging.info("%s Done %s", day_label, os.path.basename(pdf_filename))
                        manifest.mark_downloaded(pdf_key, pdf_filename)
                    except Exception as exc:
                        logging.error("%s PDF %s failed: %s", day_label, pdf_index, exc)


if __name__ == "__main__":
    main()
