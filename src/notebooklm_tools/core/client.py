#!/usr/bin/env python3
"""NotebookLM MCP API client (notebooklm.google.com).

This module provides the full NotebookLMClient that inherits from BaseClient
and adds all domain-specific operations (notebooks, sources, studio, etc.).

Internal API. See CLAUDE.md for full documentation.
"""

import json
import logging
import re

from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable

import httpx

from . import constants
from .base import BaseClient, DEFAULT_TIMEOUT, SOURCE_ADD_TIMEOUT, logger
from .conversation import ConversationMixin
from .notebooks import NotebookMixin
from .research import ResearchMixin
from .sharing import SharingMixin
from .sources import SourceMixin


# Import utility functions from utils module
from .utils import (
    RPC_NAMES,
    _format_debug_json,
    _decode_request_body,
    _parse_url_params,
    parse_timestamp,
    extract_cookies_from_chrome_export,
)

# Import dataclasses from data_types module (re-exported for backward compatibility)
from .data_types import (
    ConversationTurn,
    Collaborator,
    ShareStatus,
    Notebook,
)


# Import exception classes from errors module (re-exported for backward compatibility)
from .errors import (
    NotebookLMError,
    ArtifactError,
    ArtifactNotReadyError,
    ArtifactParseError,
    ArtifactDownloadError,
    ArtifactNotFoundError,
    ClientAuthenticationError,
)

# Backward compatibility alias - code importing AuthenticationError from client.py
# will get the ClientAuthenticationError from errors.py
AuthenticationError = ClientAuthenticationError


# Ownership constants (from metadata position 0) - re-exported for backward compatibility
OWNERSHIP_MINE = constants.OWNERSHIP_MINE
OWNERSHIP_SHARED = constants.OWNERSHIP_SHARED


