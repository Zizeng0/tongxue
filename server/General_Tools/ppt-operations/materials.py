"""Search and download reusable presentation images from public catalogs."""

import asyncio
import logging
import re
from dataclasses import dataclass
from html import unescape
from io import BytesIO
from itertools import zip_longest
from pathlib import Path
from uuid import UUID

import httpx
from PIL import Image, ImageOps
from starlette.concurrency import run_in_threadpool

from . import CONSTANT


SEARCH_URL = "https://api.openverse.org/v1/images/"
COMMONS_URL = "https://commons.wikimedia.org/w/api.php"
NASA_SEARCH_URL = "https://images-api.nasa.gov/search"
NASA_MEDIA_GUIDELINES = "https://www.nasa.gov/nasa-brand-center/images-and-media/"
NASA_ID = re.compile(r"[A-Za-z0-9][A-Za-z0-9_-]{0,99}")
MAX_BYTES = 8 * 1024 * 1024
MAX_ONLINE_IMAGES = 8
logger = logging.getLogger("uvicorn.error")
LICENSES = {"by", "cc0", "pdm"}
IMAGE_HOSTS = {
    "upload.wikimedia.org", "live.staticflickr.com", "images-assets.nasa.gov",
    "images.metmuseum.org", "cdn.pixabay.com", "www.rawpixel.com", "images.rawpixel.com",
    "cdn2.picryl.com", "cdn.picryl.com", "digitalcollections.nypl.org",
}


@dataclass(frozen=True)
class Material:
    path: Path
    title: str
    creator: str
    source: str
    license_url: str
    query: str = ""
    subject: str = ""

    @property
    def credit(self) -> str:
        rights = "使用指引" if self.license_url == NASA_MEDIA_GUIDELINES else "许可证"
        return (f"图片：《{self.title}》 · {self.creator or '作者未标注'}\n来源：{self.source}\n"
                f"{rights}：{self.license_url}\n图片按版面缩放或裁剪。")


def public_link(value) -> str:
    if not isinstance(value, str):
        return ""
    url = httpx.URL(value)
    if url.scheme in {"http", "https"} and url.host and not url.userinfo:
        return str(url)
    return ""


def commons_candidate(page: dict) -> dict | None:
    if not isinstance(page, dict) or not isinstance(page.get("pageid"), int) or not page.get("imageinfo"):
        return None
    image = page["imageinfo"][0]
    metadata = image.get("extmetadata", {})
    license_name = metadata.get("LicenseShortName", {}).get("value", "").casefold()
    license_url = public_link(metadata.get("LicenseUrl", {}).get("value"))
    if not license_url:
        return None
    path = httpx.URL(license_url).path
    if license_name.startswith("cc by ") and path.startswith("/licenses/by/"):
        license_type = "by"
    elif path.startswith("/publicdomain/zero/"):
        license_type = "cc0"
    elif path.startswith("/publicdomain/mark/"):
        license_type = "pdm"
    else:
        return None
    return {"id": f"commons-{page['pageid']}", "title": page.get("title", "").removeprefix("File:"),
            "creator": unescape(re.sub("<[^>]*>", "", metadata.get("Artist", {}).get("value", ""))).strip(),
            "foreign_landing_url": image.get("descriptionurl"), "license": license_type, "license_url": license_url,
            "url": image.get("thumburl") or image.get("url")}


async def download_image(client: httpx.AsyncClient, url: str) -> bytes:
    async with client.stream("GET", url) as response:
        response.raise_for_status()
        if not response.headers.get("content-type", "").startswith("image/"):
            raise ValueError(CONSTANT.IMAGE_CONTENT_REQUIRED)
        content = bytearray()
        async for chunk in response.aiter_bytes():
            content.extend(chunk)
            if len(content) > MAX_BYTES:
                raise ValueError(CONSTANT.IMAGE_SIZE_EXCEEDED)
        return bytes(content)


