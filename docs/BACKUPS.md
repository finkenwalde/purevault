# Portable encrypted backups

Open **Settings → Backups**. Set a strong independent recovery passphrase and confirm with your current master password. Use several independently random words or a password manager. Save that passphrase separately from your vault and copies of the backup file. Purevault cannot recover a lost passphrase. Changing it affects future backups only. Keep the old passphrase for any older copies you still need.

Choose **Back up now**, then download an encrypted copy and store it off the Docker host. The server stores its encrypted copies in the existing data volume under `/data/backups`. Loss of that volume loses its local backups. The file and its recovery passphrase are sufficient to decrypt the entry passwords outside Purevault, including those currently waiting or locked. Anyone who has both can bypass the vault's access delays.

The optional schedule runs every 24 hours or seven days using the server's wall clock. It persists across restarts. Purevault checks for due work every 30 seconds and makes one catch-up attempt after downtime, then starts the next interval. An unsuccessful scheduled attempt retries at the next interval. You can run a backup manually without resetting the schedule. Retention defaults to seven successful copies and accepts 1–30. Pruning runs only after a new copy is written successfully. Download copies you want to keep beyond local retention.

## Restore on another installation

1. Install Purevault and create a target account in an empty vault.
2. Open **Settings → Backups**, select the file under **Restore into this vault**, and enter its recovery passphrase and the target vault's current master password.
3. Submit the restore. Every imported entry starts locked and requires its full configured delay. The restored display timezone appears in Settings. Configure backup scheduling separately on the target if desired.

The file contains titles, passwords, display timezone, and unlock and relock settings. Your account, master password, sessions, installation key, active grants, runtime clock and schedule are excluded. The target installation uses its own encryption key. A restore into a nonempty vault is rejected. Version 1 supports up to 2,000 entries and 12 MiB per file. For HTTPS proxy deployments, allow at least 12 MiB plus 16 KiB of multipart overhead on the restore route.

## Version 1 file format

All offsets are zero-based. Fields are raw bytes. AES-GCM ciphertext includes its 16-byte authentication tag at the end. KDF parameters are fixed by the format version.

| Offset | Bytes | Field |
| --- | ---: | --- |
| 0 | 8 | ASCII magic `PVBACK01` |
| 8 | 16 | Random scrypt salt |
| 24 | 12 | Random recovery AES-GCM nonce |
| 36 | 48 | Encrypted 32-byte raw X25519 recovery private key and tag |
| 84 | 32 | Raw ephemeral X25519 public key, newly generated for each file |
| 116 | 12 | Random payload AES-GCM nonce |
| 128 | remainder | Encrypted UTF-8 JSON payload and tag |

To recover independently, derive a 32-byte AES key using scrypt with the UTF-8 recovery passphrase (exact text), the header salt, and `N=65536, r=8, p=1`. Decrypt bytes 36–83 with this key and the recovery nonce. The additional authenticated data is ASCII `Purevault v1 recovery key`, followed by one zero byte and the 16 salt bytes. The result is a raw X25519 private key. Perform X25519 with the ephemeral public key from the same file. Use HKDF-SHA256 on that shared secret with a default zero salt, length 32, and info equal to ASCII `Purevault v1 file key` followed by one zero byte. Decrypt the payload with the derived key, the payload nonce, and the full 128-byte header as additional authenticated data. Verify both GCM tags before using the output.

The JSON object has keys `display_timezone` (IANA name) and `entries` (array). Each entry has `title`, `password`, `delay_seconds`, `relock_policy` (`manual` or `automatic`), and `relock_seconds` (null for manual, integer seconds otherwise). No entry metadata is stored outside the authenticated ciphertext. The source stores only the public recovery key and the passphrase-encrypted recovery bundle for scheduling.
