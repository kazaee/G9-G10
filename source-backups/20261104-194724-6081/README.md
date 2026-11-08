# Bare-metal provisioning prototype

Local HPE iLO provisioning panel with tested Ubuntu flows and experimental Windows
Server 2022 and HPE ESXi installers. The Windows path uses the original DVD ISO
and a per-installation virtual USB answer image. ESXi uses a per-host Kickstart
ISO. Hardware support, network configuration, and final OS boot must be verified
for each image and server model before production use.

The panel binds to loopback. Its HTTP media server must remain running while iLO
uses virtual media. This repository contains source and tests; local ISO images,
credentials, generated answer media, and installation logs are excluded from Git.

## Local installation window

Double-click `Start-Provisioner.cmd`, or run `python app.py --open`. The local
browser window asks for iLO address, username and password, then guides Ubuntu
selection, live disk/NIC discovery, network and administrator credentials, YAML
partition-plan editing, ISO preparation and the single INSTALL confirmation.
It binds only to 127.0.0.1 and requires the random session URL printed at startup.
If the panel is already running on its default port, launching it again opens
the authenticated existing session instead of reporting a port conflict. If a
different program owns the port, the launcher still reports the conflict. Keep
the panel process running during installation because it also serves virtual
media to iLO; closing only the browser tab does not stop that process.
iLO credentials remain in process memory and are not returned by its status API.
Live discovery reboots the server; its explicit checkbox describes that action.
After connecting, the app reuses a prior live inventory when the iLO serial and
RAID volume WWNs and sizes still match. Otherwise the bottom action starts live
discovery and offers the partition plan. Live discovery leaves the server powered
on; the later installer boot uses a warm restart. Once discovery finishes, the
same action prepares the installation ISO.
Disabled controls show an unavailable cursor; a waiting cursor is used only during
an active operation, whose elapsed time appears in the status box.
The app opens and captures iLO VSP through OpenSSH automatically. Keep this process
running until installation verification completes. OpenSSH and OpenSSL must be on PATH.

The window also provides iLO power actions, local custom ISO mount/one-time boot,
booting a user-supplied rescue ISO, and a text console through iLO VSP. The app
must remain running while a hosted custom ISO is in use. The panel creates a
separate iLO browser session and opens the vendor's HTML5 console at `/irc.html`
in a new tab. Redfish authentication alone does not authenticate that browser
page. iLO's `X-Frame-Options: sameorigin` prevents embedding it locally.
Offline Ubuntu/Windows password recovery is not implemented.

When no NIC has carrier, the installation window proposes the first discovered
port and shows its MAC for review. The user can choose another discovered port.
The chosen MAC receives a static netplan file with the supplied IP, gateway and
DNS even if link or ping is unavailable. Network reachability remains a separate
verification result; no failed ping blocks offline OS installation. Prior installs
made with deferred network selection are not retroactively changed by this update.

The default plan now gives 1 GiB to EFI on UEFI systems, 2 GiB to `/boot`, 8 GiB
swap below 1 TiB (16 GiB above), and all remaining selected-disk space to `/`.
Other disks remain untouched unless explicitly added to the reviewed plan.

The OS administrator can use a chosen password of at least 12 characters, or a
generated password saved locally in `generated/login-credentials.json`. OpenSSL
hashes it through stdin; only its hash enters the ISO. This file is ignored by Git
and is not served by the local UI. A generated password is for the newly installed
account, not an existing OS. The current config enables console and SSH password
login for the sudo account `provision`, alongside the generated SSH key. The root
account itself stays locked, as in a normal Ubuntu installation.

## Test status

Ubuntu 24.04 and Windows Server 2022 have completed installations on test
hardware. ESXi 7.0.3 has completed an installer run, while final management-network
behavior needs more validation. Windows post-install networking and RDP need a
new live test after the first successful install exposed a disconnected selected NIC. These
results do not establish production readiness. Per-run evidence remains under
`logs/` and `generated/` on the test machine and is deliberately not committed.

Debian 11.0 DVD-1, Debian 12.0 netinst, and Debian 13.7 netinst are available as experimental
unattended installers. Their SHA-256 digests, UEFI boot entries, and local
remastering have been checked; neither has completed a live installation yet.
The Debian preseed path currently accepts one reviewed GPT disk with direct
EFI/ext4/swap partitions. It rejects multi-disk, LVM, and software RAID plans
before reboot. The generated ISO contains a per-run preseed and first-boot
reporter. The panel reports success only after that reporter confirms Debian,
active SSH, and a network route; the selected ISO is ejected after confirmation.
Debian 13 netinst has fewer packages than DVD-1 and may need a reachable package
mirror for workloads beyond the included base packages. Validate both images on
the target hardware before treating them as supported for customers.

