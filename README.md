# NotebookLM Consumer MCP Server

An MCP server for **Consumer NotebookLM** (notebooklm.google.com) - the free/personal tier.

> **Note:** This is NOT for NotebookLM Enterprise (Vertex AI). Those are completely separate systems.

## Features

| Tool | Description |
|------|-------------|
| `notebook_list` | List all notebooks |
| `notebook_create` | Create a new notebook |
| `notebook_get` | Get notebook details with sources |
| `notebook_rename` | Rename a notebook |
| `notebook_delete` | Delete a notebook (requires confirmation) |
| `notebook_add_url` | Add URL/YouTube as source |
| `notebook_add_text` | Add pasted text as source |
| `notebook_add_drive` | Add Google Drive document as source |
| `notebook_query` | Ask questions and get AI answers |
| `source_list_drive` | List sources with freshness status |
| `source_sync_drive` | Sync stale Drive sources (requires confirmation) |
| `research_start` | Start Web or Drive research to discover sources |
| `research_status` | Poll research progress with built-in wait |
| `research_import` | Import discovered sources into notebook |
| `audio_overview_create` | Generate audio podcasts (requires confirmation) |
| `video_overview_create` | Generate video overviews (requires confirmation) |
| `studio_status` | Check audio/video generation status |
| `studio_delete` | Delete audio/video overviews (requires confirmation) |
| `save_auth_tokens` | Save cookies for authentication |

## Important Disclaimer

This MCP uses **reverse-engineered internal APIs** that:
- Are undocumented and may change without notice
- May violate Google's Terms of Service
- Require cookie extraction from your browser

Use at your own risk for personal/experimental purposes.

## Installation

```bash
# Clone the repository
git clone https://github.com/jacob-bd/notebooklm-consumer-mcp.git
cd notebooklm-consumer-mcp

# Install with uv
uv tool install .
```

## Authentication Setup (Simplified!)

**You only need to extract cookies once** - they last for weeks. The CSRF token and session ID are automatically extracted when needed.

### Option 1: Using Chrome DevTools MCP (Recommended)

If your AI assistant has Chrome DevTools MCP available:

1. Navigate to `notebooklm.google.com` in Chrome
2. Ask your assistant to extract cookies from any network request
3. Call `save_auth_tokens(cookies=<cookie_header>)`

That's it! Cookies are cached to `~/.notebooklm-consumer/auth.json`.

### Option 2: Manual Extraction

1. Go to `notebooklm.google.com` in Chrome and log in
2. Open DevTools (F12) > Network tab
3. Refresh or perform any action
4. Find any request to `notebooklm.google.com`
5. Copy the entire `Cookie` header value
6. Set environment variable:

```bash
export NOTEBOOKLM_COOKIES="SID=xxx; HSID=xxx; SSID=xxx; ..."
```

> **Note:** You no longer need to extract CSRF token or session ID manually - they are auto-extracted from the page when the MCP starts.

## MCP Configuration

### Claude Code

Add to `~/.claude.json`:

```json
{
  "mcpServers": {
    "notebooklm-consumer": {
      "command": "notebooklm-consumer-mcp"
    }
  }
}
```

### Cursor

Add to `~/.cursor/mcp.json`:

```json
{
  "mcpServers": {
    "notebooklm-consumer": {
      "command": "/path/to/notebooklm-consumer-mcp",
      "args": []
    }
  }
}
```

### Gemini CLI

Add to `~/.gemini/settings.json` under `mcpServers`:

```json
"notebooklm-consumer": {
  "command": "/path/to/notebooklm-consumer-mcp",
  "args": []
}
```

No environment variables needed - the MCP uses cached tokens from `~/.notebooklm-consumer/auth.json`.

## Usage Examples

### List Notebooks
```python
notebooks = notebook_list()
```

### Create and Query
```python
# Create a notebook
notebook = notebook_create(title="Research Project")

# Add sources
notebook_add_url(notebook_id, url="https://example.com/article")
notebook_add_text(notebook_id, text="My research notes...", title="Notes")

# Ask questions
result = notebook_query(notebook_id, query="What are the key points?")
print(result["answer"])
```