def save_image(content: bytes, path: Path) -> None:
    with Image.open(BytesIO(content)) as source:
        if source.format not in {"JPEG", "PNG", "WEBP", "GIF"} or source.width * source.height > 24_000_000:
            raise ValueError(CONSTANT.IMAGE_FORMAT_UNSUPPORTED)
        image = ImageOps.exif_transpose(source)
        image.thumbnail((1600, 1600))
        encoded = BytesIO()
        image.convert("RGB").save(encoded, format="JPEG", quality=87)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(encoded.getvalue())


def nasa_candidate(item: dict, query: str) -> dict | None:
    if not isinstance(item, dict) or not item.get("data"):
        return None
    data = item["data"][0]
    if not isinstance(data, dict) or data.get("media_type") != "image":
        return None
    identifier, center, title = (str(data.get(field) or "") for field in ("nasa_id", "center", "title"))
    if not NASA_ID.fullmatch(identifier) or not re.fullmatch(r"[A-Z]{2,8}", center):
        return None
    if not set(re.findall(r"[a-z0-9]{2,}", query.lower())) <= set(re.findall(r"[a-z0-9]{2,}", title.lower())):
        return None
    credit = " ".join(str(data.get(field) or "") for field in
                      ("description", "description_508", "photographer", "secondary_creator"))
    if data.get("secondary_creator") or re.search(
        r"©|copyright|all rights reserved|courtesy of|\bcredit[:：]|\b(?:ESA|JAXA|SpaceX|Getty|Reuters)\b", credit, re.I
    ):
        return None
    urls = [link.get("href") for link in item.get("links", []) if isinstance(link, dict) and link.get("render") == "image"]
    url = next((url for size in ("medium", "small", "thumb") for url in urls
                if url == f"https://images-assets.nasa.gov/image/{identifier}/{identifier}~{size}.jpg"), None)
    if not url:
        return None
    return {"id": f"nasa-{identifier}", "title": title, "creator": f"NASA / {center}",
            "foreign_landing_url": f"https://images.nasa.gov/details/{identifier}", "license": "nasa",
            "license_url": NASA_MEDIA_GUIDELINES, "url": url}


async def fetch_result(client, item, target: Path, query: str = "") -> Material | None:
    if not isinstance(item, dict):
        return None
    try:
        license_type = item["license"]
        nasa = license_type == "nasa"
        if not nasa and license_type not in LICENSES:
            return None
        catalog_id = item["id"]
        commons = re.fullmatch(r"commons-\d+", catalog_id)
        identifier = catalog_id if commons or nasa else UUID(catalog_id)
        source = public_link(item.get("foreign_landing_url"))
        license_url = public_link(item.get("license_url"))
        creator = str(item.get("creator") or "")[:160]
        origin = httpx.URL(item.get("url") or "")
        if nasa:
            asset_id = catalog_id.removeprefix("nasa-")
            if (not catalog_id.startswith("nasa-") or not NASA_ID.fullmatch(asset_id)
                    or source != f"https://images.nasa.gov/details/{asset_id}"
                    or license_url != NASA_MEDIA_GUIDELINES or origin.host != "images-assets.nasa.gov"):
                return None
        else:
            license_link = httpx.URL(license_url)
            license_path = {"by": "/licenses/by/", "cc0": "/publicdomain/zero/", "pdm": "/publicdomain/mark/"}[license_type]
            if (not source or license_link.host != "creativecommons.org" or not license_link.path.startswith(license_path)
                    or (license_type == "by" and not creator)):
                return None
        urls = [] if commons or nasa else [f"{SEARCH_URL}{identifier}/thumb/"]
        if (origin.scheme == "https" and origin.host in IMAGE_HOSTS and not origin.userinfo
                and origin.port is None):
            urls.insert(0, str(origin))
    except (KeyError, TypeError, ValueError, AttributeError, httpx.InvalidURL):
        return None
    path = target / f"{identifier}.jpg"
    for url in urls:
        try:
            content = await download_image(client, url)
            await run_in_threadpool(save_image, content, path)
            return Material(path, str(item.get("title") or "素材图片")[:180], creator, source, license_url, query)
        except (httpx.HTTPError, ValueError, OSError, Image.DecompressionBombError):
            continue
    return None


