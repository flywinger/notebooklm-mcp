import time
import json
import logging
from pathlib import Path
from typing import Any

from notebooklm_tools.core.exceptions import NLMError
from notebooklm_tools.utils import cdp
from notebooklm_tools.core.auth import AuthManager

logger = logging.getLogger(__name__)

class BrowserUploader:
    """Handles file uploads via Chrome automation using persistent profile.

    This class uses the same Chrome profile that was used during login,
    which already contains the necessary authentication cookies. No cookie
    injection is needed - Chrome loads them from its profile automatically.
    """

    def __init__(self, profile_name: str = "default", headless: bool = False):
        """Initialize the browser uploader.

        Args:
            profile_name: Name of the auth profile to use
            headless: Whether to use headless Chrome (default: False for better compatibility)
        """
        self.auth_manager = AuthManager(profile_name)
        self.port = cdp.CDP_DEFAULT_PORT
        self.ws_url: str | None = None
        self._chrome_launched = False
        self.headless = headless

    def _ensure_browser(self):
        """Ensure Chrome is running and we are connected.

        Uses the persistent Chrome profile from login, which already has cookies.
        Does NOT inject cookies - relies on Chrome's native cookie storage.
        """
        if self.ws_url:
            return

        # 1. Try to connect to existing Chrome
        existing_port = cdp.find_existing_nlm_chrome()
        if existing_port:
            self.port = existing_port
            logger.info(f"Connected to existing Chrome on port {self.port}")
        else:
            # 2. Check if profile is locked by a stale Chrome instance
            if cdp.is_profile_locked():
                logger.warning(
                    "Chrome profile is locked but no Chrome instance found. "
                    "This may be a stale lock. If upload fails, delete the SingletonLock file."
                )

            # 3. Launch new Chrome with persistent profile
            # Note: Using headless=False by default for better cookie compatibility
            logger.info(f"Launching Chrome ({'headless' if self.headless else 'visible'})...")
            if not cdp.launch_chrome(self.port, headless=self.headless):
                raise NLMError("Failed to launch Chrome for file upload")
            self._chrome_launched = True
            time.sleep(3) # Wait for startup

        # 4. Find/Create NotebookLM page
        page = cdp.find_or_create_notebooklm_page(self.port)
        if not page:
            raise NLMError("Failed to open NotebookLM page")

        self.ws_url = page.get("webSocketDebuggerUrl")
        if not self.ws_url:
            raise NLMError("Failed to get WebSocket debugger URL")

    def upload_file(self, notebook_id: str, file_path: str | Path) -> bool:
        """Upload a file to a notebook.

        Args:
            notebook_id: The notebook ID to upload to
            file_path: Path to the file to upload

        Returns:
            True if upload succeeded

        Raises:
            NLMError: If upload fails or authentication is required
        """
        file_path = Path(file_path).absolute()
        if not file_path.exists():
            raise NLMError(f"File not found: {file_path}")

        self._ensure_browser()

        url = f"https://notebooklm.google.com/notebook/{notebook_id}"
        logger.info(f"Navigating to {url}...")
        cdp.navigate_to_url(self.ws_url, url)

        # Check if we were redirected to login or error page
        current_url = self._execute_script("window.location.href")
        if "accounts.google.com" in current_url:
            error_msg = "Redirected to Google login page. "
            if "CookieMismatch" in current_url:
                error_msg += "Cookie mismatch detected. "
            raise NLMError(
                error_msg +
                "Your session may have expired or the profile cookies are stale. "
                "Please run 'nlm login' or 'notebooklm-mcp-auth' again to re-authenticate."
            )

        # Wait for page to load
        time.sleep(2)

        # Debug: Check what's on the page
        logger.info("Checking page structure...")
        page_text = self._execute_script("document.body.innerText.substring(0, 500)")
        logger.info(f"Page text preview: {page_text[:200]}")

        # Look for the Add sources button - try multiple strategies
        logger.info("Looking for 'Add sources' button...")
        found = self._execute_script("""
            (function() {
                // Strategy 1: Look for button with specific text
                const buttons = Array.from(document.querySelectorAll('button, [role=button]'));
                const addButton = buttons.find(b =>
                    b.textContent.toLowerCase().includes('add source') ||
                    b.textContent.toLowerCase().includes('upload') ||
                    b.getAttribute('aria-label')?.toLowerCase().includes('add source')
                );
                if (addButton) {
                    console.log('Found add sources button:', addButton);
                    return true;
                }

                // Log what buttons we found
                console.log('Available buttons:', buttons.slice(0, 10).map(b => ({
                    text: b.textContent.substring(0, 50),
                    ariaLabel: b.getAttribute('aria-label'),
                    className: b.className
                })));
                return false;
            })()
        """)

        if not found:
            # Log current URL for debugging
            current_url = self._execute_script("window.location.href")
            # Get all button text for debugging
            button_info = self._execute_script("""
                Array.from(document.querySelectorAll('button, [role=button]'))
                    .slice(0, 20)
                    .map(b => b.textContent.substring(0, 100))
                    .join(' | ')
            """)
            logger.error(f"Available buttons: {button_info}")
            raise NLMError(
                f"Could not find 'Add source' button after navigating to notebook. "
                f"Current URL: {current_url}. "
                f"Ensure the notebook exists and you have access. "
                f"Available buttons logged above."
            )

        # Click Add Source
        logger.info("Clicking 'Add source'...")
        clicked = self._execute_script("""
            (function() {
                 const btn = document.querySelector('.add-source-button') ||
                             document.querySelector('.upload-button') ||
                             document.querySelector('button[aria-label="Add sources"]');
                 if (btn) {
                     btn.click();
                     return true;
                 }
                 // Fallback: search by text
                 const elements = document.querySelectorAll('button, div[role=button]');
                 for(const el of elements) {
                     if(el.textContent.includes('Add source')) {
                         el.click();
                         return true;
                     }
                 }
                 return false;
            })()
        """)

        if not clicked:
            raise NLMError("Could not find 'Add source' button. Ensure notebook exists and you have access.")
            
        time.sleep(1) # Wait for menu
        
        # Click PDF/File option in menu
        logger.info("Clicking file upload option...")
        clicked_upload = self._execute_script("""
            (function() {
                const options = document.querySelectorAll('button, [role=menuitem]');
                for(const el of options) {
                    if(el.textContent.includes('PDF') || el.textContent.includes('File') || el.textContent.includes('Upload')) {
                        console.log('Clicking upload option:', el.textContent);
                        el.click();
                        return true;
                    }
                }
                console.log('Upload option not found in menu');
                return false;
            })()
        """)

        if not clicked_upload:
            logger.warning("Could not find PDF/File upload option in menu")

        # Wait for input to be created
        time.sleep(2)

        # Find input element - try multiple approaches
        logger.info("Looking for file input element...")

        # First check if input exists
        input_exists = self._execute_script("!!document.querySelector('input[type=file]')")
        logger.info(f"File input exists: {input_exists}")

        if not input_exists:
            # Log all inputs for debugging
            all_inputs = self._execute_script("""
                Array.from(document.querySelectorAll('input'))
                    .map(i => ({type: i.type, id: i.id, name: i.name, style: i.style.display}))
            """)
            logger.error(f"All inputs on page: {all_inputs}")
            raise NLMError(
                "File input element not found after clicking upload options. "
                "The NotebookLM UI may have changed."
            )

        # Find input logic
        try:
            root = cdp.get_document_root(self.ws_url)
            input_node_id = cdp.query_selector(self.ws_url, root["nodeId"], "input[type=file]")

            if not input_node_id:
                raise NLMError("File input element found in DOM but CDP query failed")
                
            # Set file
            logger.info(f"Uploading {file_path.name}...")
            cdp.execute_cdp_command(self.ws_url, "DOM.setFileInputFiles", {
                "files": [str(file_path)],
                "nodeId": input_node_id
            })
            
            # Trigger change event
            self._execute_script("document.querySelector('input[type=file]').dispatchEvent(new Event('change', {bubbles: true}))")
            
            # Wait for upload to complete (dumb wait for now, ideal would be to watch for progress)
            # NotebookLM shows a "Uploading..." toast. 
            # We can return true immediately and let user check status, or wait.
            # Let's wait a bit.
            time.sleep(5) 
            return True
            
        except Exception as e:
            raise NLMError(f"Upload failed: {e}")

    def _execute_script(self, script: str) -> Any:
        res = cdp.execute_cdp_command(self.ws_url, "Runtime.evaluate", {
            "expression": script,
            "returnByValue": True
        })
        return res.get("result", {}).get("value")

    def _wait_for_selector(self, selector: str, timeout: int = 30) -> bool:
        """Wait for an element to appear.

        Args:
            selector: CSS selector to wait for
            timeout: Maximum time to wait in seconds

        Returns:
            True if element appeared, False if timeout
        """
        start = time.time()
        while time.time() - start < timeout:
            # Check if element exists
            found = self._execute_script(f"!!document.querySelector('{selector}')")
            if found:
                return True

            # Also check if we got redirected to an error page
            current_url = self._execute_script("window.location.href")
            if "accounts.google.com" in current_url:
                logger.error(f"Redirected to Google accounts page: {current_url}")
                return False

            time.sleep(0.5)

        # Log debug info on failure
        current_url = self._execute_script("window.location.href")
        logger.error(f"Timeout waiting for selector '{selector}'. Current URL: {current_url}")
        return False

    def close(self):
        """Close browser if we launched it."""
        if self._chrome_launched:
            cdp.terminate_chrome()
