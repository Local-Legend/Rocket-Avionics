# Local/remote synchronization audit

Date: 2026-10-27

## Verdict

**SYNCHRONIZED WITH INTENTIONAL ROLE-SPECIFIC FILES** — more precisely, **LOCAL FILES MATCH THE LAST RECORDED DEPLOYMENT STATE**.

This is an evidence-based local-only verdict, not a live remote checksum result. Both Raspberry Pis are powered off. SSH to `192.168.1.172` and `192.168.1.101` timed out before that was clarified; those timeouts are expected for powered-off hosts and are **not drift evidence**. Current remote equality, current remote file inventories, and current installed-copy hashes cannot be verified until the Pis are powered on.

The strongest available deployment records show:

- the 7 Hz/BW500 source set was checksum-matched on both Pis;
- the latest 20-point Obsidian-1 title file was checksum-matched on RX;
- the later GUI-only changes intentionally were not installed on TX;
- TX-only launcher, graph, command-note, and service artifacts are intentionally role-specific;
- no local generated/runtime artifacts were found outside `.agents`;
- the three authoritative `Grok_` scripts still exist, have pre-change timestamps, and are repeatedly documented as untouched.

## Scope and method

Read-only inspection only, except creation of this requested report. No project script was executed or imported. Nothing was copied, deployed, deleted, stopped, restarted, or modified. Local SHA-256 values were computed with `shasum -a 256`. Historical evidence was read from `.agents/tasks/`, `.agents/backup/`, and the persistent project history.

Remote backup directories, `__pycache__`, `.lgd-nfy*`, logs, databases, PID/temp files, and runtime `flight_*` folders are excluded from the intended source inventory.

## Local `System Scripts/` inventory and deployment comparison

There are 16 ordinary files directly in local `System Scripts/`; there are no direct local directories included in this table.

Legend:

- **Recorded match**: a deployment report gives the same hash, or records synchronized deployment and the local file has not changed afterward.
- **Role-specific**: later deployment records intentionally target only the Pi that uses the file/change.
- **Not live-verified**: the powered-off Pi could not be inventoried now.

| Local file | Local SHA-256 | TX project source | RX project source | Missing/drift assessment |
|---|---|---|---|---|
| `README.md` | `107e44b03d5a8f3b4d0c9e39a647c9981b9867d25479d16e21fa5afec7dc392c` | Last recorded TX copy is the pre-header 7 Hz version, hash `7ba50ffd20713eef19090850a7b5911b5541851e6ac414376be9fb11caf563f3` | **Recorded match** at current hash during RX header deployment | Intentional RX-only documentation delta; no functional TX requirement changed. Live presence unverified. |
| `RX_Temenetry.py` | `27fc46b4574336a59b107800052e249786c5e6da5caddd70f7c12766e313ac46` | Last recorded TX copy is the common 7 Hz version, hash `bc6f2c48a39f91e445ea6a4b4a96e66f8cc94185e35526ef7e9b5115f9117e6f` | **Recorded exact match** at current hash after the 20-point title deployment | Intentional RX-only GUI delta after common radio deployment. Live presence unverified. |
| `Rocket-RX.desktop` | `92e76c1df50e60a3b6b1fbf361fcc4026726d363513401667c8baeb2f76a0e90` | Recorded in earlier complete project deployment; not operationally used on TX | Recorded RX launcher source; local hash is unchanged from the 2026-10-08 pre-timeline backup | Role-specific operational use on RX; no evidence of drift. |
| `Rocket-TX-Graphs.desktop` | `0b2e88164407b14ed86f8cc9f6df62e9e6fcad9669b9303a164c2e51403fb46b` | Recorded deployed to TX with graph launcher on 2026-10-11 | No RX deployment recorded or required | Intentional TX-only file. |
| `Rocket-TX.desktop` | `922308dd4df231da8165582f498c293eef68ed3df338b237f651caedad8bc122` | Recorded installed for manual TX launch on 2026-10-09 | No RX deployment recorded or required | Intentional TX-only file. |
| `TX-Autostart-Commands.txt` | `f60df80a5331d48785c8d853c4616c015e54accd68d09c29a213bc6de0212907` | Recorded TX desktop command note updated with installed-service state | No RX deployment recorded or required | Intentional TX-only operational note. |
| `TX_Telemetry.py` | `24905505c497408d6b2e09d587d419045dfd68d1db55b76077206683dc2665b4` | **Recorded exact match** at current hash in 7 Hz deployment | **Recorded exact match** at current hash in 7 Hz deployment | No recorded missing copy or drift; live presence unverified. |
| `graph_latest_tx.sh` | `8750f303518eb87825d904f7351ee07e9e5d4f6ce4702daa3fd7c26d229a7de8` | Recorded deployed to TX on 2026-10-11 | No RX deployment recorded or required | Intentional TX-only file. |
| `install_tx_autostart.sh` | `93bc96c63a623f12b07b3f9c3b1570a8997ee4dda709986f5604d0596d28c736` | Recorded in complete deployment; local hash unchanged from pre-timeline backup | Project-source presence was part of earlier complete deployment but operationally unused | TX-role installer; no evidence of subsequent local change. |
| `make_rx_gui_shortcut.sh` | `13d7648a7b6a1ee0542bbc39f684d682a197bcf0cad6862c3be1b8a339c39393` | Recorded in earlier complete project deployment but operationally unused | Recorded RX launcher-generator source; local hash unchanged from pre-timeline backup | RX-role script; no evidence of drift. |
| `playback.py` | `f08c93e47f0d4c876fc8fb10ce41ca423e472d5c3ce0e23d83c7c9fa49639d28` | Recorded deployed in the 2026-10-27 G/altitude/temperature update | Recorded deployed in the same update | Deployment reported matching local files; no later local modification. Live hash unverified. |
| `rocket-tx.service` | `1c0cdb938768d6a935f7564d302308ac04bf0c6466048b5f13dfb241713da8e1` | Current service behavior was installed and `systemd-analyze verify` passed; see installed-copy section | No RX service is expected | Intentional TX-only installed unit; live source/installed hash unverified. |
| `start_rx.sh` | `3672b8aa8935dec60f62110b54582a8da1e39a77f722de2326ac06012572f328` | Recorded in earlier complete project deployment but operationally unused | Recorded RX launcher source; local hash unchanged from pre-timeline backup | RX-role script; no evidence of drift. |
| `start_tx.sh` | `c47f52699a49ea78f6aa7b39d7d8889f05afd721b5a614d1818967957ac81efc` | Recorded deployed with the manual TX launcher on 2026-10-09 | No RX deployment recorded or required | Intentional TX-only file. |
| `telemetry_db.py` | `c15f5e8ee05eea9ebd3238cd97ceb0a5c71a27a628a6975fee2471f0c82c5e1a` | Recorded as deployed/current during logging/playback work; unchanged in the 2026-10-27 updates | Recorded as deployed/current during logging/playback work; unchanged in the 2026-10-27 updates | No per-file current hash was preserved in reports, so live equality remains unverified. No drift evidence. |
| `telemetry_decode.py` | `be0cb1fc75e03e79c86e726258c1db541411c058678785232d5b80f242254651` | **Recorded exact match** at current hash in 7 Hz deployment | **Recorded exact match** at current hash in 7 Hz deployment | No recorded missing copy or drift; live presence unverified. |

