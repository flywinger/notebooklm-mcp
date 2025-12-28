# NotebookLM Consumer MCP - Comprehensive Test Plan

**Purpose:** Verify all 30 MCP tools work correctly after optimization.

**Prerequisites:**
- MCP server installed: `uv cache clean && uv tool install --force .`
- Valid authentication cookies saved

---

## Test Group 1: Authentication & Basic Operations

### Test 1.1 - Save Auth Tokens
**Tool:** `save_auth_tokens`

**Prompt:**
```
I have cookies from Chrome DevTools. Let me save them using save_auth_tokens.
[Note: Use actual cookies from your browser session]
```

**Expected:** Success message with cache path.

---

### Test 1.2 - List Notebooks
**Tool:** `notebook_list`

**Prompt:**
```
List all my NotebookLM notebooks.
```

**Expected:** List of notebooks with counts (owned, shared).

---

### Test 1.3 - Create Notebook
**Tool:** `notebook_create`

**Prompt:**
```
Create a new notebook titled "MCP Test Notebook".
```

**Expected:** New notebook created with ID and URL.

**Save:** Note the `notebook_id` for subsequent tests.

---

### Test 1.4 - Get Notebook Details
**Tool:** `notebook_get`

**Prompt:**
```
Get the details of notebook [notebook_id from Test 1.3].
```

**Expected:** Notebook details with empty sources list, timestamps.

---

### Test 1.5 - Rename Notebook
**Tool:** `notebook_rename`

**Prompt:**
```
Rename notebook [notebook_id] to "MCP Test - Renamed".
```

**Expected:** Success with updated title.

---

## Test Group 2: Adding Sources

### Test 2.1 - Add URL Source
**Tool:** `notebook_add_url`

**Prompt:**
```
Add this URL to notebook [notebook_id]: https://en.wikipedia.org/wiki/Artificial_intelligence
```

**Expected:** Source added successfully.

---

### Test 2.2 - Add Text Source
**Tool:** `notebook_add_text`

**Prompt:**
```
Add this text as a source to notebook [notebook_id]:
Title: "Test Document"
Text: "This is a test document about machine learning. Machine learning is a subset of artificial intelligence that focuses on algorithms that can learn from data."
```

**Expected:** Text source added successfully.

---

### Test 2.3 - Add Drive Source (Optional - requires Drive doc)
**Tool:** `notebook_add_drive`

**Prompt:**
```
Add this Google Drive document to notebook [notebook_id]:
Document ID: [your_doc_id]
Title: "My Drive Doc"
Type: doc
```

**Expected:** Drive source added successfully (or skip if no Drive doc available).

---

### Test 2.4 - List Sources with Drive Status
**Tool:** `source_list_drive`

**Prompt:**
```
List all sources in notebook [notebook_id] and check their Drive freshness status.
```

**Expected:** List showing sources by type, Drive sources show freshness.

**Save:** Note a `source_id` for Test 2.5.

---

### Test 2.5 - Describe Source
**Tool:** `source_describe`

**Prompt:**
```
Get an AI-generated summary of source [source_id from Test 2.4].
```

**Expected:** AI summary with keywords/chips.

---

## Test Group 3: AI Features

### Test 3.1 - Describe Notebook
**Tool:** `notebook_describe`

**Prompt:**
```
Get an AI-generated summary of what notebook [notebook_id] is about.
```

**Expected:** AI summary with suggested topics.

---

### Test 3.2 - Query Notebook
**Tool:** `notebook_query`

**Prompt:**
```
Ask this question about notebook [notebook_id]: "What is artificial intelligence?"
```

**Expected:** AI answer with conversation_id.

---

### Test 3.3 - Configure Chat
**Tool:** `chat_configure`

**Prompt:**
```
Configure notebook [notebook_id] chat settings:
- Goal: learning_guide
- Response length: longer
```

**Expected:** Settings updated successfully.

---

## Test Group 4: Research

### Test 4.1 - Start Fast Research (Web)
**Tool:** `research_start`

**Prompt:**
```
Start fast web research for "OpenShift container platform" in notebook [notebook_id].
```

**Expected:** Research task started with task_id.

**Save:** Note the `task_id`.

---