For new Windows installations, the answer image stages `SetupComplete.cmd` and a
post-install PowerShell script. They select the configured NIC by MAC, or the sole
linked NIC if that MAC is disconnected; configure static IPv4, gateway and DNS;
set Tehran time; enable NLA-protected RDP on TCP 33096 and its firewall rule;
apply the requested NetBIOS/SMB firewall rules; and create a separate local
`iranserver` recovery administrator. Its generated password is in the per-run
`windows-credentials.json` under ignored `logs/`. The script records its result
at `C:\Windows\Setup\Scripts\provision-postinstall.json` and sends a success or
failure event to the tokenized local media server. The panel marks success only
after a matching post-install success event; without a report it marks the outcome
unverified after six hours, never silently successful. For runs started before
this callback existed, an operator who has logged in through the graphical
console can use the manual verification control; it also checks the iLO power
state and the actual RDP protocol response on TCP 33096. Activation
requires a licensed company method and is not configured by this prototype.
ESXi and custom-ISO jobs also become explicitly unverified after six hours if
no independent completion evidence is available; the panel does not infer
success from ISO reads alone.
The Windows disk selector also offers an optional second disk for a data volume.
Selecting it includes that disk in the erase review; after Windows first boots,
the post-install script identifies it by a unique verified size and creates one
GPT/NTFS volume with the first free drive letter from `E:` onward. Without an explicit selection, every non-OS disk remains
untouched. Duplicate-size disks are refused rather than guessed.

## Run

On Linux or Windows with Python 3.10+, install `pip install -r requirements.txt`.
OpenSSH client (`ssh` and `ssh-keygen`) must be on PATH. On this workspace, portable
Python is available as `.tools/python/python.exe` and the two Python dependencies
have been installed locally from their upstream GitHub release sources because
the PyPI file host was unreachable.

```powershell
.\.tools\python\python.exe provision.py
```

On Linux: `python3 provision.py`. The interactive workflow asks for iLO and OS
network settings, boots a nondestructive Linux probe, generates a partition plan,
lets you edit it, and asks for `INSTALL` once before the real installation.

Copy `config/example.yaml` to `config/test.yaml` and fill in your own iLO,
network and media-host details for noninteractive commands. The local file is
ignored by Git. Supply `ILO_PASSWORD` through
the environment or use the hidden prompt. Passwords are excluded from source control; generated answer media and local
credential files may contain secrets and must stay in ignored directories. `.env` is ignored by Git; it is not automatically loaded.

```text
python provision.py discover
python provision.py mount-test
python provision.py live-probe
python provision.py plan
python provision.py generate --config generated/effective-config.yaml
python provision.py install --config generated/effective-config.yaml
```

`mount-test` mounts the original ISO, verifies `Inserted=true`, then ejects it.
`live-probe` boots a derived ISO and waits in autoinstall early-commands forever;
it never reaches partitioning. It tests connected NICs against the configured
gateway and returns Linux `lsblk`, by-id paths and networking. After receiving
the inventory, the tool powers off the live session before ejecting its root
media. The install command rereads your edited plan and powers on the server.
The probe also prints a compressed inventory record on iLO COM2. If a VSP console
capture is available, pass `live-probe --serial-log PATH` to recover that inventory
even without OS networking. Records are matched to the current probe ID, so an old
capture cannot substitute for a new hardware check. For scripted OpenSSH input to
iLO on Windows, use `ssh -T` and send `vsp` through stdin; forcing a local terminal
with `-tt` prevented pipe input in this test environment.
For already-reviewed automation, `--accept-plan INSTALL` supplies the single
confirmation explicitly. Do not use that switch until reviewing the disk list.

## Network and jump hosts

`ilo.address` accepts a host or `https://host:port`. A TCP failure recommends running
this same project on the jump host. A simple SSH local forward also works, e.g.
`ssh -N -L 8443:ILO_ADDRESS:443 operator@jump-host`, with the iLO address set to
`https://127.0.0.1:8443`. The ISO URL must still be reachable **from iLO**; tunnelling
the API does not tunnel media. `media.host` is the externally reachable address
of the machine running this tool, and `media.port` defaults to 8080.

The process must remain running throughout boot and installation. Expose its media
port to the iLO address and target OS address. It serves only the one ISO under a
random per-run URL and accepts status callbacks under that same token. It does
not expose the project directory. This test setup uses HTTP; the ISO contains the
public SSH key and target network settings, but never a private key or iLO password.