### Key interpretation

The two known project-source differences are supported by explicit deployment evidence, not assumptions:

1. `RX_Temenetry.py`: after both Pis received the common BW500/7 Hz hash, three title-only updates were explicitly deployed **only to RX**, ending at the current local hash.
2. `README.md`: the RX header-layout deployment explicitly checksum-matched the current local README on RX and explicitly did not access TX. The TX's last recorded hash is therefore older. The later README edits document RX GUI presentation only; they do not alter TX runtime behavior.

These differences explain the **SYNCHRONIZED WITH INTENTIONAL ROLE-SPECIFIC FILES** verdict. They should not be “fixed” by blindly copying the entire folder between roles.

## Newest requested files/changes

| Item | Audit result |
|---|---|
| `TX_Telemetry.py` | Current local hash exactly equals both last recorded 7 Hz/BW500 deployed hashes. |
| `RX_Temenetry.py` | Current local hash exactly equals the last recorded RX deployment (20-point title). TX intentionally retains the common pre-GUI hash. |
| `telemetry_decode.py` | Current local hash exactly equals both last recorded 7 Hz/BW500 deployed hashes. |
| `telemetry_db.py` | Present locally; deployment history says current/unchanged on both, but no current per-file remote hash was retained. |
| `playback.py` | Present locally and recorded deployed to both in the G/altitude/temperature update; no later local edit. |
| `README.md` | Current local hash exactly equals last recorded RX hash; TX intentionally has the prior 7 Hz documentation hash. |
| `rocket-tx.service` | Present locally; recorded installed/enabled and verified on TX. Live source-to-installed checksum cannot be checked while off. |
| Install scripts | `install_tx_autostart.sh` present; unchanged since its recorded deployment. |
| Start scripts | `start_rx.sh` unchanged since recorded deployment; `start_tx.sh` recorded TX-only with manual launcher. |
| `Rocket-RX.desktop` | Present; RX-role launcher source unchanged since recorded deployment. |
| `Rocket-TX.desktop` | Present; recorded TX-only deployment. |
| `Rocket-TX-Graphs.desktop` | Present; recorded TX-only deployment on 2026-10-11. |
| `graph_latest_tx.sh` | Present; recorded TX-only deployment on 2026-10-11. |
| `TX-Autostart-Commands.txt` | Present; project history records the TX desktop command note updated for installed boot behavior. |

