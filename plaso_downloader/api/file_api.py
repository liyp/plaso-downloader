"""API client for fetching file metadata such as pdfLocation."""

from __future__ import annotations

import logging
import time
from typing import Optional

import oss2
from oss2 import StsAuth

from ..utils.http_client import HttpClient

FILE_INFO_PATH = "yxt/servlet/file/getfileinfo"
PLAY_INFO_PATH = "yxt/servlet/ali/getPlayInfo"
STS_INFO_PATH = "yxt/servlet/stsHelper/stsInfo"
LOCATION_PATH_INFO_PATH = "yxt/servlet/file/nc/getLocationPathInfo"


class STSCredentials:
    """Holds OSS STS temporary credentials."""
    
    def __init__(self, access_key_id: str, access_key_secret: str, security_token: str, 
                 expire: int, accelerate_domain: str, bucket: str, region: str, pre: str) -> None:
        self.access_key_id = access_key_id
        self.access_key_secret = access_key_secret
        self.security_token = security_token
        self.expire = expire
        self.accelerate_domain = accelerate_domain
        self.bucket = bucket
        self.region = region
        self.pre = pre
    
    def is_expired(self) -> bool:
        return time.time() >= self.expire - 60  # 1 minute buffer
    
    def sign_url(self, object_key: str, expires_in: int = 3600) -> str:
        """Generate a signed URL for OSS object access using official oss2 SDK."""
        logging.info("[DEBUG] sign_url using oss2 SDK:")
        logging.info("[DEBUG]   bucket: %s", self.bucket)
        logging.info("[DEBUG]   object_key: %s", object_key)
        logging.info("[DEBUG]   region: %s", self.region)
        logging.info("[DEBUG]   accelerate_domain: %s", self.accelerate_domain)
        logging.info("[DEBUG]   expires_in: %s", expires_in)
        
        # Create STS auth
        auth = StsAuth(
            self.access_key_id,
            self.access_key_secret,
            self.security_token
        )
        
        # Use accelerate domain as endpoint (CNAME - custom domain bound to bucket)
        # Format: https://accelerate_domain (e.g., https://file.plaso.com)
        endpoint = f"https://{self.accelerate_domain}"
        
        # Create bucket object with is_cname=True to use custom domain
        # This prevents SDK from prepending bucket name to the domain
        bucket = oss2.Bucket(auth, endpoint, self.bucket, is_cname=True)
        
        # Generate signed URL
        signed_url = bucket.sign_url('GET', object_key, expires_in)
        
        logging.info("[DEBUG]   signed_url: %s", signed_url[:150] + "..." if len(signed_url) > 150 else signed_url)
        
        return signed_url


class FileAPI:
    """Retrieves extra metadata for files (PDF pages, etc.)."""

    def __init__(self, http_client: HttpClient) -> None:
        self._client = http_client
        self._sts_cache: dict[str, STSCredentials] = {}

    def get_file_info(self, file_id: str) -> dict:
        payload = {"fileId": file_id, "checkResource": True}
        try:
            data = self._client.request_api(FILE_INFO_PATH, payload)
            return data.get("obj") or data.get("file") or {}
        except Exception as exc:  # pragma: no cover - network errors
            logging.error("Failed to fetch file info for %s: %s", file_id, exc)
            raise

    def get_play_info(self, record_id: str, file_id: str) -> dict:
        """Get play info for ossvideo type videos.
        
        Args:
            record_id: The record/location ID (e.g., from location field)
            file_id: The file ID
        """
        payload = {"id": record_id, "fileId": file_id}
        logging.info("[DEBUG] get_play_info called with id=%s, fileId=%s", record_id, file_id)
        try:
            data = self._client.request_api(PLAY_INFO_PATH, payload)
            return data.get("obj") or {}
        except Exception as exc:  # pragma: no cover - network errors
            logging.error("Failed to fetch play info for %s: %s", file_id, exc)
            raise

    def get_sts_credentials(self, location_id: str = "liveclass") -> Optional[STSCredentials]:
        """Get STS credentials for accessing OSS resources."""
        logging.info("[DEBUG] get_sts_credentials called with location_id=%s", location_id)
        
        # Check cache first
        if location_id in self._sts_cache:
            cached = self._sts_cache[location_id]
            if not cached.is_expired():
                logging.info("[DEBUG] Using cached STS credentials (expires at %s)", cached.expire)
                return cached
            else:
                logging.info("[DEBUG] Cached STS credentials expired")
        
        payload = {"id": location_id}
        try:
            logging.info("[DEBUG] Calling STS API: %s with payload=%s", STS_INFO_PATH, payload)
            data = self._client.request_api(STS_INFO_PATH, payload)
            logging.info("[DEBUG] STS API response code=%s", data.get("code"))
            obj = data.get("obj") or data
            
            logging.info("[DEBUG] STS response fields: id=%s, secret=%s..., token=%s..., expire=%s, region=%s, pre=%s, accelerateDomain=%s",
                        obj.get("id", "")[:20] if obj.get("id") else "None",
                        obj.get("secret", "")[:10] + "..." if obj.get("secret") else "None",
                        obj.get("token", "")[:20] + "..." if obj.get("token") else "None",
                        obj.get("expire"),
                        obj.get("region"),
                        obj.get("pre"),
                        obj.get("accelerateDomain"))
            
            # Use API's accelerateDomain as-is
            accelerate_domain = obj.get("accelerateDomain", "file.plaso.cn")
            
            # Fix region format: API returns "oss-cn-hangzhou" but we need "cn-hangzhou" for some uses
            raw_region = obj.get("region", "oss-cn-hangzhou")
            # Keep full region for oss2 SDK (it expects oss-cn-hangzhou format)
            region = raw_region
            logging.info("[DEBUG] Using region: %s", region)
            
            creds = STSCredentials(
                access_key_id=obj.get("id", ""),
                access_key_secret=obj.get("secret", ""),
                security_token=obj.get("token", ""),
                expire=obj.get("expire", 0),
                accelerate_domain=accelerate_domain,
                bucket=obj.get("bucket", "file-plaso"),
                region=region,
                pre=obj.get("pre", "liveclass/plaso"),
            )
            
            if creds.access_key_id and creds.security_token:
                self._sts_cache[location_id] = creds
                logging.info("[DEBUG] STS credentials obtained successfully, expires at %s", creds.expire)
                return creds
            
            logging.warning("STS response missing required fields (id or token)")
            return None
        except Exception as exc:
            logging.warning("Failed to get STS credentials for %s: %s", location_id, exc)
            return None

    def get_signed_plist_url(self, location: str, location_path: str = "liveclass") -> Optional[str]:
        """Get a signed URL for accessing info.plist."""
        creds = self.get_sts_credentials(location_path)
        if not creds:
            return None
        
        # Build object key: pre/location/info.plist
        # e.g., liveclass/plaso/18008/18958766_1751109804879a3_kg2/info.plist
        object_key = f"{creds.pre}/{location.strip('/')}/info.plist"
        
        return creds.sign_url(object_key)
