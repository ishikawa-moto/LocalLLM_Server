# ClientPC setup

1. On ServerPC, run `Install-Server-Admin.cmd` once and approve the Windows administrator prompt.
2. Run `Prepare-Client-Package.cmd` and choose a new 12-character-or-longer PFX password. This writes the encrypted ZIP and `Install-LocalBrain-On-ClientPC.cmd` launcher together. Keep the password separate from the ZIP.
3. For a temporary ClientPC-only transfer share, run `Create-Temporary-Client-Share.ps1` as administrator. From ClientPC open `\\SERVER_HOST\LocalBrainTransfer`, then run `Install-LocalBrain-On-ClientPC.cmd`. It copies the ZIP locally before extraction, prompts for the PFX password and first Git workspace, installs the client, and verifies the loopback listener.
4. After installation is verified, run `Remove-Temporary-Client-Share.ps1` as administrator on ServerPC to remove both the share and its TCP SMB_PORT firewall rule.
5. Restart VS Code and select **LocalBrain Qwen 27B** in Continue Agent mode.
6. Use Continue for planning and implementation. LOW work uses no Codex, NORMAL work uses one final review, and HIGH work uses an independent plan review and final review.

The bridge starts at Windows logon. The first Continue request starts the model on ServerPC. After 30 minutes without a model request and with no active or queued job, the model stops. Heartbeat loss never interrupts an active inference. A later request starts the model again without requiring resubmission.

SecondBrain read tools are available through the local MCP adapter. Writeback, decision, merge, and supersede tools create proposals only and require user approval. The ServerPC idle librarian grows verified knowledge from `raw` without invoking Codex automatically.

`SERVER_HOST` is a placeholder for the configured ServerPC hostname or address. Documentation-only example IPs must not be used as the actual connection target.
