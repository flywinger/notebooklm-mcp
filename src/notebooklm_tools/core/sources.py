"""SourceMixin - Source management operations.

This mixin provides source-related operations:
- check_source_freshness: Check if Drive source is up-to-date
- sync_drive_source: Sync a Drive source with latest content
- delete_source: Delete a source permanently
- get_notebook_sources_with_types: Get sources with type info
- add_url_source: Add URL/YouTube as source
- add_text_source: Add pasted text as source
- add_drive_source: Add Google Drive document as source
- upload_file: Upload local file via Chrome automation
- get_source_guide: Get AI-generated summary and keywords
- get_source_fulltext: Get raw text content of a source
"""

from typing import Any

import httpx

from .base import BaseClient, SOURCE_ADD_TIMEOUT
from . import constants


class SourceMixin(BaseClient):
    """Mixin for source management operations.
    
    This class inherits from BaseClient and provides all source-related
    operations. It is designed to be composed with other mixins via
    multiple inheritance in the final NotebookLMClient class.
    """

    def check_source_freshness(self, source_id: str) -> bool | None:
        """Check if a Drive source is fresh (up-to-date with Google Drive)."""
        client = self._get_client()

        params = [None, [source_id], [2]]
        body = self._build_request_body(self.RPC_CHECK_FRESHNESS, params)
        url = self._build_url(self.RPC_CHECK_FRESHNESS)

        response = client.post(url, content=body)
        response.raise_for_status()

        parsed = self._parse_response(response.text)
        result = self._extract_rpc_result(parsed, self.RPC_CHECK_FRESHNESS)

        # true = fresh, false = stale
        if result and isinstance(result, list) and len(result) > 0:
            inner = result[0] if result else []
            if isinstance(inner, list) and len(inner) >= 2:
                return inner[1]  # true = fresh, false = stale
        return None

    def sync_drive_source(self, source_id: str) -> dict | None:
        """Sync a Drive source with the latest content from Google Drive."""
        client = self._get_client()

        # Sync params: [null, ["source_id"], [2]]
        params = [None, [source_id], [2]]
        body = self._build_request_body(self.RPC_SYNC_DRIVE, params)
        url = self._build_url(self.RPC_SYNC_DRIVE)

        response = client.post(url, content=body)
        response.raise_for_status()

        parsed = self._parse_response(response.text)
        result = self._extract_rpc_result(parsed, self.RPC_SYNC_DRIVE)

        if result and isinstance(result, list) and len(result) > 0:
            source_data = result[0] if result else []
            if isinstance(source_data, list) and len(source_data) >= 3:
                source_id_result = source_data[0][0] if source_data[0] else None
                title = source_data[1] if len(source_data) > 1 else "Unknown"
                metadata = source_data[2] if len(source_data) > 2 else []

                synced_at = None
                if isinstance(metadata, list) and len(metadata) > 3:
                    sync_info = metadata[3]
                    if isinstance(sync_info, list) and len(sync_info) > 1:
                        ts = sync_info[1]
                        if isinstance(ts, list) and len(ts) > 0:
                            synced_at = ts[0]

                return {
                    "id": source_id_result,
                    "title": title,
                    "synced_at": synced_at,
                }
        return None

    def delete_source(self, source_id: str) -> bool:
        """Delete a source from a notebook permanently.

        WARNING: This action is IRREVERSIBLE. The source will be permanently
        deleted from the notebook.

        Args:
            source_id: The source UUID to delete

        Returns:
            True on success, False on failure
        """
        client = self._get_client()

        # Delete source params: [[["source_id"]], [2]]
        # Note: Extra nesting compared to delete_notebook
        params = [[[source_id]], [2]]
        body = self._build_request_body(self.RPC_DELETE_SOURCE, params)
        url = self._build_url(self.RPC_DELETE_SOURCE)

        response = client.post(url, content=body)
        response.raise_for_status()

        parsed = self._parse_response(response.text)
        result = self._extract_rpc_result(parsed, self.RPC_DELETE_SOURCE)

        # Response is typically [] on success
        return result is not None

    def get_notebook_sources_with_types(self, notebook_id: str) -> list[dict]:
        """Get all sources from a notebook with their type information."""
        result = self.get_notebook(notebook_id)

        sources = []
        # The notebook data is wrapped in an outer array
        if result and isinstance(result, list) and len(result) >= 1:
            notebook_data = result[0] if isinstance(result[0], list) else result
            # Sources are in notebook_data[1]
            sources_data = notebook_data[1] if len(notebook_data) > 1 else []

            if isinstance(sources_data, list):
                for src in sources_data:
                    if isinstance(src, list) and len(src) >= 3:
                        # Source structure: [[id], title, [metadata...], [null, 2]]
                        source_id = src[0][0] if src[0] and isinstance(src[0], list) else None
                        title = src[1] if len(src) > 1 else "Untitled"
                        metadata = src[2] if len(src) > 2 else []

                        source_type = None
                        drive_doc_id = None
                        if isinstance(metadata, list):
                            if len(metadata) > 4:
                                source_type = metadata[4]
                            # Drive doc info at metadata[0]
                            if len(metadata) > 0 and isinstance(metadata[0], list):
                                drive_doc_id = metadata[0][0] if metadata[0] else None

                        # Google Docs (type 1) and Slides/Sheets (type 2) are stored in Drive
                        # and can be synced if they have a drive_doc_id
                        can_sync = drive_doc_id is not None and source_type in (
                            self.SOURCE_TYPE_GOOGLE_DOCS,
                            self.SOURCE_TYPE_GOOGLE_OTHER,
                        )

                        # Extract URL if available (position 7)
                        url = None
                        if isinstance(metadata, list) and len(metadata) > 7:
                            url_info = metadata[7]
                            if isinstance(url_info, list) and len(url_info) > 0:
                                url = url_info[0]

                        sources.append({
                            "id": source_id,
                            "title": title,
                            "source_type": source_type,
                            "source_type_name": constants.SOURCE_TYPES.get_name(source_type),
                            "url": url,
                            "drive_doc_id": drive_doc_id,
                            "can_sync": can_sync,  # True for Drive docs AND Gemini Notes
                        })

        return sources

    def add_url_source(self, notebook_id: str, url: str) -> dict | None:
        """Add a URL (website or YouTube) as a source to a notebook."""
        client = self._get_client()

        # URL position differs for YouTube vs regular websites:
        # - YouTube: position 7
        # - Regular websites: position 2
        is_youtube = "youtube.com" in url.lower() or "youtu.be" in url.lower()

        if is_youtube:
            # YouTube: [null, null, null, null, null, null, null, [url], null, null, 1]
            source_data = [None, None, None, None, None, None, None, [url], None, None, 1]
        else:
            # Regular website: [null, null, [url], null, null, null, null, null, null, null, 1]
            source_data = [None, None, [url], None, None, None, None, None, None, None, 1]

        params = [
            [source_data],
            notebook_id,
            [2],
            [1, None, None, None, None, None, None, None, None, None, [1]]
        ]
        body = self._build_request_body(self.RPC_ADD_SOURCE, params)
        source_path = f"/notebook/{notebook_id}"
        url_endpoint = self._build_url(self.RPC_ADD_SOURCE, source_path)

        try:
            response = client.post(url_endpoint, content=body, timeout=SOURCE_ADD_TIMEOUT)
            response.raise_for_status()
        except httpx.TimeoutException:
            # Large pages may take longer than the timeout but still succeed on backend
            return {
                "status": "timeout",
                "message": f"Operation timed out after {SOURCE_ADD_TIMEOUT}s but may have succeeded. Check notebook sources before retrying.",
            }

        parsed = self._parse_response(response.text)
        result = self._extract_rpc_result(parsed, self.RPC_ADD_SOURCE)

        if result and isinstance(result, list) and len(result) > 0:
            source_list = result[0] if result else []
            if source_list and len(source_list) > 0:
                source_data = source_list[0]
                source_id = source_data[0][0] if source_data[0] else None
                source_title = source_data[1] if len(source_data) > 1 else "Untitled"
                return {"id": source_id, "title": source_title}
        return None

    def add_text_source(self, notebook_id: str, text: str, title: str = "Pasted Text") -> dict | None:
        """Add pasted text as a source to a notebook."""
        client = self._get_client()

        # Text source params structure:
        source_data = [None, [title, text], None, 2, None, None, None, None, None, None, 1]
        params = [
            [source_data],
            notebook_id,
            [2],
            [1, None, None, None, None, None, None, None, None, None, [1]]
        ]
        body = self._build_request_body(self.RPC_ADD_SOURCE, params)
        source_path = f"/notebook/{notebook_id}"
        url_endpoint = self._build_url(self.RPC_ADD_SOURCE, source_path)

        try:
            response = client.post(url_endpoint, content=body, timeout=SOURCE_ADD_TIMEOUT)
            response.raise_for_status()
        except httpx.TimeoutException:
            return {
                "status": "timeout",
                "message": f"Operation timed out after {SOURCE_ADD_TIMEOUT}s but may have succeeded. Check notebook sources before retrying.",
            }

        parsed = self._parse_response(response.text)
        result = self._extract_rpc_result(parsed, self.RPC_ADD_SOURCE)

        if result and isinstance(result, list) and len(result) > 0:
            source_list = result[0] if result else []
            if source_list and len(source_list) > 0:
                source_data = source_list[0]
                source_id = source_data[0][0] if source_data[0] else None
                source_title = source_data[1] if len(source_data) > 1 else title
                return {"id": source_id, "title": source_title}
        return None

    def add_drive_source(
        self,
        notebook_id: str,
        document_id: str,
        title: str,
        mime_type: str = "application/vnd.google-apps.document"
    ) -> dict | None:
        """Add a Google Drive document as a source to a notebook."""
        client = self._get_client()

        # Drive source params structure (verified from network capture):
        source_data = [
            [document_id, mime_type, 1, title],  # Drive document info at position 0
            None,
            None,
            None,
            None,
            None,
            None,
            None,
            None,
            None,
            1
        ]
        params = [
            [source_data],
            notebook_id,
            [2],
            [1, None, None, None, None, None, None, None, None, None, [1]]
        ]
        body = self._build_request_body(self.RPC_ADD_SOURCE, params)
        source_path = f"/notebook/{notebook_id}"
        url_endpoint = self._build_url(self.RPC_ADD_SOURCE, source_path)

        try:
            response = client.post(url_endpoint, content=body, timeout=SOURCE_ADD_TIMEOUT)
            response.raise_for_status()
        except httpx.TimeoutException:
            # Large files may take longer than the timeout but still succeed on backend
            return {
                "status": "timeout",
                "message": f"Operation timed out after {SOURCE_ADD_TIMEOUT}s but may have succeeded. Check notebook sources before retrying.",
            }

        parsed = self._parse_response(response.text)
        result = self._extract_rpc_result(parsed, self.RPC_ADD_SOURCE)

        if result and isinstance(result, list) and len(result) > 0:
            source_list = result[0] if result else []
            if source_list and len(source_list) > 0:
                source_data = source_list[0]
                source_id = source_data[0][0] if source_data[0] else None
                source_title = source_data[1] if len(source_data) > 1 else title
                return {"id": source_id, "title": source_title}
        return None

    def upload_file(
        self,
        notebook_id: str,
        file_path: str,
        headless: bool = False,
        profile_name: str = "default",
    ) -> bool:
        """Upload a local file to a notebook using Chrome automation.

        This method uses the same Chrome profile that was used during login,
        which already contains authentication cookies. Chrome is launched
        (visible by default) to perform the upload via browser automation.

        Args:
            notebook_id: The notebook ID to upload to
            file_path: Path to the local file to upload
            headless: Whether to use headless Chrome (default: False for better compatibility)
            profile_name: Name of the profile to use (default: "default")

        Returns:
            True if upload succeeded

        Raises:
            RuntimeError: If BrowserUploader dependencies are missing
            NLMError: If upload fails or authentication is required
        """
        try:
            from notebooklm_tools.core.uploader import BrowserUploader
            uploader = BrowserUploader(profile_name=profile_name, headless=headless)
            try:
                return uploader.upload_file(notebook_id, file_path)
            finally:
                uploader.close()
        except ImportError as e:
            raise RuntimeError(
                "BrowserUploader not available. Install with: pip install websocket-client"
            ) from e

    def get_source_guide(self, source_id: str) -> dict[str, Any]:
        """Get AI-generated summary and keywords for a source."""
        result = self._call_rpc(self.RPC_GET_SOURCE_GUIDE, [[[[source_id]]]], "/")
        summary = ""
        keywords = []

        if result and isinstance(result, list):
            if len(result) > 0 and isinstance(result[0], list):
                if len(result[0]) > 0 and isinstance(result[0][0], list):
                    inner = result[0][0]

                    if len(inner) > 1 and isinstance(inner[1], list) and len(inner[1]) > 0:
                        summary = inner[1][0]

                    if len(inner) > 2 and isinstance(inner[2], list) and len(inner[2]) > 0:
                        keywords = inner[2][0] if isinstance(inner[2][0], list) else []

        return {
            "summary": summary,
            "keywords": keywords,
        }

    def get_source_fulltext(self, source_id: str) -> dict[str, Any]:
        """Get the full text content of a source.

        Returns the raw text content that was indexed from the source,
        along with metadata like title and source type.

        Args:
            source_id: The source UUID

        Returns:
            Dict with content, title, source_type, and char_count
        """
        # The hizoJc RPC returns source details including full text
        params = [[source_id], [2], [2]]
        result = self._call_rpc(self.RPC_GET_SOURCE, params, "/")

        content = ""
        title = ""
        source_type = ""
        url = None

        if result and isinstance(result, list):
            # Response structure:
            # result[0] = [[source_id], title, metadata, ...]
            # result[1] = null
            # result[2] = null
            # result[3] = [[content_blocks]]
            #
            # Each content block: [start_pos, end_pos, content_data, ...]

            # Extract from result[0] which contains source metadata
            if len(result) > 0 and isinstance(result[0], list):
                source_meta = result[0]

                # Title is at position 1
                if len(source_meta) > 1 and isinstance(source_meta[1], str):
                    title = source_meta[1]

                # Metadata is at position 2
                if len(source_meta) > 2 and isinstance(source_meta[2], list):
                    metadata = source_meta[2]
                    # Source type code is at position 4
                    if len(metadata) > 4:
                        type_code = metadata[4]
                        source_type = constants.SOURCE_TYPES.get_name(type_code)

                    # URL might be at position 7 for web sources
                    if len(metadata) > 7 and isinstance(metadata[7], list):
                        url_info = metadata[7]
                        if len(url_info) > 0 and isinstance(url_info[0], str):
                            url = url_info[0]

            # Extract content from result[3][0] - array of content blocks
            if len(result) > 3 and isinstance(result[3], list):
                content_wrapper = result[3]
                if len(content_wrapper) > 0 and isinstance(content_wrapper[0], list):
                    content_blocks = content_wrapper[0]
                    # Collect all text from content blocks
                    text_parts = []
                    for block in content_blocks:
                        if isinstance(block, list):
                            # Each block is [start, end, content_data, ...]
                            # Extract all text strings recursively
                            texts = self._extract_all_text(block)
                            text_parts.extend(texts)
                    content = "\n\n".join(text_parts)

        return {
            "content": content,
            "title": title,
            "source_type": source_type,
            "url": url,
            "char_count": len(content),
        }

    def _extract_all_text(self, data: list) -> list[str]:
        """Recursively extract all text strings from nested arrays."""
        texts = []
        for item in data:
            if isinstance(item, str) and len(item) > 0:
                texts.append(item)
            elif isinstance(item, list):
                texts.extend(self._extract_all_text(item))
        return texts