class NotebookLMClient(ResearchMixin, ConversationMixin, SourceMixin, SharingMixin, NotebookMixin):
    """Client for NotebookLM MCP internal API.
    
    This class extends BaseClient with all domain-specific operations:
    - Notebook management (list, create, rename, delete)
    - Source management (add, sync, delete)
    - Query/chat operations
    - Studio content (audio, video, reports, flashcards, etc.)
    - Research operations
    - Sharing and collaboration
    
    All HTTP/RPC infrastructure is provided by the BaseClient base class.
    """
    
    # Note: All RPC IDs, API constants, and infrastructure methods are inherited from BaseClient

    # =========================================================================
    # Conversation Operations (inherited from ConversationMixin)
    # =========================================================================
    # The following methods are provided by ConversationMixin:
    # - query
    # - clear_conversation
    # - get_conversation_history
    # - _build_conversation_history
    # - _cache_conversation_turn
    # - _parse_query_response
    # - _extract_answer_from_chunk
    # - _extract_source_ids_from_notebook

    # =========================================================================
    # Notebook Operations (inherited from NotebookMixin)
    # =========================================================================
    # The following methods are provided by NotebookMixin:
    # - list_notebooks
    # - get_notebook
    # - get_notebook_summary
    # - create_notebook
    # - rename_notebook
    # - configure_chat
    # - delete_notebook

    # =========================================================================
    # Sharing Operations (inherited from SharingMixin)
    # =========================================================================
    # The following methods are provided by SharingMixin:
    # - get_share_status
    # - set_public_access
    # - add_collaborator

    # =========================================================================
    # Source Operations (inherited from SourceMixin)
    # =========================================================================
    # The following methods are provided by SourceMixin:
    # - check_source_freshness
    # - sync_drive_source
    # - delete_source
    # - get_notebook_sources_with_types
    # - add_url_source
    # - add_text_source
    # - add_drive_source
    # - upload_file
    # - get_source_guide
    # - get_source_fulltext

    # =========================================================================
    # Download Operations
    # =========================================================================

    async def _download_url(
        self,
        url: str,
        output_path: str,
        progress_callback: Callable[[int, int], None] | None = None,
        chunk_size: int = 65536
    ) -> str:
        """Download content from a URL to a local file with streaming support.

        Features:
        - Streams file in chunks to minimize memory usage
        - Optional progress callback for UI integration
        - Per-chunk timeouts to detect stalled connections
        - Temp file usage to prevent corrupted partial downloads
        - Authentication error detection

        Args:
            url: The URL to download
            output_path: The local path to save the file
            progress_callback: Optional callback(bytes_downloaded, total_bytes)
            chunk_size: Size of chunks to read (default 64KB)

        Returns:
            The output path

        Raises:
            ArtifactDownloadError: If download fails
            AuthenticationError: If auth redirect detected
        """
        output_file = Path(output_path)
        output_file.parent.mkdir(parents=True, exist_ok=True)

        # Use temp file to prevent corrupted partial downloads
        temp_file = output_file.with_suffix(output_file.suffix + ".tmp")

        # Build headers with auth cookies
        base_headers = getattr(self, "_PAGE_FETCH_HEADERS", {
            "User-Agent": "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36"
        })
        headers = {**base_headers, "Referer": "https://notebooklm.google.com/"}

        # Use httpx.Cookies for proper cross-domain redirect handling
        cookies = self._get_httpx_cookies()

        # Per-chunk timeouts: 10s connect, 30s per chunk read/write
        # This allows large files to download without timeout while detecting stalls
        timeout = httpx.Timeout(connect=10.0, read=30.0, write=30.0, pool=30.0)

        try:
            async with httpx.AsyncClient(
                cookies=cookies,
                headers=headers,
                follow_redirects=True,
                timeout=timeout
            ) as client:
                async with client.stream("GET", url) as response:
                    response.raise_for_status()

                    # Get total size if available
                    content_length = response.headers.get("content-length")
                    total_bytes = int(content_length) if content_length else 0

                    # Check for auth redirect before starting download
                    content_type = response.headers.get("content-type", "").lower()
                    if "text/html" in content_type:
                        # Read first chunk to check for login page
                        first_chunk = b""
                        async for chunk in response.aiter_bytes(chunk_size=8192):
                            first_chunk = chunk
                            break

                        if b"<!doctype html>" in first_chunk.lower() or b"sign in" in first_chunk.lower():
                            raise AuthenticationError(
                                "Download failed: Redirected to login page. "
                                "Run 'nlm login' to refresh credentials."
                            )

                        # Not an auth error - write first chunk and continue
                        with open(temp_file, "wb") as f:
                            f.write(first_chunk)
                            bytes_downloaded = len(first_chunk)

                            if progress_callback:
                                progress_callback(bytes_downloaded, total_bytes)

                            # Continue streaming rest of file
                            async for chunk in response.aiter_bytes(chunk_size=chunk_size):
                                f.write(chunk)
                                bytes_downloaded += len(chunk)

                                if progress_callback:
                                    progress_callback(bytes_downloaded, total_bytes)
                    else:
                        # Binary content - stream directly
                        bytes_downloaded = 0
                        with open(temp_file, "wb") as f:
                            async for chunk in response.aiter_bytes(chunk_size=chunk_size):
                                f.write(chunk)
                                bytes_downloaded += len(chunk)

                                if progress_callback:
                                    progress_callback(bytes_downloaded, total_bytes)

            # Move temp file to final location only on success
            temp_file.rename(output_file)
            return str(output_file)

        except httpx.HTTPError as e:
            # Clean up temp file on failure
            if temp_file.exists():
                temp_file.unlink()
            raise ArtifactDownloadError(
                "file",
                details=f"HTTP error downloading from {url[:50]}...: {e}"
            ) from e
        except Exception as e:
            # Clean up temp file on failure
            if temp_file.exists():
                temp_file.unlink()
            raise ArtifactDownloadError(
                "file",
                details=f"Failed to download from {url[:50]}...: {str(e)}"
            ) from e

    def _list_raw(self, notebook_id: str) -> list[Any]:
        """Get raw artifact list for parsing download URLs."""
        # This reuses the list_notebooks parsing logic but returns the raw list
        # needed for extracting deeply nested metadata
        # RPC: wXbhsf is list_notebooks. But we need artifacts within a notebook.
        # Actually, artifacts are usually fetched via list_notebooks (which returns everything)
        # OR via specific RPCs.
        # Let's check how list_notebooks gets data.
        # It calls RPC_LIST_NOTEBOOKS.
        # But wait, audio/video are "studio artifacts".
        # We should use poll_studio_status to get the raw list of artifacts.
        
        # Poll params: [[2], notebook_id, 'NOT artifact.status = "ARTIFACT_STATUS_SUGGESTED"']
        params = [[2], notebook_id, 'NOT artifact.status = "ARTIFACT_STATUS_SUGGESTED"']
        body = self._build_request_body(self.RPC_POLL_STUDIO, params)
        url = self._build_url(self.RPC_POLL_STUDIO, f"/notebook/{notebook_id}")
        
        client = self._get_client()
        response = client.post(url, content=body)
        response.raise_for_status()
        
        parsed = self._parse_response(response.text)
        result = self._extract_rpc_result(parsed, self.RPC_POLL_STUDIO)
        
        if result and isinstance(result, list) and len(result) > 0:
             # Response is an array of artifacts, possibly wrapped
             return result[0] if isinstance(result[0], list) else result
        return []

    async def download_audio(
        self,
        notebook_id: str,
        output_path: str,
        artifact_id: str | None = None,
        progress_callback: Callable[[int, int], None] | None = None,
    ) -> str:
        """Download an Audio Overview to a file.

        Args:
            notebook_id: The notebook ID.
            output_path: Path to save the audio file (MP4/MP3).
            artifact_id: Specific artifact ID, or uses first completed audio.
            progress_callback: Optional callback(bytes_downloaded, total_bytes).

        Returns:
            The output path.
        """
        artifacts = self._list_raw(notebook_id)

        # Filter for completed audio (Type 1, Status 3)
        # Type 1 = STUDIO_TYPE_AUDIO (constants.py)
        # Status 3 = COMPLETED
        candidates = []
        for a in artifacts:
            if isinstance(a, list) and len(a) > 4:
                if a[2] == self.STUDIO_TYPE_AUDIO and a[4] == 3: # 3 is COMPLETED
                    candidates.append(a)

        if not candidates:
            raise ArtifactNotReadyError("audio")

        target = None
        if artifact_id:
            target = next((a for a in candidates if a[0] == artifact_id), None)
            if not target:
                raise ArtifactNotReadyError("audio", artifact_id)
        else:
            target = candidates[0]

        # Extract URL from metadata[6][5]
        try:
            metadata = target[6]
            if not isinstance(metadata, list) or len(metadata) <= 5:
                raise ArtifactParseError("audio", details="Invalid audio metadata structure")

            media_list = metadata[5]
            if not isinstance(media_list, list) or len(media_list) == 0:
                raise ArtifactParseError("audio", details="No media URLs found in metadata")

            # Look for audio/mp4 mime type
            url = None
            for item in media_list:
                if isinstance(item, list) and len(item) > 2 and item[2] == "audio/mp4":
                    url = item[0]
                    break

            # Fallback to first URL if no audio/mp4 found
            if not url and len(media_list) > 0 and isinstance(media_list[0], list):
                url = media_list[0][0]

            if not url:
                raise ArtifactDownloadError("audio", details="No download URL found")

            return await self._download_url(url, output_path, progress_callback)

        except (IndexError, TypeError, AttributeError) as e:
            raise ArtifactParseError("audio", details=str(e)) from e

    async def download_video(
        self,
        notebook_id: str,
        output_path: str,
        artifact_id: str | None = None,
        progress_callback: Callable[[int, int], None] | None = None,
    ) -> str:
        """Download a Video Overview to a file.

        Args:
            notebook_id: The notebook ID.
            output_path: Path to save the video file (MP4).
            artifact_id: Specific artifact ID, or uses first completed video.
            progress_callback: Optional callback(bytes_downloaded, total_bytes).

        Returns:
            The output path.
        """
        artifacts = self._list_raw(notebook_id)

        # Filter for completed video (Type 3, Status 3)
        candidates = []
        for a in artifacts:
            if isinstance(a, list) and len(a) > 4:
                 if a[2] == self.STUDIO_TYPE_VIDEO and a[4] == 3:
                     candidates.append(a)

        if not candidates:
            raise ArtifactNotReadyError("video")

        target = None
        if artifact_id:
            target = next((a for a in candidates if a[0] == artifact_id), None)
            if not target:
                raise ArtifactNotReadyError("video", artifact_id)
        else:
            target = candidates[0]

        # Extract URL from metadata[8]
        try:
            metadata = target[8]
            if not isinstance(metadata, list):
                raise ArtifactParseError("video", details="Invalid metadata structure")

            # First, find the media_list (nested list containing URLs)
            media_list = None
            for item in metadata:
                if (isinstance(item, list) and len(item) > 0 and
                    isinstance(item[0], list) and len(item[0]) > 0 and
                    isinstance(item[0][0], str) and item[0][0].startswith("http")):
                    media_list = item
                    break

            if not media_list:
                raise ArtifactDownloadError("video", details="No media URLs found in metadata")

            # Look for video/mp4 with optimal encoding (item[1] == 4 indicates priority)
            url = None
            for item in media_list:
                if isinstance(item, list) and len(item) > 2 and item[2] == "video/mp4":
                    url = item[0]
                    # Prefer URLs with priority flag (item[1] == 4)
                    if len(item) > 1 and item[1] == 4:
                        break

            # Fallback to first URL if no video/mp4 found
            if not url and len(media_list) > 0 and isinstance(media_list[0], list):
                url = media_list[0][0]

            if not url:
                raise ArtifactDownloadError("video", details="No download URL found")

            return await self._download_url(url, output_path, progress_callback)

        except (IndexError, TypeError, AttributeError) as e:
            raise ArtifactParseError("video", details=str(e)) from e

    # =========================================================================
    # Research Operations (inherited from ResearchMixin)
    # =========================================================================
    # The following methods are provided by ResearchMixin:
    # - start_research
    # - poll_research
    # - import_research_sources
    # - _parse_research_sources

    # =========================================================================
    # Studio Operations
    # =========================================================================

    def create_audio_overview(
        self,
        notebook_id: str,
        source_ids: list[str],
        format_code: int = 1,  # AUDIO_FORMAT_DEEP_DIVE
        length_code: int = 2,  # AUDIO_LENGTH_DEFAULT
        language: str = "en",
        focus_prompt: str = "",
    ) -> dict | None:
        """Create an Audio Overview (podcast) for a notebook.
    """
        client = self._get_client()

        # Build source IDs in the nested format: [[[id1]], [[id2]], ...]
        sources_nested = [[[sid]] for sid in source_ids]

        # Build source IDs in the simpler format: [[id1], [id2], ...]
        sources_simple = [[sid] for sid in source_ids]

        audio_options = [
            None,
            [
                focus_prompt,
                length_code,
                None,
                sources_simple,
                language,
                None,
                format_code
            ]
        ]

        params = [
            [2],
            notebook_id,
            [
                None, None,
                self.STUDIO_TYPE_AUDIO,
                sources_nested,
                None, None,
                audio_options
            ]
        ]

        body = self._build_request_body(self.RPC_CREATE_STUDIO, params)
        url = self._build_url(self.RPC_CREATE_STUDIO, f"/notebook/{notebook_id}")

        response = client.post(url, content=body)
        response.raise_for_status()

        parsed = self._parse_response(response.text)
        result = self._extract_rpc_result(parsed, self.RPC_CREATE_STUDIO)

        if result and isinstance(result, list) and len(result) > 0:
            artifact_data = result[0]
            artifact_id = artifact_data[0] if isinstance(artifact_data, list) and len(artifact_data) > 0 else None
            status_code = artifact_data[4] if isinstance(artifact_data, list) and len(artifact_data) > 4 else None

            return {
                "artifact_id": artifact_id,
                "notebook_id": notebook_id,
                "type": "audio",
                "status": "in_progress" if status_code == 1 else "completed" if status_code == 3 else "unknown",
                "format": constants.AUDIO_FORMATS.get_name(format_code),
                "length": constants.AUDIO_LENGTHS.get_name(length_code),
                "language": language,
            }

        return None

    def create_video_overview(
        self,
        notebook_id: str,
        source_ids: list[str],
        format_code: int = 1,  # VIDEO_FORMAT_EXPLAINER
        visual_style_code: int = 1,  # VIDEO_STYLE_AUTO_SELECT
        language: str = "en",
        focus_prompt: str = "",
    ) -> dict | None:
        """Create a Video Overview for a notebook.
    """
        client = self._get_client()

        # Build source IDs in the nested format: [[[id1]], [[id2]], ...]
        sources_nested = [[[sid]] for sid in source_ids]

        # Build source IDs in the simpler format: [[id1], [id2], ...]
        sources_simple = [[sid] for sid in source_ids]

        video_options = [
            None, None,
            [
                sources_simple,
                language,
                focus_prompt,
                None,
                format_code,
                visual_style_code
            ]
        ]

        params = [
            [2],
            notebook_id,
            [
                None, None,
                self.STUDIO_TYPE_VIDEO,
                sources_nested,
                None, None, None, None,
                video_options
            ]
        ]

        body = self._build_request_body(self.RPC_CREATE_STUDIO, params)
        url = self._build_url(self.RPC_CREATE_STUDIO, f"/notebook/{notebook_id}")

        response = client.post(url, content=body)
        response.raise_for_status()

        parsed = self._parse_response(response.text)
        result = self._extract_rpc_result(parsed, self.RPC_CREATE_STUDIO)

        if result and isinstance(result, list) and len(result) > 0:
            artifact_data = result[0]
            artifact_id = artifact_data[0] if isinstance(artifact_data, list) and len(artifact_data) > 0 else None
            status_code = artifact_data[4] if isinstance(artifact_data, list) and len(artifact_data) > 4 else None

            return {
                "artifact_id": artifact_id,
                "notebook_id": notebook_id,
                "type": "video",
                "status": "in_progress" if status_code == 1 else "completed" if status_code == 3 else "unknown",
                "format": constants.VIDEO_FORMATS.get_name(format_code),
                "visual_style": constants.VIDEO_STYLES.get_name(visual_style_code),
                "language": language,
            }

        return None

    def poll_studio_status(self, notebook_id: str) -> list[dict]:
        """Poll for studio content (audio/video overviews) status.
    """
        client = self._get_client()

        # Poll params: [[2], notebook_id, 'NOT artifact.status = "ARTIFACT_STATUS_SUGGESTED"']
        params = [[2], notebook_id, 'NOT artifact.status = "ARTIFACT_STATUS_SUGGESTED"']
        body = self._build_request_body(self.RPC_POLL_STUDIO, params)
        url = self._build_url(self.RPC_POLL_STUDIO, f"/notebook/{notebook_id}")

        response = client.post(url, content=body)
        response.raise_for_status()

        parsed = self._parse_response(response.text)
        result = self._extract_rpc_result(parsed, self.RPC_POLL_STUDIO)

        artifacts = []
        if result and isinstance(result, list) and len(result) > 0:
            # Response is an array of artifacts, possibly wrapped
            artifact_list = result[0] if isinstance(result[0], list) else result

            for artifact_data in artifact_list:
                if not isinstance(artifact_data, list) or len(artifact_data) < 5:
                    continue

                artifact_id = artifact_data[0]
                title = artifact_data[1] if len(artifact_data) > 1 else ""
                type_code = artifact_data[2] if len(artifact_data) > 2 else None
                status_code = artifact_data[4] if len(artifact_data) > 4 else None

                audio_url = None
                video_url = None
                duration_seconds = None

                # Audio artifacts have URLs at position 6
                if type_code == self.STUDIO_TYPE_AUDIO and len(artifact_data) > 6:
                    audio_options = artifact_data[6]
                    if isinstance(audio_options, list) and len(audio_options) > 3:
                        audio_url = audio_options[3] if isinstance(audio_options[3], str) else None
                        # Duration is often at position 9
                        if len(audio_options) > 9 and isinstance(audio_options[9], list):
                            duration_seconds = audio_options[9][0] if audio_options[9] else None

                # Video artifacts have URLs at position 8
                if type_code == self.STUDIO_TYPE_VIDEO and len(artifact_data) > 8:
                    video_options = artifact_data[8]
                    if isinstance(video_options, list) and len(video_options) > 3:
                        video_url = video_options[3] if isinstance(video_options[3], str) else None

                # Infographic artifacts have image URL at position 14
                infographic_url = None
                if type_code == self.STUDIO_TYPE_INFOGRAPHIC and len(artifact_data) > 14:
                    infographic_options = artifact_data[14]
                    if isinstance(infographic_options, list) and len(infographic_options) > 2:
                        # URL is at [2][0][1][0] - image_data[0][1][0]
                        image_data = infographic_options[2]
                        if isinstance(image_data, list) and len(image_data) > 0:
                            first_image = image_data[0]
                            if isinstance(first_image, list) and len(first_image) > 1:
                                image_details = first_image[1]
                                if isinstance(image_details, list) and len(image_details) > 0:
                                    url = image_details[0]
                                    if isinstance(url, str) and url.startswith("http"):
                                        infographic_url = url

                # Slide deck artifacts have download URL at position 16
                slide_deck_url = None
                if type_code == self.STUDIO_TYPE_SLIDE_DECK and len(artifact_data) > 16:
                    slide_deck_options = artifact_data[16]
                    if isinstance(slide_deck_options, list) and len(slide_deck_options) > 0:
                        # URL is typically at position 0 in the options
                        if isinstance(slide_deck_options[0], str) and slide_deck_options[0].startswith("http"):
                            slide_deck_url = slide_deck_options[0]
                        # Or may be nested deeper
                        elif len(slide_deck_options) > 3 and isinstance(slide_deck_options[3], str):
                            slide_deck_url = slide_deck_options[3]

                # Report artifacts have content at position 7
                report_content = None
                if type_code == self.STUDIO_TYPE_REPORT and len(artifact_data) > 7:
                    report_options = artifact_data[7]
                    if isinstance(report_options, list) and len(report_options) > 1:
                        # Content is nested in the options
                        content_data = report_options[1] if isinstance(report_options[1], list) else None
                        if content_data and len(content_data) > 0:
                            # Report content is typically markdown text
                            report_content = content_data[0] if isinstance(content_data[0], str) else None

                # Flashcard artifacts have cards data at position 9
                flashcard_count = None
                if type_code == self.STUDIO_TYPE_FLASHCARDS and len(artifact_data) > 9:
                    flashcard_options = artifact_data[9]
                    if isinstance(flashcard_options, list) and len(flashcard_options) > 1:
                        # Count cards in the data
                        cards_data = flashcard_options[1] if isinstance(flashcard_options[1], list) else None
                        if cards_data:
                            flashcard_count = len(cards_data) if isinstance(cards_data, list) else None

                # Extract created_at timestamp
                # Position varies by type but often at position 10, 15, or similar
                created_at = None
                # Try common timestamp positions
                for ts_pos in [10, 15, 17]:
                    if len(artifact_data) > ts_pos:
                        ts_candidate = artifact_data[ts_pos]
                        if isinstance(ts_candidate, list) and len(ts_candidate) >= 2:
                            # Check if it looks like a timestamp [seconds, nanos]
                            if isinstance(ts_candidate[0], (int, float)) and ts_candidate[0] > 1700000000:
                                created_at = parse_timestamp(ts_candidate)
                                break

                # Map type codes to type names
                type_map = {
                    self.STUDIO_TYPE_AUDIO: "audio",
                    self.STUDIO_TYPE_REPORT: "report",
                    self.STUDIO_TYPE_VIDEO: "video",
                    self.STUDIO_TYPE_FLASHCARDS: "flashcards",  # Also includes Quiz (type 4)
                    self.STUDIO_TYPE_INFOGRAPHIC: "infographic",
                    self.STUDIO_TYPE_SLIDE_DECK: "slide_deck",
                    self.STUDIO_TYPE_DATA_TABLE: "data_table",
                }
                artifact_type = type_map.get(type_code, "unknown")
                status = "in_progress" if status_code == 1 else "completed" if status_code == 3 else "unknown"

                artifacts.append({
                    "artifact_id": artifact_id,
                    "title": title,
                    "type": artifact_type,
                    "status": status,
                    "created_at": created_at,
                    "audio_url": audio_url,
                    "video_url": video_url,
                    "infographic_url": infographic_url,
                    "slide_deck_url": slide_deck_url,
                    "report_content": report_content,
                    "flashcard_count": flashcard_count,
                    "duration_seconds": duration_seconds,
                })

        return artifacts


    def get_studio_status(self, notebook_id: str) -> list[dict]:
        """Alias for poll_studio_status (used by CLI)."""
        return self.poll_studio_status(notebook_id)

    def delete_studio_artifact(self, artifact_id: str, notebook_id: str | None = None) -> bool:
        """Delete a studio artifact (Audio, Video, or Mind Map).

        WARNING: This action is IRREVERSIBLE. The artifact will be permanently deleted.

        Args:
            artifact_id: The artifact UUID to delete
            notebook_id: Optional notebook ID. Required for deleting Mind Maps.

        Returns:
            True on success, False on failure
        """
        # 1. Try standard deletion (Audio, Video, etc.)
        try:
            params = [[2], artifact_id]
            result = self._call_rpc(self.RPC_DELETE_STUDIO, params)
            if result is not None:
                return True
        except Exception:
            # Continue to fallback if standard delete fails
            pass

        # 2. Fallback: Try Mind Map deletion if we have a notebook ID
        # Mind maps require a different RPC (AH0mwd) and payload structure
        if notebook_id:
            return self.delete_mind_map(notebook_id, artifact_id)

        return False

    def delete_mind_map(self, notebook_id: str, mind_map_id: str) -> bool:
        """Delete a Mind Map artifact using the observed two-step RPC sequence.

        Args:
            notebook_id: The notebook UUID.
            mind_map_id: The Mind Map artifact UUID.

        Returns:
            True on success
        """
        # 1. We need the artifact-specific timestamp from LIST_MIND_MAPS
        params = [notebook_id]
        list_result = self._call_rpc(
            self.RPC_LIST_MIND_MAPS, params, f"/notebook/{notebook_id}"
        )

        timestamp = None
        if list_result and isinstance(list_result, list) and len(list_result) > 0:
            mm_list = list_result[0] if isinstance(list_result[0], list) else []
            for mm_entry in mm_list:
                if isinstance(mm_entry, list) and mm_entry[0] == mind_map_id:
                    # Based on debug output: item[1][2][2] contains [seconds, micros]
                    try:
                        timestamp = mm_entry[1][2][2]
                    except (IndexError, TypeError):
                        pass
                    break

        # 2. Step 1: UUID-based deletion (AH0mwd)
        params_v2 = [notebook_id, None, [mind_map_id], [2]]
        self._call_rpc(self.RPC_DELETE_MIND_MAP, params_v2, f"/notebook/{notebook_id}")

        # 3. Step 2: Timestamp-based sync/deletion (cFji9)
        # This is required to fully remove it from the list and avoid "ghosts"
        if timestamp:
            params_v1 = [notebook_id, None, timestamp, [2]]
            self._call_rpc(self.RPC_LIST_MIND_MAPS, params_v1, f"/notebook/{notebook_id}")

        return True

    def create_infographic(
        self,
        notebook_id: str,
        source_ids: list[str],
        orientation_code: int = 1,  # INFOGRAPHIC_ORIENTATION_LANDSCAPE
        detail_level_code: int = 2,  # INFOGRAPHIC_DETAIL_STANDARD
        language: str = "en",
        focus_prompt: str = "",
    ) -> dict | None:
        """Create an Infographic from notebook sources.
    """
        client = self._get_client()

        # Build source IDs in the nested format: [[[id1]], [[id2]], ...]
        sources_nested = [[[sid]] for sid in source_ids]

        # Options at position 14: [[focus_prompt, language, null, orientation, detail_level]]
        # Captured RPC structure was [[null, "en", null, 1, 2]]
        infographic_options = [[focus_prompt or None, language, None, orientation_code, detail_level_code]]

        content = [
            None, None,
            self.STUDIO_TYPE_INFOGRAPHIC,
            sources_nested,
            None, None, None, None, None, None, None, None, None, None,  # 10 nulls (positions 4-13)
            infographic_options  # position 14
        ]

        params = [
            [2],
            notebook_id,
            content
        ]

        body = self._build_request_body(self.RPC_CREATE_STUDIO, params)
        url = self._build_url(self.RPC_CREATE_STUDIO, f"/notebook/{notebook_id}")

        response = client.post(url, content=body)
        response.raise_for_status()

        parsed = self._parse_response(response.text)
        result = self._extract_rpc_result(parsed, self.RPC_CREATE_STUDIO)

        if result and isinstance(result, list) and len(result) > 0:
            artifact_data = result[0]
            artifact_id = artifact_data[0] if isinstance(artifact_data, list) and len(artifact_data) > 0 else None
            status_code = artifact_data[4] if isinstance(artifact_data, list) and len(artifact_data) > 4 else None

            return {
                "artifact_id": artifact_id,
                "notebook_id": notebook_id,
                "type": "infographic",
                "status": "in_progress" if status_code == 1 else "completed" if status_code == 3 else "unknown",
                "orientation": constants.INFOGRAPHIC_ORIENTATIONS.get_name(orientation_code),
                "detail_level": constants.INFOGRAPHIC_DETAILS.get_name(detail_level_code),
                "language": language,
            }

        return None

    def create_slide_deck(
        self,
        notebook_id: str,
        source_ids: list[str],
        format_code: int = 1,  # SLIDE_DECK_FORMAT_DETAILED
        length_code: int = 3,  # SLIDE_DECK_LENGTH_DEFAULT
        language: str = "en",
        focus_prompt: str = "",
    ) -> dict | None:
        """Create a Slide Deck from notebook sources.
    """
        client = self._get_client()

        # Build source IDs in the nested format: [[[id1]], [[id2]], ...]
        sources_nested = [[[sid]] for sid in source_ids]

        # Options at position 16: [[focus_prompt, language, format, length]]
        slide_deck_options = [[focus_prompt or None, language, format_code, length_code]]

        content = [
            None, None,
            self.STUDIO_TYPE_SLIDE_DECK,
            sources_nested,
            None, None, None, None, None, None, None, None, None, None, None, None,  # 12 nulls (positions 4-15)
            slide_deck_options  # position 16
        ]

        params = [
            [2],
            notebook_id,
            content
        ]

        body = self._build_request_body(self.RPC_CREATE_STUDIO, params)
        url = self._build_url(self.RPC_CREATE_STUDIO, f"/notebook/{notebook_id}")

        response = client.post(url, content=body)
        response.raise_for_status()

        parsed = self._parse_response(response.text)
        result = self._extract_rpc_result(parsed, self.RPC_CREATE_STUDIO)

        if result and isinstance(result, list) and len(result) > 0:
            artifact_data = result[0]
            artifact_id = artifact_data[0] if isinstance(artifact_data, list) and len(artifact_data) > 0 else None
            status_code = artifact_data[4] if isinstance(artifact_data, list) and len(artifact_data) > 4 else None

            return {
                "artifact_id": artifact_id,
                "notebook_id": notebook_id,
                "type": "slide_deck",
                "status": "in_progress" if status_code == 1 else "completed" if status_code == 3 else "unknown",
                "format": constants.SLIDE_DECK_FORMATS.get_name(format_code),
                "length": constants.SLIDE_DECK_LENGTHS.get_name(length_code),
                "language": language,
            }

        return None

    def create_report(
        self,
        notebook_id: str,
        source_ids: list[str],
        report_format: str = "Briefing Doc",
        custom_prompt: str = "",
        language: str = "en",
    ) -> dict | None:
        """Create a Report from notebook sources.
    """
        client = self._get_client()

        # Build source IDs in the nested format: [[[id1]], [[id2]], ...]
        sources_nested = [[[sid]] for sid in source_ids]

        # Build source IDs in the simpler format: [[id1], [id2], ...]
        sources_simple = [[sid] for sid in source_ids]

        # Map report format to title, description, and prompt
        format_configs = {
            "Briefing Doc": {
                "title": "Briefing Doc",
                "description": "Key insights and important quotes",
                "prompt": (
                    "Create a comprehensive briefing document that includes an "
                    "Executive Summary, detailed analysis of key themes, important "
                    "quotes with context, and actionable insights."
                ),
            },
            "Study Guide": {
                "title": "Study Guide",
                "description": "Short-answer quiz, essay questions, glossary",
                "prompt": (
                    "Create a comprehensive study guide that includes key concepts, "
                    "short-answer practice questions, essay prompts for deeper "
                    "exploration, and a glossary of important terms."
                ),
            },
            "Blog Post": {
                "title": "Blog Post",
                "description": "Insightful takeaways in readable article format",
                "prompt": (
                    "Write an engaging blog post that presents the key insights "
                    "in an accessible, reader-friendly format. Include an attention-"
                    "grabbing introduction, well-organized sections, and a compelling "
                    "conclusion with takeaways."
                ),
            },
            "Create Your Own": {
                "title": "Custom Report",
                "description": "Custom format",
                "prompt": custom_prompt or "Create a report based on the provided sources.",
            },
        }

        if report_format not in format_configs:
            raise ValueError(
                f"Invalid report_format: {report_format}. "
                f"Must be one of: {list(format_configs.keys())}"
            )

        config = format_configs[report_format]

        # Options at position 7: [null, [title, desc, null, sources, lang, prompt, null, True]]
        report_options = [
            None,
            [
                config["title"],
                config["description"],
                None,
                sources_simple,
                language,
                config["prompt"],
                None,
                True
            ]
        ]

        content = [
            None, None,
            self.STUDIO_TYPE_REPORT,
            sources_nested,
            None, None, None,
            report_options
        ]

        params = [
            [2],
            notebook_id,
            content
        ]

        body = self._build_request_body(self.RPC_CREATE_STUDIO, params)
        url = self._build_url(self.RPC_CREATE_STUDIO, f"/notebook/{notebook_id}")

        response = client.post(url, content=body)
        response.raise_for_status()

        parsed = self._parse_response(response.text)
        result = self._extract_rpc_result(parsed, self.RPC_CREATE_STUDIO)

        if result and isinstance(result, list) and len(result) > 0:
            artifact_data = result[0]
            artifact_id = artifact_data[0] if isinstance(artifact_data, list) and len(artifact_data) > 0 else None
            status_code = artifact_data[4] if isinstance(artifact_data, list) and len(artifact_data) > 4 else None

            return {
                "artifact_id": artifact_id,
                "notebook_id": notebook_id,
                "type": "report",
                "status": "in_progress" if status_code == 1 else "completed" if status_code == 3 else "unknown",
                "format": report_format,
                "language": language,
            }

        return None

    def create_flashcards(
        self,
        notebook_id: str,
        source_ids: list[str],
        difficulty_code: int = 2,  # FLASHCARD_DIFFICULTY_MEDIUM
    ) -> dict | None:
        """Create Flashcards from notebook sources.
    """
        client = self._get_client()

        # Build source IDs in the nested format: [[[id1]], [[id2]], ...]
        sources_nested = [[[sid]] for sid in source_ids]

        # Card count code (default = 2)
        count_code = constants.FLASHCARD_COUNT_DEFAULT

        # Options at position 9: [null, [1, null*5, [difficulty, card_count]]]
        flashcard_options = [
            None,
            [
                1,  # Unknown (possibly default count base)
                None, None, None, None, None,
                [difficulty_code, count_code]
            ]
        ]

        content = [
            None, None,
            self.STUDIO_TYPE_FLASHCARDS,
            sources_nested,
            None, None, None, None, None,  # 5 nulls (positions 4-8)
            flashcard_options  # position 9
        ]

        params = [
            [2],
            notebook_id,
            content
        ]

        body = self._build_request_body(self.RPC_CREATE_STUDIO, params)
        url = self._build_url(self.RPC_CREATE_STUDIO, f"/notebook/{notebook_id}")

        response = client.post(url, content=body)
        response.raise_for_status()

        parsed = self._parse_response(response.text)
        result = self._extract_rpc_result(parsed, self.RPC_CREATE_STUDIO)

        if result and isinstance(result, list) and len(result) > 0:
            artifact_data = result[0]
            artifact_id = artifact_data[0] if isinstance(artifact_data, list) and len(artifact_data) > 0 else None
            status_code = artifact_data[4] if isinstance(artifact_data, list) and len(artifact_data) > 4 else None

            return {
                "artifact_id": artifact_id,
                "notebook_id": notebook_id,
                "type": "flashcards",
                "status": "in_progress" if status_code == 1 else "completed" if status_code == 3 else "unknown",
                "difficulty": constants.FLASHCARD_DIFFICULTIES.get_name(difficulty_code),
            }

        return None

    def create_quiz(
        self,
        notebook_id: str,
        source_ids: list[str],
        question_count: int = 2,
        difficulty: int = 2,
    ) -> dict | None:
        """Create Quiz from notebook sources.

        Args:
            notebook_id: Notebook UUID
            source_ids: List of source UUIDs
            question_count: Number of questions (default: 2)
            difficulty: Difficulty level (default: 2)
        """
        client = self._get_client()
        sources_nested = [[[sid]] for sid in source_ids]

        # Quiz options at position 9: [null, [2, null*6, [question_count, difficulty]]]
        quiz_options = [
            None,
            [
                2,  # Format/variant code
                None, None, None, None, None, None,
                [question_count, difficulty]
            ]
        ]

        content = [
            None, None,
            self.STUDIO_TYPE_FLASHCARDS,  # Type 4 (shared with flashcards)
            sources_nested,
            None, None, None, None, None,
            quiz_options  # position 9
        ]

        params = [[2], notebook_id, content]

        body = self._build_request_body(self.RPC_CREATE_STUDIO, params)
        url = self._build_url(self.RPC_CREATE_STUDIO, f"/notebook/{notebook_id}")

        response = client.post(url, content=body)
        response.raise_for_status()

        parsed = self._parse_response(response.text)
        result = self._extract_rpc_result(parsed, self.RPC_CREATE_STUDIO)

        if result and isinstance(result, list) and len(result) > 0:
            artifact_data = result[0]
            artifact_id = artifact_data[0] if isinstance(artifact_data, list) and len(artifact_data) > 0 else None
            status_code = artifact_data[4] if isinstance(artifact_data, list) and len(artifact_data) > 4 else None

            return {
                "artifact_id": artifact_id,
                "notebook_id": notebook_id,
                "type": "quiz",
                "status": "in_progress" if status_code == 1 else "completed" if status_code == 3 else "unknown",
                "question_count": question_count,
                "difficulty": constants.FLASHCARD_DIFFICULTIES.get_name(difficulty),
            }

        return None

    def create_data_table(
        self,
        notebook_id: str,
        source_ids: list[str],
        description: str,
        language: str = "en",
    ) -> dict | None:
        """Create Data Table from notebook sources.

        Args:
            notebook_id: Notebook UUID
            source_ids: List of source UUIDs
            description: Description of the data table to create
            language: Language code (default: "en")
        """
        client = self._get_client()
        sources_nested = [[[sid]] for sid in source_ids]

        # Data Table options at position 18: [null, [description, language]]
        datatable_options = [None, [description, language]]

        content = [
            None, None,
            self.STUDIO_TYPE_DATA_TABLE,  # Type 9
            sources_nested,
            None, None, None, None, None, None, None, None, None, None, None, None, None, None,  # 14 nulls (positions 4-17)
            datatable_options  # position 18
        ]

        params = [[2], notebook_id, content]

        body = self._build_request_body(self.RPC_CREATE_STUDIO, params)
        url = self._build_url(self.RPC_CREATE_STUDIO, f"/notebook/{notebook_id}")

        response = client.post(url, content=body)
        response.raise_for_status()

        parsed = self._parse_response(response.text)
        result = self._extract_rpc_result(parsed, self.RPC_CREATE_STUDIO)

        if result and isinstance(result, list) and len(result) > 0:
            artifact_data = result[0]
            artifact_id = artifact_data[0] if isinstance(artifact_data, list) and len(artifact_data) > 0 else None
            status_code = artifact_data[4] if isinstance(artifact_data, list) and len(artifact_data) > 4 else None

            return {
                "artifact_id": artifact_id,
                "notebook_id": notebook_id,
                "type": "data_table",
                "status": "in_progress" if status_code == 1 else "completed" if status_code == 3 else "unknown",
                "description": description,
            }

        return None

    def generate_mind_map(
        self,
        source_ids: list[str],
    ) -> dict | None:
        """Generate a Mind Map JSON from sources.

        This is step 1 of 2 for creating a mind map. After generation,
        use save_mind_map() to save it to a notebook.

        Args:
            source_ids: List of source UUIDs to include

        Returns:
            Dict with mind_map_json and generation_id, or None on failure
        """
        client = self._get_client()

        # Build source IDs in the nested format: [[[id1]], [[id2]], ...]
        sources_nested = [[[sid]] for sid in source_ids]

        params = [
            sources_nested,
            None, None, None, None,
            ["interactive_mindmap", [["[CONTEXT]", ""]], ""],
            None,
            [2, None, [1]]
        ]

        body = self._build_request_body(self.RPC_GENERATE_MIND_MAP, params)
        url = self._build_url(self.RPC_GENERATE_MIND_MAP)

        response = client.post(url, content=body)
        response.raise_for_status()

        parsed = self._parse_response(response.text)
        result = self._extract_rpc_result(parsed, self.RPC_GENERATE_MIND_MAP)

        if result and isinstance(result, list) and len(result) > 0:
            # Response is nested: [[json_string, null, [gen_ids]]]
            # So result[0] is [json_string, null, [gen_ids]]
            inner = result[0] if isinstance(result[0], list) else result

            mind_map_json = inner[0] if isinstance(inner[0], str) else None
            generation_info = inner[2] if len(inner) > 2 else None

            generation_id = None
            if isinstance(generation_info, list) and len(generation_info) > 0:
                generation_id = generation_info[0]

            return {
                "mind_map_json": mind_map_json,
                "generation_id": generation_id,
                "source_ids": source_ids,
            }

        return None

    def save_mind_map(
        self,
        notebook_id: str,
        mind_map_json: str,
        source_ids: list[str],
        title: str = "Mind Map",
    ) -> dict | None:
        """Save a generated Mind Map to a notebook.

        This is step 2 of 2 for creating a mind map. First use
        generate_mind_map() to create the JSON structure.

        Args:
            notebook_id: The notebook UUID
            mind_map_json: The JSON string from generate_mind_map()
            source_ids: List of source UUIDs used to generate the map
            title: Display title for the mind map

        Returns:
            Dict with mind_map_id and saved info, or None on failure
        """
        client = self._get_client()

        # Build source IDs in the simpler format: [[id1], [id2], ...]
        sources_simple = [[sid] for sid in source_ids]

        metadata = [2, None, None, 5, sources_simple]

        params = [
            notebook_id,
            mind_map_json,
            metadata,
            None,
            title
        ]

        body = self._build_request_body(self.RPC_SAVE_MIND_MAP, params)
        url = self._build_url(self.RPC_SAVE_MIND_MAP, f"/notebook/{notebook_id}")

        response = client.post(url, content=body)
        response.raise_for_status()

        parsed = self._parse_response(response.text)
        result = self._extract_rpc_result(parsed, self.RPC_SAVE_MIND_MAP)

        if result and isinstance(result, list) and len(result) > 0:
            # Response is nested: [[mind_map_id, json, metadata, null, title]]
            inner = result[0] if isinstance(result[0], list) else result

            mind_map_id = inner[0] if len(inner) > 0 else None
            saved_json = inner[1] if len(inner) > 1 else None
            saved_title = inner[4] if len(inner) > 4 else title

            return {
                "mind_map_id": mind_map_id,
                "notebook_id": notebook_id,
                "title": saved_title,
                "mind_map_json": saved_json,
            }

        return None

    def list_mind_maps(self, notebook_id: str) -> list[dict]:
        """List all Mind Maps in a notebook.
    """
        client = self._get_client()

        params = [notebook_id]

        body = self._build_request_body(self.RPC_LIST_MIND_MAPS, params)
        url = self._build_url(self.RPC_LIST_MIND_MAPS, f"/notebook/{notebook_id}")

        response = client.post(url, content=body)
        response.raise_for_status()

        parsed = self._parse_response(response.text)
        result = self._extract_rpc_result(parsed, self.RPC_LIST_MIND_MAPS)

        mind_maps = []
        if result and isinstance(result, list) and len(result) > 0:
            mind_map_list = result[0] if isinstance(result[0], list) else []

            for mind_map_data in mind_map_list:
                # Skip invalid or tombstone entries (deleted entries have details=None)
                # Tombstone format: [uuid, null, 2]
                if not isinstance(mind_map_data, list) or len(mind_map_data) < 2:
                    continue
                
                details = mind_map_data[1]
                if details is None:
                    # This is a tombstone/deleted entry, skip it
                    continue

                mind_map_id = mind_map_data[0]

                if isinstance(details, list) and len(details) >= 5:
                    # Details: [id, json, metadata, null, title]
                    mind_map_json = details[1] if len(details) > 1 else None
                    title = details[4] if len(details) > 4 else "Mind Map"
                    metadata = details[2] if len(details) > 2 else []

                    created_at = None
                    if isinstance(metadata, list) and len(metadata) > 2:
                        ts = metadata[2]
                        created_at = parse_timestamp(ts)

                    mind_maps.append({
                        "mind_map_id": mind_map_id,
                        "title": title,
                        "mind_map_json": mind_map_json,
                        "created_at": created_at,
                    })

        return mind_maps


    def close(self) -> None:
        """Close the HTTP client."""
        if self._client:
            self._client.close()
            self._client = None



    async def download_infographic(
        self,
        notebook_id: str,
        output_path: str,
        artifact_id: str | None = None,
        progress_callback: Callable[[int, int], None] | None = None,
    ) -> str:
        """Download an Infographic to a file.

        Args:
            notebook_id: The notebook ID.
            output_path: Path to save the PNG file.
            artifact_id: Specific artifact ID, or uses first completed infographic.
            progress_callback: Optional callback(bytes_downloaded, total_bytes).

        Returns:
            The output path.
        """
        artifacts = self._list_raw(notebook_id)

        # Filter for completed infographics (Type 7, Status 3)
        candidates = []
        for a in artifacts:
            if isinstance(a, list) and len(a) > 5:
                if a[2] == self.STUDIO_TYPE_INFOGRAPHIC and a[4] == 3:
                    candidates.append(a)

        if not candidates:
            raise ArtifactNotReadyError("infographic")

        target = None
        if artifact_id:
            target = next((a for a in candidates if a[0] == artifact_id), None)
            if not target:
                raise ArtifactNotReadyError("infographic", artifact_id)
        else:
            target = candidates[0]

        # Extract URL from metadata[5][0][0]
        try:
            metadata = target[5]
            if not isinstance(metadata, list) or len(metadata) == 0:
                raise ArtifactParseError("infographic", details="Invalid metadata structure")

            media_list = metadata[0]
            if not isinstance(media_list, list) or len(media_list) == 0:
                raise ArtifactParseError("infographic", details="No media URLs found in metadata")

            url = media_list[0][0] if isinstance(media_list[0], list) else None
            if not url:
                raise ArtifactDownloadError("infographic", details="No download URL found")

            return await self._download_url(url, output_path, progress_callback)

        except (IndexError, TypeError, AttributeError) as e:
            raise ArtifactParseError("infographic", details=str(e)) from e


    async def download_slide_deck(
        self,
        notebook_id: str,
        output_path: str,
        artifact_id: str | None = None,
        progress_callback: Callable[[int, int], None] | None = None,
    ) -> str:
        """Download a Slide Deck to a file (PDF).

        Args:
            notebook_id: The notebook ID.
            output_path: Path to save the PDF file.
            artifact_id: Specific artifact ID, or uses first completed slide deck.
            progress_callback: Optional callback(bytes_downloaded, total_bytes).

        Returns:
            The output path.
        """
        artifacts = self._list_raw(notebook_id)

        # Filter for completed slide decks (Type 8, Status 3)
        candidates = []
        for a in artifacts:
            if isinstance(a, list) and len(a) > 5:
                if a[2] == self.STUDIO_TYPE_SLIDE_DECK and a[4] == 3:
                    candidates.append(a)

        if not candidates:
            raise ArtifactNotReadyError("slide_deck")

        target = None
        if artifact_id:
            target = next((a for a in candidates if a[0] == artifact_id), None)
            if not target:
                raise ArtifactNotReadyError("slide_deck", artifact_id)
        else:
            target = candidates[0]

        # Extract PDF URL from metadata[12][0][1] (contribution.usercontent.google.com)
        try:
            metadata = target[12]
            if not isinstance(metadata, list) or len(metadata) == 0:
                raise ArtifactParseError("slide_deck", details="Invalid metadata structure")

            media_list = metadata[0]
            if not isinstance(media_list, list) or len(media_list) < 2:
                raise ArtifactParseError("slide_deck", details="No media URLs found in metadata")

            pdf_url = media_list[1]
            if not pdf_url or not isinstance(pdf_url, str):
                raise ArtifactDownloadError("slide_deck", details="No download URL found")

            return await self._download_url(pdf_url, output_path, progress_callback)

        except (IndexError, TypeError, AttributeError) as e:
            raise ArtifactParseError("slide_deck", details=str(e)) from e

        
    def download_report(
        self,
        notebook_id: str,
        output_path: str,
        artifact_id: str | None = None,
    ) -> str:
        """Download a report artifact as markdown.

        Args:
            notebook_id: The notebook ID.
            output_path: Path to save the markdown file.
            artifact_id: Specific artifact ID, or uses first completed report.

        Returns:
            The output path where the file was saved.
        """
        artifacts = self._list_raw(notebook_id)

        # Filter for completed reports (Type 6, Status 3)
        candidates = []
        for a in artifacts:
            if isinstance(a, list) and len(a) > 7:
                 if a[2] == self.STUDIO_TYPE_REPORT and a[4] == 3:
                     candidates.append(a)
        
        if not candidates:
             raise ArtifactNotReadyError("report")
        
        target = None
        if artifact_id:
            target = next((a for a in candidates if a[0] == artifact_id), None)
            if not target:
                raise ArtifactNotReadyError("report", artifact_id)
        else:
            target = candidates[0]

        try:
            # Report content is in index 7
            content_wrapper = target[7]
            markdown_content = ""
            
            if isinstance(content_wrapper, list) and len(content_wrapper) > 0:
                markdown_content = content_wrapper[0]
            elif isinstance(content_wrapper, str):
                markdown_content = content_wrapper
            
            if not isinstance(markdown_content, str):
                raise ArtifactParseError("report", details="Invalid content structure")

            output = Path(output_path)
            output.parent.mkdir(parents=True, exist_ok=True)
            output.write_text(markdown_content, encoding="utf-8")
            return str(output)

        except (IndexError, TypeError, AttributeError) as e:
            raise ArtifactParseError("report", details=str(e)) from e

    def download_mind_map(
        self,
        notebook_id: str,
        output_path: str,
        artifact_id: str | None = None,
    ) -> str:
        """Download a mind map as JSON.

        Mind maps are stored in the notes system, not the regular artifacts list.

        Args:
            notebook_id: The notebook ID.
            output_path: Path to save the JSON file.
            artifact_id: Specific mind map ID (note ID), or uses first available.

        Returns:
            The output path where the file was saved.
        """
        # Mind maps are retrieved via list_mind_maps RPC
        params = [notebook_id]
        result = self._call_rpc(self.RPC_LIST_MIND_MAPS, params, f"/notebook/{notebook_id}")
        
        mind_maps = []
        if result and isinstance(result, list) and len(result) > 0:
            if isinstance(result[0], list):
                 mind_maps = result[0]
        
        if not mind_maps:
            raise ArtifactNotReadyError("mind_map")

        target = None
        if artifact_id:
            target = next((mm for mm in mind_maps if isinstance(mm, list) and mm[0] == artifact_id), None)
            if not target:
                raise ArtifactNotFoundError(artifact_id, artifact_type="mind_map")
        else:
            target = mind_maps[0]

        try:
            # Mind map JSON is stringified in target[1][1]
            if len(target) > 1 and isinstance(target[1], list) and len(target[1]) > 1:
                 json_string = target[1][1]
                 if isinstance(json_string, str):
                     json_data = json.loads(json_string)
                     
                     output = Path(output_path)
                     output.parent.mkdir(parents=True, exist_ok=True)
                     output.write_text(json.dumps(json_data, indent=2, ensure_ascii=False), encoding="utf-8")
                     return str(output)
            
            raise ArtifactParseError("mind_map", details="Invalid structure")

        except (IndexError, TypeError, json.JSONDecodeError, AttributeError) as e:
            raise ArtifactParseError("mind_map", details=str(e)) from e

    @staticmethod
    def _extract_cell_text(cell: Any, _depth: int = 0) -> str:
        """Recursively extract text from a nested data table cell structure.

        Data table cells have deeply nested arrays with position markers (integers)
        and text content (strings). This function traverses the structure and
        concatenates all text fragments found.

        Features:
        - Depth tracking to prevent infinite recursion (max 100 levels)
        - Handles None values gracefully
        - Strips whitespace from extracted text
        - Type validation at each level

        Args:
            cell: The cell data structure (can be str, int, list, or other)
            _depth: Internal recursion depth counter for safety

        Returns:
            Extracted text string, stripped of leading/trailing whitespace
        """
        # Safety: prevent infinite recursion
        if _depth > 100:
            return ""

        # Handle different types
        if cell is None:
            return ""
        if isinstance(cell, str):
            return cell.strip()
        if isinstance(cell, (int, float)):
            return ""  # Position markers are numeric
        if isinstance(cell, list):
            # Recursively extract from all list items
            parts = []
            for item in cell:
                text = NotebookLMClient._extract_cell_text(item, _depth + 1)
                if text:
                    parts.append(text)
            return " ".join(parts) if parts else ""

        # Unknown type - convert to string as fallback
        return str(cell).strip()

    def _parse_data_table(
        self,
        raw_data: list,
        validate_columns: bool = True
    ) -> tuple[list[str], list[list[str]]]:
        """Parse rich-text data table into headers and rows.

        Features:
        - Validates structure at each navigation step with clear error messages
        - Optional column count validation across all rows
        - Handles missing/empty cells gracefully
        - Provides detailed error context for debugging

        Structure: raw_data[0][0][0][0][4][2] contains the rows array where:
        - [0][0][0][0] navigates through wrapper layers
        - [4] contains the table content section [type, flags, rows_array]
        - [2] is the actual rows array

        Each row: [start_pos, end_pos, [cell1, cell2, ...]]
        Each cell: deeply nested with position markers mixed with text

        Args:
            raw_data: The raw data table metadata from artifact[18]
            validate_columns: If True, ensures all rows have same column count as headers

        Returns:
            Tuple of (headers, rows) where:
            - headers: List of column names
            - rows: List of data rows (each row is a list matching header length)

        Raises:
            ArtifactParseError: With detailed context if parsing fails
        """
        # Validate and navigate structure with clear error messages
        try:
            if not isinstance(raw_data, list) or len(raw_data) == 0:
                raise ArtifactParseError(
                    "data_table",
                    details="Invalid raw_data: expected non-empty list at artifact[18]"
                )

            # Navigate: raw_data[0]
            layer1 = raw_data[0]
            if not isinstance(layer1, list) or len(layer1) == 0:
                raise ArtifactParseError(
                    "data_table",
                    details="Invalid structure at raw_data[0]: expected non-empty list"
                )

            # Navigate: [0][0]
            layer2 = layer1[0]
            if not isinstance(layer2, list) or len(layer2) == 0:
                raise ArtifactParseError(
                    "data_table",
                    details="Invalid structure at raw_data[0][0]: expected non-empty list"
                )

            # Navigate: [0][0][0]
            layer3 = layer2[0]
            if not isinstance(layer3, list) or len(layer3) == 0:
                raise ArtifactParseError(
                    "data_table",
                    details="Invalid structure at raw_data[0][0][0]: expected non-empty list"
                )

            # Navigate: [0][0][0][0]
            layer4 = layer3[0]
            if not isinstance(layer4, list) or len(layer4) < 5:
                raise ArtifactParseError(
                    "data_table",
                    details=f"Invalid structure at raw_data[0][0][0][0]: expected list with at least 5 elements, got {len(layer4) if isinstance(layer4, list) else type(layer4).__name__}"
                )

            # Navigate: [0][0][0][0][4] - table content section
            table_section = layer4[4]
            if not isinstance(table_section, list) or len(table_section) < 3:
                raise ArtifactParseError(
                    "data_table",
                    details=f"Invalid table section at raw_data[0][0][0][0][4]: expected list with at least 3 elements, got {len(table_section) if isinstance(table_section, list) else type(table_section).__name__}"
                )

            # Navigate: [0][0][0][0][4][2] - rows array
            rows_array = table_section[2]
            if not isinstance(rows_array, list):
                raise ArtifactParseError(
                    "data_table",
                    details=f"Invalid rows array at raw_data[0][0][0][0][4][2]: expected list, got {type(rows_array).__name__}"
                )

            if not rows_array:
                raise ArtifactParseError(
                    "data_table",
                    details="Empty rows array - data table contains no data"
                )

        except IndexError as e:
            raise ArtifactParseError(
                "data_table",
                details=f"Structure navigation failed - table may be corrupted or in unexpected format: {e}"
            ) from e

        # Extract headers and rows
        headers: list[str] = []
        rows: list[list[str]] = []
        skipped_rows = 0

        for i, row_section in enumerate(rows_array):
            # Validate row format: [start_pos, end_pos, [cell_array]]
            if not isinstance(row_section, list):
                skipped_rows += 1
                continue

            if len(row_section) < 3:
                skipped_rows += 1
                continue

            cell_array = row_section[2]
            if not isinstance(cell_array, list):
                skipped_rows += 1
                continue

            # Extract text from each cell
            row_values = [self._extract_cell_text(cell) for cell in cell_array]

            # First row is headers
            if i == 0:
                headers = row_values
                if not headers or all(not h for h in headers):
                    raise ArtifactParseError(
                        "data_table",
                        details="First row (headers) is empty - table must have column headers"
                    )
            else:
                # Validate column count if requested
                if validate_columns and len(row_values) != len(headers):
                    # Pad or truncate to match header length
                    if len(row_values) < len(headers):
                        row_values.extend([""] * (len(headers) - len(row_values)))
                    else:
                        row_values = row_values[:len(headers)]

                rows.append(row_values)

        # Final validation
        if not headers:
            raise ArtifactParseError(
                "data_table",
                details="Failed to extract headers - first row may be malformed"
            )

        if not rows:
            raise ArtifactParseError(
                "data_table",
                details=f"No data rows extracted (skipped {skipped_rows} malformed rows)"
            )

        return headers, rows

    def download_data_table(
        self,
        notebook_id: str,
        output_path: str,
        artifact_id: str | None = None,
    ) -> str:
        """Download a data table as CSV.

        Args:
            notebook_id: The notebook ID.
            output_path: Path to save the CSV file.
            artifact_id: Specific artifact ID, or uses first completed data table.

        Returns:
            The output path where the file was saved.
        """
        import csv

        artifacts = self._list_raw(notebook_id)

        # Filter for completed data tables (Type 9, Status 3)
        candidates = []
        for a in artifacts:
            if isinstance(a, list) and len(a) > 18:
                if a[2] == self.STUDIO_TYPE_DATA_TABLE and a[4] == 3:
                    candidates.append(a)

        if not candidates:
            raise ArtifactNotReadyError("data_table")

        target = None
        if artifact_id:
            target = next((a for a in candidates if a[0] == artifact_id), None)
            if not target:
                raise ArtifactNotReadyError("data_table", artifact_id)
        else:
            target = candidates[0]

        try:
            # Data is at index 18
            raw_data = target[18]
            headers, rows = self._parse_data_table(raw_data)

            # Write to CSV
            output = Path(output_path)
            output.parent.mkdir(parents=True, exist_ok=True)

            with open(output, 'w', newline='', encoding='utf-8-sig') as f:
                writer = csv.writer(f)
                writer.writerow(headers)
                writer.writerows(rows)

            return str(output)

        except (IndexError, TypeError, AttributeError) as e:
            raise ArtifactParseError("data_table", details=str(e)) from e
        
    def _get_artifact_content(self, notebook_id: str, artifact_id: str) -> str | None:
        """Fetch artifact HTML content for quiz/flashcard types.

        Args:
            notebook_id: The notebook ID.
            artifact_id: The artifact ID.

        Returns:
            HTML content string, or None if not found.

        Raises:
            ArtifactDownloadError: If API response structure is unexpected.
        """
        result = self._call_rpc(
            self.RPC_GET_INTERACTIVE_HTML,
            [artifact_id],
            f"/notebook/{notebook_id}"
        )

        if not result:
            logger.debug(f"Empty response for artifact {artifact_id}")
            return None

        # Response structure: result[0] contains artifact data
        # HTML content is at result[0][9][0]
        # This is reverse-engineered from the API and may change
        try:
            if not isinstance(result, list) or len(result) == 0:
                logger.warning(f"Unexpected response type for {artifact_id}: {type(result)}")
                return None

            data = result[0]
            if not isinstance(data, list):
                logger.warning(f"Unexpected artifact data type: {type(data)}")
                return None

            if len(data) <= 9:
                logger.warning(f"Artifact data too short (len={len(data)}), expected index 9")
                return None

            html_container = data[9]
            if not html_container or not isinstance(html_container, list) or len(html_container) == 0:
                logger.debug(f"No HTML content in artifact {artifact_id}")
                return None

            return html_container[0]

        except (IndexError, TypeError) as e:
            logger.error(f"Error parsing artifact content for {artifact_id}: {e}")
            # Log truncated response for debugging
            result_preview = str(result)[:500] if result else "None"
            logger.debug(f"Response preview: {result_preview}...")
            raise ArtifactDownloadError(
                "interactive",
                details=f"Unexpected API response structure: {e}"
            ) from e

    def _extract_app_data(self, html_content: str) -> dict:
        """Extract JSON app data from interactive HTML.

        Quiz and flashcard HTML contains embedded JSON in a data-app-data
        attribute with HTML-encoded content (&quot; for quotes).

        Tries multiple extraction patterns for robustness:
        1. data-app-data attribute (primary)
        2. <script id="application-data"> tag (fallback)
        3. Other common patterns

        Args:
            html_content: The HTML content string.

        Returns:
            Parsed JSON data as dict.

        Raises:
            ArtifactParseError: If data cannot be extracted or parsed.
        """
        import html as html_module
        import re

        # Pattern 1: data-app-data attribute (most common)
        # Handle both single and multiline with greedy matching
        match = re.search(r'data-app-data="([^"]*(?:\\"[^"]*)*)"', html_content, re.DOTALL)
        if match:
            encoded_json = match.group(1)
            decoded_json = html_module.unescape(encoded_json)

            try:
                data = json.loads(decoded_json)
                logger.debug("Extracted app data using data-app-data attribute")
                return data
            except json.JSONDecodeError as e:
                # Log the error but try other patterns
                logger.debug(f"Failed to parse data-app-data JSON: {e}")
                logger.debug(f"JSON preview: {decoded_json[:200]}...")

        # Pattern 2: <script id="application-data"> tag
        match = re.search(
            r'<script[^>]+id=["\']application-data["\'][^>]*>(.*?)</script>',
            html_content,
            re.DOTALL
        )
        if match:
            try:
                data = json.loads(match.group(1))
                logger.debug("Extracted app data using script tag")
                return data
            except json.JSONDecodeError as e:
                logger.debug(f"Failed to parse script tag JSON: {e}")

        # Pattern 3: data-state or data-config attributes (additional fallback)
        for attr in ['data-state', 'data-config', 'data-initial-state']:
            match = re.search(rf'{attr}="([^"]*(?:\\"[^"]*)*)"', html_content, re.DOTALL)
            if match:
                encoded_json = match.group(1)
                decoded_json = html_module.unescape(encoded_json)
                try:
                    data = json.loads(decoded_json)
                    logger.debug(f"Extracted app data using {attr} attribute")
                    return data
                except json.JSONDecodeError:
                    continue

        # No patterns matched - provide detailed error
        html_preview = html_content[:500] if html_content else "Empty"
        logger.error(f"Failed to extract app data. HTML preview: {html_preview}...")

        raise ArtifactParseError(
            "interactive",
            details=(
                "Could not extract JSON data from HTML. "
                "Tried: data-app-data, script#application-data, data-state, data-config"
            )
        )

    @staticmethod
    def _format_quiz_markdown(title: str, questions: list[dict]) -> str:
        """Format quiz as markdown.

        Args:
            title: Quiz title.
            questions: List of question dicts with 'question', 'answerOptions', 'hint'.

        Returns:
            Formatted markdown string.
        """
        lines = [f"# {title}", ""]

        for i, q in enumerate(questions, 1):
            lines.append(f"## Question {i}")
            lines.append(q.get("question", ""))
            lines.append("")

            for opt in q.get("answerOptions", []):
                marker = "[x]" if opt.get("isCorrect") else "[ ]"
                lines.append(f"- {marker} {opt.get('text', '')}")

            if q.get("hint"):
                lines.append("")
                lines.append(f"**Hint:** {q['hint']}")

            lines.append("")

        return "\n".join(lines)

    @staticmethod
    def _format_flashcards_markdown(title: str, cards: list[dict]) -> str:
        """Format flashcards as markdown.

        Args:
            title: Flashcard deck title.
            cards: List of card dicts with 'f' (front) and 'b' (back).

        Returns:
            Formatted markdown string.
        """
        lines = [f"# {title}", ""]

        for i, card in enumerate(cards, 1):
            front = card.get("f", "")
            back = card.get("b", "")

            lines.append(f"## Card {i}")
            lines.append("")
            lines.append(f"**Front:** {front}")
            lines.append("")
            lines.append(f"**Back:** {back}")
            lines.append("")
            lines.append("---")
            lines.append("")

        return "\n".join(lines)

    def _format_interactive_content(
        self,
        app_data: dict,
        title: str,
        output_format: str,
        html_content: str,
        is_quiz: bool,
    ) -> str:
        """Format quiz or flashcard content for output.

        Args:
            app_data: Parsed JSON data from HTML.
            title: Artifact title.
            output_format: Output format - json, markdown, or html.
            html_content: Original HTML content.
            is_quiz: True for quiz, False for flashcards.

        Returns:
            Formatted content string.
        """
        if output_format == "html":
            return html_content

        if is_quiz:
            questions = app_data.get("quiz", [])
            if output_format == "markdown":
                return self._format_quiz_markdown(title, questions)
            return json.dumps({"title": title, "questions": questions}, indent=2)

        # Flashcards
        cards = app_data.get("flashcards", [])
        if output_format == "markdown":
            return self._format_flashcards_markdown(title, cards)

        # Normalize JSON format: {"f": "...", "b": "..."} -> {"front": "...", "back": "..."}
        normalized = [{"front": c.get("f", ""), "back": c.get("b", "")} for c in cards]
        return json.dumps({"title": title, "cards": normalized}, indent=2)

    async def _download_interactive_artifact(
        self,
        notebook_id: str,
        output_path: str,
        artifact_type: str,
        is_quiz: bool,
        artifact_id: str | None = None,
        output_format: str = "json",
    ) -> str:
        """Shared implementation for downloading quiz/flashcard artifacts.

        Args:
            notebook_id: The notebook ID.
            output_path: Path to save the file.
            artifact_type: Human-readable type for error messages ("quiz" or "flashcards").
            is_quiz: True for quiz, False for flashcards.
            artifact_id: Specific artifact ID, or uses first completed artifact.
            output_format: Output format - json, markdown, or html.

        Returns:
            The output path where the file was saved.

        Raises:
            ValueError: If invalid output_format.
            ArtifactNotReadyError: If no completed artifact found.
            ArtifactParseError: If content parsing fails.
            ArtifactDownloadError: If content fetch fails.
        """
        # Validate format
        valid_formats = ("json", "markdown", "html")
        if output_format not in valid_formats:
            raise ValueError(
                f"Invalid output_format: {output_format!r}. "
                f"Use one of: {', '.join(valid_formats)}"
            )

        # Get all artifacts and filter for completed interactive artifacts
        artifacts = self._list_raw(notebook_id)

        # Type 4 (STUDIO_TYPE_FLASHCARDS) covers both quizzes and flashcards
        # Status 3 = completed
        candidates = [
            a for a in artifacts
            if isinstance(a, list) and len(a) > 4
            and a[2] == self.STUDIO_TYPE_FLASHCARDS
            and a[4] == 3
        ]

        if not candidates:
            raise ArtifactNotReadyError(artifact_type)

        # Select artifact by ID or use most recent
        if artifact_id:
            target = next((a for a in candidates if a[0] == artifact_id), None)
            if not target:
                raise ArtifactNotReadyError(artifact_type, artifact_id)
        else:
            target = candidates[0]  # Most recent

        # Fetch HTML content
        html_content = self._get_artifact_content(notebook_id, target[0])
        if not html_content:
            raise ArtifactDownloadError(
                artifact_type,
                details="Failed to fetch HTML content from API"
            )

        # Extract and parse embedded JSON
        try:
            app_data = self._extract_app_data(html_content)
        except ArtifactParseError:
            raise  # Re-raise as-is
        except (ValueError, json.JSONDecodeError) as e:
            raise ArtifactParseError(artifact_type, details=str(e)) from e

        # Get title from artifact metadata
        default_title = f"Untitled {artifact_type.title()}"
        title = target[1] if len(target) > 1 and target[1] else default_title

        # Format content
        content = self._format_interactive_content(
            app_data, title, output_format, html_content, is_quiz
        )

        # Write to file
        output = Path(output_path)
        output.parent.mkdir(parents=True, exist_ok=True)
        output.write_text(content, encoding="utf-8")

        logger.info(f"Downloaded {artifact_type} to {output} ({output_format} format)")
        return str(output)

    async def download_quiz(
        self,
        notebook_id: str,
        output_path: str,
        artifact_id: str | None = None,
        output_format: str = "json",
    ) -> str:
        """Download quiz artifact.

        Args:
            notebook_id: The notebook ID.
            output_path: Path to save the file.
            artifact_id: Specific artifact ID, or uses first completed quiz.
            output_format: Output format - json, markdown, or html (default: json).

        Returns:
            The output path where the file was saved.

        Raises:
            ValueError: If invalid output_format.
            ArtifactNotReadyError: If no completed quiz found.
            ArtifactParseError: If content parsing fails.
        """
        return await self._download_interactive_artifact(
            notebook_id=notebook_id,
            output_path=output_path,
            artifact_type="quiz",
            is_quiz=True,
            artifact_id=artifact_id,
            output_format=output_format,
        )

    async def download_flashcards(
        self,
        notebook_id: str,
        output_path: str,
        artifact_id: str | None = None,
        output_format: str = "json",
    ) -> str:
        """Download flashcard deck artifact.

        Args:
            notebook_id: The notebook ID.
            output_path: Path to save the file.
            artifact_id: Specific artifact ID, or uses first completed flashcard deck.
            output_format: Output format - json, markdown, or html (default: json).

        Returns:
            The output path where the file was saved.

        Raises:
            ValueError: If invalid output_format.
            ArtifactNotReadyError: If no completed flashcards found.
            ArtifactParseError: If content parsing fails.
        """
        return await self._download_interactive_artifact(
            notebook_id=notebook_id,
            output_path=output_path,
            artifact_type="flashcards",
            is_quiz=False,
            artifact_id=artifact_id,
            output_format=output_format,
        )

    def close(self) -> None:
        """Close the HTTP client."""
        if self._client:
            self._client.close()
            self._client = None

