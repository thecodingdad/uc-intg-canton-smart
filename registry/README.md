# Integration Manager registry entry

The [uc-intg-manager](https://github.com/JackJPowell/uc-intg-manager) can install and update any
integration, but it only offers **configuration backup/restore** for integrations listed in
[JackJPowell/uc-intg-list](https://github.com/JackJPowell/uc-intg-list) with `supports_backup: true`
(see `intg-manager/backup_capabilities.py`).

Backup itself needs no extra code here: the manager drives the setup flow remotely
(`POST /intg/setup {reconfigure: true}` → `PUT` with `{choice, action: "backup"}` → reads the
`backup_data` field), which is exactly what `BaseSetupFlow` from `ucapi-framework` provides via its
"Backup configuration to clipboard" / "Restore configuration from backup" actions.

## Submitting the entry

1. Publish a release of this repository (tag `v1.0.0`), so the manager finds the `.tar.gz` asset.
2. Fork <https://github.com/JackJPowell/uc-intg-list>.
3. Append the contents of [registry-entry.json](registry-entry.json) to the `integrations` array in
   `registry.json`.
4. Open a pull request.

`driver_id` must match `driver.json` (`canton_smart`), and `backup_min_version` is the first release
that supports backup — `1.0.0` here, since the feature is present from the initial release.
