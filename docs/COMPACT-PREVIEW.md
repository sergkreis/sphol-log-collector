# Compact expanded-mode local preview

Local only; no publication, deployment, release or live-PC changes. Linux source Tk is not Windows/frozen acceptance.

- Character → one overall status → separately labelled combat counters → primary Start/Stop.
- Main combat pending counts only authenticated-listener eligible rows; anonymous history remains untouched with a small local-storage badge. Collected counts explicitly cover all characters this launch. Observation accepted counts remain independent; either stream's failure overrides healthy overall presentation.
- Observations disclose fleet warp, module range and target invulnerability only, excluding Chatlogs/routes. Approved consent hides its button. Consent, scoped credentials, independent queue and durable per-ID ACK paths remain unchanged.
- Main is 780×700; settings switch to a scrollable view at 780×750 rather than growing beyond the screen. Closing settings restores the main view.
- Preview fixtures each create fresh app/queues/updater under temporary directories, block socket connections and use a synthetic HTTP responder. Active, approval-needed, observation-offline and settings screenshots are independent. No update-error state can leak between scenes.

Run `xvfb-run -a -s '-screen 0 1024x768x24' /usr/bin/python3 -m tools.gui_preview /tmp/sphol-compact-preview` and `xvfb-run -a /usr/bin/python3 -m unittest discover -s tests`.

Gates cover consent decline/no capture; per-stream validated ACK; combat success with observation failure and converse; no ACK refresh from empty polls; retained anonymous rows; Stop ending both streams; capture error stopping both streams; updater blocked during capture; footer required-height bounds; settings scrolling to bottom. Privacy classifiers, transport validation and server admission are unchanged by this UI patch. Parent code/UX review remains a separate acceptance gate.