### Test 4.2 - Check Research Status
**Tool:** `research_status`

**Prompt:**
```
Check the status of research for notebook [notebook_id]. Poll until complete.
```

**Expected:** Research completes with list of discovered sources.

---

### Test 4.3 - Import Research Sources
**Tool:** `research_import`

**Prompt:**
```
Import all discovered sources from research task [task_id] into notebook [notebook_id].
```

**Expected:** Sources imported successfully.

---

## Test Group 5: Studio - Audio/Video

### Test 5.1 - Create Audio Overview (with confirmation)
**Tool:** `audio_overview_create`

**Prompt:**
```
Create an audio overview for notebook [notebook_id]:
- Format: brief
- Length: short
- Language: en
Show me the settings first (confirm=False).
```

**Expected:** Settings shown for approval.

**Follow-up Prompt:**
```
Confirmed. Create the audio overview with confirm=True.
```

**Expected:** Audio generation started with artifact_id.

**Save:** Note the `artifact_id`.

---

### Test 5.2 - Create Video Overview (with confirmation)
**Tool:** `video_overview_create`

**Prompt:**
```
Create a video overview for notebook [notebook_id]:
- Format: brief
- Visual style: classic
- Language: en
Show me settings first.
```

**Expected:** Settings shown.

**Follow-up:**
```
Confirmed. Create it.
```

**Expected:** Video generation started.

---

### Test 5.3 - Check Studio Status
**Tool:** `studio_status`

**Prompt:**
```
Check the studio content generation status for notebook [notebook_id].
```

**Expected:** List of artifacts (audio, video) with status (in_progress or completed). URLs when completed.

---

### Test 5.4 - Delete Studio Artifact (with confirmation)
**Tool:** `studio_delete`

**Prompt:**
```
Delete the audio artifact [artifact_id from Test 5.1] from notebook [notebook_id].
First show me what will be deleted.
```

**Expected:** Error asking for confirmation.

**Follow-up:**
```
Confirmed. Delete it with confirm=True.
```

**Expected:** Artifact deleted successfully.

---

## Test Group 6: Studio - Other Formats

### Test 6.1 - Create Infographic (with confirmation)
**Tool:** `infographic_create`

**Prompt:**
```
Create an infographic for notebook [notebook_id]:
- Orientation: landscape
- Detail level: standard
- Language: en
Show settings first.
```

**Expected:** Settings shown.

**Follow-up:** Approve and create.

**Expected:** Infographic generation started.

---

### Test 6.2 - Create Slide Deck (with confirmation)
**Tool:** `slide_deck_create`

**Prompt:**
```
Create a slide deck for notebook [notebook_id]:
- Format: detailed_deck
- Length: short
Show settings first.
```

**Expected:** Settings shown, then generation starts.

---

### Test 6.3 - Create Report (with confirmation)
**Tool:** `report_create`

**Prompt:**
```
Create a "Briefing Doc" report for notebook [notebook_id].
Show settings first.
```

**Expected:** Settings shown, then generation starts.

---

### Test 6.4 - Create Flashcards (with confirmation)
**Tool:** `flashcards_create`

**Prompt:**
```
Create flashcards for notebook [notebook_id] with medium difficulty.
Show settings first.
```

**Expected:** Settings shown, then generation starts.

---

### Test 6.5 - Create Quiz (with confirmation)
**Tool:** `quiz_create`

**Prompt:**
```
Create a quiz for notebook [notebook_id] with 2 questions and difficulty level 2.
Show settings first.
```

**Expected:** Settings shown.

**Follow-up:** Approve and create.

**Expected:** Quiz generation started.

---

### Test 6.6 - Create Data Table (with confirmation)
**Tool:** `data_table_create`

**Prompt:**
```
Create a data table for notebook [notebook_id] extracting "Key features and capabilities" in English.
Show settings first.
```

**Expected:** Settings shown.

**Follow-up:** Approve and create.

**Expected:** Data table generation started.

---

## Test Group 7: Mind Maps

### Test 7.1 - Create Mind Map (with confirmation)
**Tool:** `mind_map_create`

**Prompt:**
```
Create a mind map titled "AI Concepts" for notebook [notebook_id].
Show settings first.
```

