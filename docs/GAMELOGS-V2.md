# Safe expanded Gamelogs — local-only candidate

The preview GUI stays unmounted/default-off until native acceptance and server rollout approval. No release or live upload is implied.

Explicit gamelogs:write browser scope plus capture consent is required. Existing combat credentials, queue IDs, CurrentUser DPAPI and per-ID durable ACK semantics are unchanged. ExpandedQueue is separate; local recording files are never read by an uploader.

Only bounded exact notify/None templates are recognized: fleet warp, module range, target invulnerability. Dynamic commander/module/range fields are removed before queue insertion. Only canonical fixed Russian text is transmitted; server independently rejects anything else. Unknown, personal, commerce, links, self-destruct, Chatlogs and route/system information are excluded. Route collection is unimplemented and requires separate future default-off consent. Existing raw schema 2 backlog is preserved and blocked, never rewritten/deleted or uploaded.

Combat capture remains independent through schema 1. Existing server damage, incoming shield repair and tackle-attempt analysis is preserved. Notifications are source observations, not victim-specific facts or complete fleet coverage. Generic module failures, additional repair/warp forms and routes remain unimplemented. Earlier verified Russian template shapes are tested with synthetic fixtures, not live localized acceptance.

Run python3 -m unittest discover -s tests -v. Set SPHOL_SERVER_PATH to the isolated integration server and EVE_DASHBOARD_NO_COLLECTOR=1 for real loopback HTTP pairing/Tailer/SQLite/ACK, sortie association, privacy rejection and per-killmail damage/repair/tackle JSON proof. Native Windows Tk, DPAPI, frozen EXE and browser acceptance remain separate gates.