## Project source versus desktop and installed copies

These are separate synchronization surfaces and must not be conflated:

- **TX desktop copies:** `/home/pi/Desktop/Rocket-TX.desktop`, `/home/pi/Desktop/Rocket-TX-Graphs.desktop`, and the TX command note are recorded as installed/updated from the corresponding local sources. Their current hashes cannot be read while TX is off.
- **RX desktop copies:** `/home/pi/Desktop/Rocket-RX.desktop` is sourced from local `System Scripts/Rocket-RX.desktop`. `Rocket-RX-GUI.desktop` is generated by `make_rx_gui_shortcut.sh`; it is intentionally a generated remote-only desktop file rather than a missing local source file.
- **Installed TX service:** `/etc/systemd/system/rocket-tx.service` is separate from `System Scripts/rocket-tx.service`. History records that the current unit was installed, enabled, and passed `systemd-analyze verify`. Its current installed hash cannot be compared while TX is off.
- **RX service:** no persistent `rocket-tx.service` or persistent RX service is expected on RX. The final GUI used a transient per-user unit/manual desktop launch; this is an intentional role difference.

No historical evidence identifies an unexpected remote ordinary source/config file absent locally. A definitive remote-extra-file check is impossible until both hosts can be listed live. Known remote-only items are intentional generated desktop entries, installed systemd state, backup directories, runtime logs/databases, and flight folders.

## Authoritative `Grok_` scripts

All three required local files exist:

| File | SHA-256 | Size | Local modification time |
|---|---|---:|---|
| `Grok_Cam_Test.py` | `93da7a44f6aaa0f88e7b3cf0f81aa4e5c9b3c3e5fc138c2ebb4c8fe95295ed2e` | 528 bytes | 2026-10-08 06:12:18 -0400 |
| `Grok_GPSTest.py` | `07270403b21ca4c99c0f1492e43b7b3deb096d877023ea70dd8f3205ca8f612c` | 639 bytes | 2026-10-08 06:12:24 -0400 |
| `Grok_TX_test.py` | `4bffdfa8cea3ed19532ce51922ded229c2b0ddac646b128f95c4b067c5b725cc` | 332 bytes | 2026-10-08 06:12:18 -0400 |

Evidence of preservation:

- Their modification times precede the System Scripts update/deployment work.
- The 2026-10-08 update summary names all three as “Authority (untouched).”
- The update plan and later history repeatedly require the `Grok_` files to remain untouched.
- Subsequent deployment summaries list changed files and do not include any `Grok_` file.

Limitation: no independent original SHA-256 manifest or byte-for-byte backup of these three files was found, so exact equality to their first-created content cannot be cryptographically proven. The timestamp and documentary evidence strongly support that recent work did not modify them.

## Local runtime/generated artifact scan

A read-only scan outside `.agents` found **none** of the following:

- `__pycache__` directories
- `*.pyc`
- `*.db`, `*.sqlite`, or `*.sqlite3`
- `*.log`
- `*.pid`
- `*.tmp`
- `.lgd-nfy*`
- `flight_*` runtime directories

This satisfies the requested local cleanliness check. Reports, screenshots, and backups under `.agents` were intentionally excluded.

## Process and service context

Current physical state supplied by the user: **TX and RX are powered off**. Therefore there are no currently running in-memory TX/RX processes to report, and no read-only live `systemctl` query is possible.

Last recorded powered-on state (historical context only, not current state):

- TX: `rocket-tx.service` enabled and active, final recorded PID 1731 after the 7 Hz deployment.
- RX: one live GUI process, final recorded PID 4833 after the 20-point title deployment; no persistent RX system service configuration was changed.

PIDs and active states are stale by definition after power-off and must not be presented as current.

## Exact remediation / next verification

No file remediation is justified from the available evidence. Do **not** copy the whole local folder over both Pis merely to make role-specific files identical.

When the Pis are next powered on, perform this read-only verification before any deployment:

1. Inventory and SHA-256 every ordinary direct file in each remote `System Scripts/`, applying the exclusions in this audit.
2. Compare TX/RX common files against the local hashes above.
3. Confirm the intentional older TX hashes for `RX_Temenetry.py` and `README.md`, unless the user explicitly wants RX GUI source/docs mirrored onto TX.
4. Compare `/home/pi/Desktop/Rocket-TX.desktop`, `/home/pi/Desktop/Rocket-TX-Graphs.desktop`, the TX command note, and `/etc/systemd/system/rocket-tx.service` to their local source counterparts.
5. Compare RX desktop launcher copies, treating generated `Rocket-RX-GUI.desktop` as generated output from `make_rx_gui_shortcut.sh`.
6. List unexpected remote source/config files after excluding backups and runtime artifacts.

If a live mismatch is then found, remediate only the exact mismatched role-appropriate file(s), with a timestamped remote backup and normal syntax/service validation. No such remediation was performed in this audit.