**Expected:** Settings shown.

**Follow-up:** Approve.

**Expected:** Mind map created immediately with mind_map_id.

**Save:** Note the `mind_map_id`.

---

### Test 7.2 - List Mind Maps
**Tool:** `mind_map_list`

**Prompt:**
```
List all mind maps in notebook [notebook_id].
```

**Expected:** List showing the mind map created in Test 7.1.

---

## Test Group 8: Drive Sync (Optional)

### Test 8.1 - Sync Drive Sources (with confirmation)
**Tool:** `source_sync_drive`

**Prompt:**
```
Check if any Drive sources in notebook [notebook_id] are stale using source_list_drive.
If any are stale, sync them using source_sync_drive.
```

**Expected:** Sources synced if any were stale.

**Note:** Skip if no Drive sources exist.

---

## Test Group 9: Cleanup

### Test 9.1 - Delete Notebook (with confirmation)
**Tool:** `notebook_delete`

**Prompt:**
```
Delete notebook [notebook_id]. Show me the warning first.
```

**Expected:** Error with warning about irreversible deletion.

**Follow-up:**
```
I confirm. Delete it with confirm=True.
```

**Expected:** Notebook deleted successfully.

---

## Summary Checklist

After completing all tests, verify:

- [ ] All 28 tools executed without errors
- [ ] Tools requiring confirmation properly blocked without confirm=True
- [ ] All create operations returned valid IDs
- [ ] All status checks returned expected structures
- [ ] All delete operations worked with confirmation
- [ ] Error messages were clear and helpful

---

## Tools Tested by Group

**Authentication (1):** save_auth_tokens

**Notebook Operations (5):** notebook_list, notebook_create, notebook_get, notebook_describe, notebook_rename

**Source Management (6):** notebook_add_url, notebook_add_text, notebook_add_drive, source_describe, source_list_drive, source_sync_drive

**AI Features (2):** notebook_query, chat_configure

**Research (3):** research_start, research_status, research_import

**Studio Audio/Video (4):** audio_overview_create, video_overview_create, studio_status, studio_delete

**Studio Other (6):** infographic_create, slide_deck_create, report_create, flashcards_create, quiz_create, data_table_create

**Mind Maps (2):** mind_map_create, mind_map_list

**Cleanup (1):** notebook_delete

**Total: 30 tools**

---

## Quick Copy-Paste Test Prompts

Use these prompts sequentially with another AI tool that has access to the MCP:

1. `List all my NotebookLM notebooks`
2. `Create a new notebook titled "MCP Test Notebook"`
3. `Get details of notebook [id]`
4. `Rename notebook [id] to "MCP Test - Renamed"`
5. `Add this URL to notebook [id]: https://en.wikipedia.org/wiki/Artificial_intelligence`
6. `Add text to notebook [id]: "Machine learning test document about AI algorithms"`
7. `List sources in notebook [id] with Drive status`
8. `Get AI summary of source [source_id]`
9. `Get AI summary of notebook [id]`
10. `Ask notebook [id]: "What is artificial intelligence?"`
11. `Configure notebook [id] chat: goal=learning_guide, response_length=longer`
12. `Start fast web research for "OpenShift" in notebook [id]`
13. `Check research status for notebook [id]`
14. `Import all research sources from task [task_id] into notebook [id]`
15. `Create brief audio overview for notebook [id] (show settings first)`
16. `Confirmed - create it with confirm=True`
17. `Create brief video overview for notebook [id] (show settings first)`
18. `Confirmed - create it`
19. `Check studio status for notebook [id]`
20. `Create landscape infographic for notebook [id] (show settings first)`
21. `Create short slide deck for notebook [id] (show settings first)`
22. `Create Briefing Doc report for notebook [id] (show settings first)`
23. `Create medium difficulty flashcards for notebook [id] (show settings first)`
24. `Create mind map titled "AI Concepts" for notebook [id] (show settings first)`
25. `List all mind maps in notebook [id]`
26. `Delete audio artifact [artifact_id] from notebook [id] (show warning first)`
27. `Confirmed - delete it with confirm=True`
28. `Delete notebook [id] (show warning first)` → `Confirmed - delete with confirm=True`
