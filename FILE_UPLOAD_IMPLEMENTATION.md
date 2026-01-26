# File Upload Implementation Summary

## Problem

The file upload feature was failing with a **CookieMismatch** error from Google. Even though valid cookies were being injected via CDP, Google detected the mismatch between:
1. The session cookies (created in one browser environment)
2. The new browser fingerprint/profile (fresh Chrome instance)

## Root Cause

The previous implementation:
1. Launched Chrome with a persistent profile directory ✓
2. **Extracted cookies from that session and stored them in JSON**
3. **Injected those cookies back via CDP `Network.setCookie`** ✗

The cookie injection approach caused Google to detect suspicious activity because:
- Cookies injected via CDP don't match the browser's natural cookie storage
- Google's security checks detect the fingerprint mismatch
- Headless Chrome has different behavior than regular Chrome

## Solution

### Key Changes

#### 1. Removed Cookie Injection ([uploader.py](src/notebooklm_tools/core/uploader.py))

**Before:**
```python
def _ensure_browser(self):
    # ... launch Chrome ...
    self._inject_cookies()  # ❌ Injecting cookies via CDP

def _inject_cookies(self):
    for name, value in cookies.items():
        cdp.execute_cdp_command(self.ws_url, "Network.setCookie", {...})
```

**After:**
```python
def _ensure_browser(self):
    """Ensure Chrome is running and we are connected.

    Uses the persistent Chrome profile from login, which already has cookies.
    Does NOT inject cookies - relies on Chrome's native cookie storage.
    """
    # ... launch Chrome with persistent profile ...
    # No cookie injection - Chrome loads them automatically from profile
```

#### 2. Non-Headless by Default

Changed default from headless Chrome to visible Chrome for better compatibility:

```python
def __init__(self, profile_name: str = "default", headless: bool = False):
    self.headless = headless  # Default: False (visible Chrome)
```

Headless mode can still be enabled explicitly if needed:
- CLI: `nlm source add --file doc.pdf --headless`
- Python: `uploader = BrowserUploader(headless=True)`

#### 3. Added Redirect Detection

Enhanced error handling to detect and report authentication issues:

```python
# Check if we were redirected to login or error page
current_url = self._execute_script("window.location.href")
if "accounts.google.com" in current_url:
    error_msg = "Redirected to Google login page. "
    if "CookieMismatch" in current_url:
        error_msg += "Cookie mismatch detected. "
    raise NLMError(
        error_msg +
        "Your session may have expired. Please run 'nlm login' again."
    )
```

#### 4. Wired Up CLI Command ([source.py](src/notebooklm_tools/cli/commands/source.py))

Added `--file` option to the `source add` command:

```bash
# Upload a file via CLI
nlm source add <notebook-id> --file document.pdf

# Use headless mode (experimental)
nlm source add <notebook-id> --file document.pdf --headless
```

## How It Works

### Authentication Flow

1. **Login** (`nlm login` or `notebooklm-mcp-auth`):
   - Launches Chrome with persistent profile: `~/.notebooklm-tools/chrome-profile`
   - User logs in to NotebookLM
   - Cookies are stored in Chrome's profile (automatic)
   - Cookies are also extracted and saved to `~/.notebooklm-tools/profiles/default/cookies.json` (for API calls)

2. **File Upload**:
   - Launches Chrome with the **same** persistent profile
   - Chrome automatically loads cookies from its profile storage (no injection!)
   - Browser navigates to the notebook
   - File upload UI is automated via CDP
   - Chrome is closed when done

### Profile Lock Handling

The implementation handles the Chrome profile lock (SingletonLock):
- If Chrome is already running with the profile (from login), it connects to that instance
- If profile is locked but no Chrome found, warns about stale lock
- If not locked, launches new Chrome instance

## Testing

### Prerequisites

1. **Install dependencies:**
   ```bash
   pip install websocket-client
   ```

2. **Authenticate:**
   ```bash
   nlm login
   # OR
   notebooklm-mcp-auth
   ```

### Run E2E Test

```bash
# Set environment variables
export NOTEBOOKLM_E2E=1
export NOTEBOOKLM_E2E_UPLOAD=1

# Run the test
uv run pytest tests/test_e2e.py::TestSourceOperations::test_upload_file -v
```

### Manual Testing via CLI

```bash
# Create a test file
echo "Test content" > test.txt

# Get or create a notebook
nlm notebook list
NOTEBOOK_ID="<your-notebook-id>"

# Upload the file
nlm source add $NOTEBOOK_ID --file test.txt

# Verify it was added
nlm source list $NOTEBOOK_ID
```

### Manual Testing via MCP

Use the MCP tool `notebook_add_file` (if exposed) or call via client:

```python
from notebooklm_tools.core.client import NotebookLMClient

client = NotebookLMClient()
result = client.upload_file(notebook_id="...", file_path="test.txt")
print(f"Upload success: {result}")
```

## Files Changed

1. **[src/notebooklm_tools/core/uploader.py](src/notebooklm_tools/core/uploader.py)**
   - Removed `_inject_cookies()` method
   - Changed default to non-headless Chrome
   - Added redirect detection and better error messages
   - Added profile lock warning

2. **[src/notebooklm_tools/core/client.py](src/notebooklm_tools/core/client.py)**
   - Updated `upload_file()` to pass profile name and headless flag
   - Added proper cleanup (`uploader.close()`)
   - Improved documentation

3. **[src/notebooklm_tools/cli/commands/source.py](src/notebooklm_tools/cli/commands/source.py)**
   - Added `--file` option to `source add` command
   - Added `--headless` flag for experimental headless mode
   - Added file existence check
   - Added progress messages

## Known Limitations

1. **Chrome Required**: File upload requires Google Chrome to be installed
2. **Session Persistence**: Cookies must be fresh from a recent login
3. **Profile Conflicts**: Can't upload if Chrome is using the profile elsewhere (unless we connect to that instance)
4. **Headless Issues**: Headless mode may have compatibility issues with Google's security checks

## Troubleshooting

### "Cookie mismatch detected"
- **Cause**: Session expired or profile cookies are stale
- **Fix**: Run `nlm login` or `notebooklm-mcp-auth` to re-authenticate

### "Profile is locked"
- **Cause**: Chrome is already running with the profile
- **Fix**: Close Chrome or use the existing instance (implementation auto-connects)

### "Failed to launch Chrome"
- **Cause**: Chrome not found or can't start
- **Fix**: Install Google Chrome, or check if already running

### Upload times out waiting for button
- **Cause**: UI selectors changed or notebook doesn't exist
- **Fix**: Verify notebook ID, check Chrome window for errors

## Future Improvements

1. **Headless Stability**: Investigate making headless mode more reliable
2. **Progress Tracking**: Add upload progress indicators
3. **Batch Upload**: Support uploading multiple files at once
4. **File Type Detection**: Auto-validate file types before upload
5. **Browser Choice**: Support other browsers (Firefox, Edge)
