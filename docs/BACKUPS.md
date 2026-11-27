# Restore a legacy backup

Purevault supports restoring Version 1 encrypted backup files created by earlier releases. Portable export and scheduled backups have been removed. Existing files may still be present in the data volume. They remain encrypted with their original recovery passphrases, and the app no longer lists or downloads them.

1. Create a target Purevault account with an empty vault.
2. Open **Settings → Restore backup** and select the encrypted file.
3. Enter the backup's original recovery passphrase and the target vault's current master password, then submit the restore.

Every imported entry starts locked and requires its full unlock delay. The restore preserves entry titles, passwords, unlock delays, relock settings, and the display timezone. The target account and encryption key stay in use. Version 1 files support up to 2,000 entries and 12 MiB. If you use an HTTPS proxy, allow at least 12 MiB plus 16 KiB of upload overhead on the restore route.

Anyone with a legacy file and its recovery passphrase can decrypt that file outside Purevault, including passwords that are locked in the app. For recovery from Docker storage, see [Backup and restore](DEPLOYMENT.md#backup-and-restore).