async def search_materials(queries: list[str], target: Path) -> list[Material]:
    failures: set[tuple[str, str]] = set()

    async def get_catalog(client, url, params):
        host = httpx.URL(url).host
        for attempt in range(2):
            try:
                response = await client.get(url, params=params)
                response.raise_for_status()
                return response.json()
            except httpx.TimeoutException as exc:
                if attempt == 0:
                    continue
                failures.add((host, type(exc).__name__))
            except httpx.HTTPStatusError as exc:
                failures.add((host, f"HTTP {exc.response.status_code}"))
                break
            except (ValueError, httpx.RequestError) as exc:
                failures.add((host, type(exc).__name__))
                break
        return {}

    async def search(client, query):
        payload = await get_catalog(client, SEARCH_URL, {
            "q": query, "page_size": 6, "license": "by,cc0,pdm", "mature": "false",
        })
        if not isinstance(payload, dict) or not isinstance(payload.get("results"), list):
            return []
        return [(query, item) for item in payload["results"][:6] if isinstance(item, dict) and item.get("license") != "nasa"]

    async def commons_search(client, query):
        try:
            payload = await get_catalog(client, COMMONS_URL, {
                "action": "query", "generator": "search", "gsrnamespace": 6,
                "gsrsearch": f"{query} filetype:bitmap", "gsrlimit": 5, "prop": "imageinfo",
                "iiprop": "url|extmetadata", "iiurlwidth": 1200,
                "extmetadatalanguage": "en", "format": "json", "formatversion": 2,
            })
            pages = payload.get("query", {}).get("pages", [])
            return [(query, item) for page in pages if (item := commons_candidate(page))]
        except (ValueError, TypeError, AttributeError, httpx.HTTPError):
            return []

    async def nasa_search(client, query):
        if not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9 -]{1,64}", query):
            return []
        payload = await get_catalog(client, NASA_SEARCH_URL, {"q": query, "media_type": "image", "page_size": 10})
        try:
            return [(query, asset) for item in payload.get("collection", {}).get("items", [])[:10]
                    if (asset := nasa_candidate(item, query))]
        except (TypeError, AttributeError, ValueError):
            return []

    async def collect(client, groups, found):
        """Keep completed images in catalog order even when a slower download times out."""
        items = {}
        for row in zip_longest(*groups):
            for query, item in filter(None, row):
                if isinstance(item.get("id"), str):
                    items.setdefault(item["id"], (query, item))
        candidates = iter(items.values())
        while len(found) < MAX_ONLINE_IMAGES:
            batch = [next(candidates, None) for _ in range(MAX_ONLINE_IMAGES - len(found))]
            if all(item is None for item in batch):
                break
            tasks = [asyncio.create_task(fetch_result(client, item, target, query))
                     for query, item in filter(None, batch)]
            try:
                await asyncio.gather(*tasks)
            finally:
                for task in tasks:
                    if not task.done():
                        task.cancel()
                    elif not task.cancelled() and task.exception() is None:
                        material = task.result()
                        if material and all(previous.source != material.source for previous in found):
                            found.append(material)
                await asyncio.gather(*tasks, return_exceptions=True)

    if not queries:
        return []
    found = []
    try:
        # Honor the backend account's system or environment proxy for both queries and downloads.
        async with asyncio.timeout(48), httpx.AsyncClient(timeout=6, trust_env=True, follow_redirects=False, headers={
            "User-Agent": "Tongxue-Workbench/1.0 (presentation asset search)",
        }) as client:
            try:
                async with asyncio.timeout(15):
                    groups = await asyncio.gather(*(search(client, query) for query in queries[:5]))
                    await collect(client, groups, found)
            except TimeoutError:
                pass
            if len(found) < 2:
                groups = await asyncio.gather(*(search_catalog(client, query) for query in queries[:5]
                                                for search_catalog in (commons_search, nasa_search)))
                await collect(client, groups, found)
    except TimeoutError:
        logger.warning(CONSTANT.IMAGE_SEARCH_TIMEOUT_LOG, len(found))
    for host, reason in sorted(failures):
        logger.warning(CONSTANT.IMAGE_SEARCH_FAILED_LOG, host, reason)
    return found