if __name__ == "__main__":
    import sys

    print("NotebookLM MCP API POC")
    print("=" * 50)
    print()
    print("To use this POC, you need to:")
    print("1. Go to notebooklm.google.com in Chrome")
    print("2. Open DevTools > Network tab")
    print("3. Find a request to notebooklm.google.com")
    print("4. Copy the entire Cookie header value")
    print()
    print("Then run:")
    print("  python notebooklm_mcp.py 'YOUR_COOKIE_HEADER'")
    print()

    if len(sys.argv) > 1:
        cookie_header = sys.argv[1]
        cookies = extract_cookies_from_chrome_export(cookie_header)

        print(f"Extracted {len(cookies)} cookies")
        print()

        # Session tokens - these need to be extracted from the page
        # To get these:
        # 1. Go to notebooklm.google.com in Chrome
        # 2. Open DevTools > Network tab
        # 3. Find any POST request to /_/LabsTailwindUi/data/batchexecute
        # 4. CSRF token: Look for 'at=' parameter in the request body
        # 5. Session ID: Look for 'f.sid=' parameter in the URL
        #
        # These tokens are session-specific and expire after some time.
        # For automated use, you'd need to extract them from the page's JavaScript.

        # Get tokens from environment or use defaults (update these if needed)
        import os
        csrf_token = os.environ.get(
            "NOTEBOOKLM_CSRF_TOKEN",
            "ACi2F2OxJshr6FHHGUtehylr0NVT:1766372302394"  # Update this
        )
        session_id = os.environ.get(
            "NOTEBOOKLM_SESSION_ID",
            "1975517010764758431"  # Update this
        )

        print(f"Using CSRF token: {csrf_token[:20]}...")
        print(f"Using session ID: {session_id}")
        print()

        client = NotebookLMClient(cookies, csrf_token=csrf_token, session_id=session_id)

        try:
            # Demo: List notebooks
            print("Listing notebooks...")
            print()

            notebooks = client.list_notebooks(debug=False)

            print(f"Found {len(notebooks)} notebooks:")
            for nb in notebooks[:5]:  # Limit output
                print(f"  - {nb.title}")
                print(f"    ID: {nb.id}")
                print(f"    URL: {nb.url}")
                print(f"    Sources: {nb.source_count}")
                print()

            # Demo: Create a notebook (commented out to avoid creating test notebooks)
            # print("Creating a new notebook...")
            # new_nb = client.create_notebook(title="Test Notebook from API")
            # if new_nb:
            #     print(f"Created notebook: {new_nb.title}")
            #     print(f"  ID: {new_nb.id}")
            #     print(f"  URL: {new_nb.url}")

        except Exception as e:
            import traceback
            traceback.print_exc()
            print(f"Error: {e}")
        finally:
            client.close()