This Windows test host has a firewall rule named `ILO-test2 temporary media`,
allowing TCP/8080 only from the configured test iLO and OS IP. For a different
target, update that rule. After finishing tests, remove it with
`Remove-NetFirewallRule -DisplayName 'ILO-test2 temporary media'`.

`ilo.verify_tls: false` explicitly permits the test iLO's self-signed certificate.
Set it to true when using a trusted certificate. An installed DMTF `redfish` SDK
is used if available; otherwise a minimal Python HTTPS transport follows the same
discovered links. HTTP request logs contain method, path and status, not credentials.

## Disk plan

Physical RAID members are inventory, **not separate OS disks**. The live probe
correlates controller volume WWNs with Linux-visible disks. Plans reference
`/dev/disk/by-id/` paths and are checked again inside the installer before any wipe.
Unknown paths, changed capacity/WWN, oversize LVs, missing boot partitions and
duplicate mounts are rejected. Unselected disks receive no curtin disk actions.

Each `disks` entry has a GPT table and editable partitions. A partition can have
`filesystem` and `mount`, or `vg` for an LVM PV. Only a final partition can use
`size: -1`. LVs have explicit sizes. To separate boot and OS storage, retain EFI
and `/boot` on the boot disk and put the PV on another discovered disk. Remove
that second disk from `untouched`. NVMe is not prohibited; the default prefers
non-NVMe boot storage, and you can override it after checking firmware support.

The ISO remaster preserves El Torito optical BIOS/UEFI boot entries and adds an
unattended GRUB entry. It is intended for iLO virtual CD/DVD, not writing as a
hybrid USB disk image. The original ISO is unchanged. No SystemRescue download is
required; none was present in the supplied workspace.

When enabled, `media.ram_iso` changes how the image is read. After iLO loads the kernel and
initrd, Ubuntu's standard `iso-url` boot option downloads the same generated ISO
over the verified OS NIC into RAM. This avoids slow random reads through iLO 4.
Static early networking uses the observed interface and MAC; installed networking
still matches the MAC. The tool checks available RAM first. Set this option to
false to use virtual CD reads throughout installation. This is not PXE and needs
no additional server or protocol.

The current test instead sets `installation.offline: true` and `media.ram_iso:
false`. All installation data comes through iLO, APT uses offline fallback with
no mirror candidates, and completion does not depend on HTTP callbacks or SSH.
Capture the iLO COM2 VSP stream to `installation.serial_log` before installing.
The late hook emits a run-specific completion record, then an installed-system
systemd service emits OS version, new boot ID, disk/mount and persisted netplan
evidence after reboot. `OS_INSTALLED_NETWORK_PENDING` means these checks passed;
it does not claim network reachability. With `offline: false`, authenticated SSH
verification remains mandatory. `install --prepare-only` builds the complete ISO
and displays the exact disk plan without mounting it or erasing any disks.

If live discovery finds no physical carrier on any port and offline installation
is selected, an unknown MAC no longer blocks OS installation. The requested IP,
gateway and DNS are saved in `/etc/provision-network/intent.json`. A systemd timer
retries selection among the server's discovered physical MACs: a unique gateway
candidate wins, or a single connected port when the gateway does not answer.
Ambiguous ports are left pending. Only the selected MAC receives static netplan
configuration. Boot verification separately checks the saved intent and enabled
retry timer; it does not label pending networking as configured or reachable.

## Verification and evidence

Every command creates `logs/YYYY-MM-DD_HH-MM-SS/`. Discovery, network, plan, seed,
HTTP/installer events, completion evidence, SSH verification and final result are
saved where applicable. A successful TCP/22 connection alone never means success:
the tool requires an installer completion event, the new SSH host key, an
authenticated command, Ubuntu 24.04, the requested hostname/IP/gateway, a matching
run ID, and a different boot ID. The full mount and disk layout are recorded.

The default target user is `provision`. `identity.generate_password` and
`identity.ssh_password_login` control password access. If no public
key is configured, `generated/provision.key` is created locally. Keep it private;
it is ignored by Git. You can instead supply `identity.ssh_public_key` and the
matching `identity.ssh_private_key` path.

Tests: `python -m unittest discover -s tests -v`.

Protocol references:
- https://hewlettpackard.github.io/ilo-rest-api-docs/ilo4/
- https://canonical-subiquity.readthedocs-hosted.com/en/latest/reference/autoinstall-reference.html
- https://canonical-subiquity.readthedocs-hosted.com/en/latest/tutorial/providing-autoinstall.html
- https://curtin.readthedocs.io/en/latest/topics/storage.html
- https://manpages.ubuntu.com/manpages/noble/man7/casper.7.html