### Sync Stale Drive Sources
```python
# Check which sources need syncing
sources = source_list_drive(notebook_id)

# Sync stale sources (after user confirmation)
source_sync_drive(source_ids=["id1", "id2"], confirm=True)
```

### Research and Import Sources
```python
# Start web research (fast mode, ~30 seconds)
result = research_start(
    query="value of ISVs on cloud marketplaces",
    source="web",   # or "drive" for Google Drive
    mode="fast",    # or "deep" for extended research (web only)
    title="ISV Research"
)
notebook_id = result["notebook_id"]

# Poll until complete (built-in wait, polls every 30s for up to 5 min)
status = research_status(notebook_id)

# Import all discovered sources
research_import(
    notebook_id=notebook_id,
    task_id=status["research"]["task_id"]
)

# Or import specific sources by index
research_import(
    notebook_id=notebook_id,
    task_id=status["research"]["task_id"],
    source_indices=[0, 2, 5]  # Import only sources at indices 0, 2, and 5
)
```

**Research Modes:**
- `fast` + `web`: Quick web search, ~10 sources in ~30 seconds
- `deep` + `web`: Extended research with AI report, ~40 sources in 3-5 minutes
- `fast` + `drive`: Quick Google Drive search, ~10 sources in ~30 seconds

### Generate Audio/Video Overviews
```python
# Create an audio overview (podcast)
result = audio_overview_create(
    notebook_id=notebook_id,
    format="deep_dive",  # deep_dive, brief, critique, debate
    length="default",    # short, default, long
    language="en",
    confirm=True         # Required - show settings first, then confirm
)

# Create a video overview
result = video_overview_create(
    notebook_id=notebook_id,
    format="explainer",      # explainer, brief
    visual_style="classic",  # auto_select, classic, whiteboard, kawaii, anime, etc.
    language="en",
    confirm=True
)

# Check generation status (takes several minutes)
status = studio_status(notebook_id)
for artifact in status["artifacts"]:
    print(f"{artifact['title']}: {artifact['status']}")
    if artifact["audio_url"]:
        print(f"  Audio: {artifact['audio_url']}")
    if artifact["video_url"]:
        print(f"  Video: {artifact['video_url']}")

# Delete an artifact (after user confirmation)
studio_delete(
    notebook_id=notebook_id,
    artifact_id="artifact-uuid",
    confirm=True
)
```

**Audio Formats:** deep_dive (conversation), brief, critique, debate
**Audio Lengths:** short, default, long
**Video Formats:** explainer, brief
**Video Styles:** auto_select, classic, whiteboard, kawaii, anime, watercolor, retro_print, heritage, paper_craft

## Consumer vs Enterprise

| Feature | Consumer | Enterprise |
|---------|----------|------------|
| URL | notebooklm.google.com | vertexaisearch.cloud.google.com |
| Auth | Browser cookies | Google Cloud ADC |
| API | Internal RPCs | Discovery Engine API |
| Notebooks | Personal | Separate system |
| Audio Overviews | Yes | Yes |
| Video Overviews | Yes | No |
| Mind Maps | Yes | No |
| Flashcards/Quizzes | Yes | No |

## Authentication Lifecycle

| Component | Duration | Refresh |
|-----------|----------|---------|
| Cookies | ~2-4 weeks | Re-extract from Chrome when expired |
| CSRF Token | Per MCP session | Auto-extracted on MCP start |
| Session ID | Per MCP session | Auto-extracted on MCP start |

When cookies expire, you'll see an auth error. Just extract fresh cookies and call `save_auth_tokens()` again.

## Limitations

- **Rate limits**: Free tier has ~50 queries/day
- **No official support**: API may change without notice
- **Cookie expiration**: Need to re-extract cookies every few weeks

## Contributing

See [CLAUDE.md](CLAUDE.md) for detailed API documentation and how to add new features.

## License

MIT License
