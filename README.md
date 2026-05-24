# pbc-wrapper

A small Python wrapper around `proxmox-backup-client` that loads repository credentials from named profile files, so you can keep secrets out of your shell history and scripts.

## Requirements

- Python 3.9+
- `proxmox-backup-client` installed at `/usr/local/bin/proxmox-backup-client`

## Installation

### Unraid (recommended)

Build a Slackware `.txz` package that bundles the statically-linked
`proxmox-backup-client`, `pxar`, and `pbc-wrapper` together:

```bash
cd unraid
python3 build.py                        # pulls from Proxmox trixie repo (default)
python3 build.py --distro bookworm      # or bookworm
python3 build.py --list-packages        # see all available packages
```

The `.txz` is written to the `unraid/` directory. Install it via the Unraid
Community Applications plugin, or manually:

```bash
installpkg pbc-wrapper-*.txz
```

`doinst.sh` will create `/etc/pbs-client/repos.d/` (chmod 700) on install.

### Manual

```bash
sudo cp pbc-wrapper /usr/local/bin/pbc-wrapper
sudo chmod +x /usr/local/bin/pbc-wrapper
```

## Profile directory

Profiles are `.env` files stored in a directory. The wrapper checks these locations in order and uses the first one found:

| Path | Notes |
|---|---|
| `$PBS_PROFILE_DIR` | Environment variable override |
| `/mnt/user/appdata/pbs/repos.d` | Unraid default |
| `/boot/config/pbs/repos.d` | Unraid flash fallback |
| `/etc/pbs-client/repos.d` | Generic Linux |

You can also pass `--profile-dir DIR` to specify a directory directly.

Create the directory and lock it down:

```bash
mkdir -p /etc/pbs-client/repos.d
chmod 700 /etc/pbs-client/repos.d
```

## Profile files

Each profile is a file named `<name>.env` in the profile directory. See [`example.env`](example.env) for a full template.

Supported keys:

| Key | Required | Description |
|---|---|---|
| `PBS_REPOSITORY` | Yes | Repository string: `user@realm!token@host:datastore` |
| `PBS_PASSWORD` | Yes | API token secret |
| `PBS_FINGERPRINT` | Yes | TLS fingerprint of the PBS server |
| `PBS_KEYFILE` | No | Path to an encryption key file. Skipped silently if the file does not exist on disk. |
| `PBS_ENCRYPTION_PASSWORD` | No | Passphrase for an encrypted key file |

Secure your profile files:

```bash
chmod 600 /etc/pbs-client/repos.d/myrepo.env
```

## Usage

```
pbc-wrapper [--profile-dir DIR] [--log-level LEVEL] [--default NAME] [--list]
            <proxmox-backup-client subcommand and args…>
```

`--repository` (or `-r`) accepts either a **profile name** or a **full repository string**. If the value contains `:` or `@pbs@` it is treated as a direct repository string and passed through as-is; otherwise it is looked up as a profile name.

### Wrapper flags

| Flag | Description |
|---|---|
| `--profile-dir DIR` | Override the profile directory |
| `--log-level LEVEL` | Set log verbosity: `debug`, `info`, `warning`, `error` (default: `warning`) |
| `--default NAME` | Profile to use when `--repository` is not provided |
| `--list` | List available profiles and exit |
| `--help` / `-h` | Show usage and exit |

Log level can also be set via the `PBS_LOG_LEVEL` environment variable. The flag takes precedence.

### Examples

```bash
# List available profiles
pbc-wrapper --list

# List snapshots in a repository
pbc-wrapper --repository myrepo list

# List snapshots in a namespace
pbc-wrapper --repository myrepo list --ns mynamespace

# Back up a directory
pbc-wrapper --repository myrepo backup mydata.pxar:/path/to/data

# Restore a snapshot
pbc-wrapper --repository myrepo restore host/myhost/2026-01-01T00:00:00Z mydata.pxar /restore/path

# Mount an encrypted backup (keyfile loaded from profile automatically)
sudo pbc-wrapper --repository myrepo mount host/myhost mydata.pxar /mnt/pbs --ns mynamespace

# Unmount
sudo umount /mnt/pbs

# Use a default profile (no --repository needed)
pbc-wrapper --default myrepo list

# Verbose wrapper output for troubleshooting
pbc-wrapper --repository myrepo --log-level debug list
```

## PBS server permissions

For read-only operations (list, restore, mount) the API token needs at least the `DatastoreReader` role on the target datastore or namespace path. `DatastoreAudit` only allows listing — it will not allow reading backup content.

To grant access in the PBS web UI: **Datastore → &lt;name&gt; → Permissions → Add**, select your token, role `DatastoreReader`, and check **Propagate** so it applies to all namespaces.

## Encryption keys

If a backup was created with client-side encryption, you need the original `.key` file to restore or mount it. Check the fingerprint of a key file with:

```bash
proxmox-backup-client key show /path/to/file.key
```

Set `PBS_KEYFILE` in your profile to the path of the key file. The wrapper passes it as `--keyfile` on the command line (the `PBS_KEYFILE` environment variable is not honoured by all `proxmox-backup-client` subcommands). If the file does not exist the key is silently skipped, so the same profile works on hosts that may not have the key present.
