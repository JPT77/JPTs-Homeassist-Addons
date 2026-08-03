# Home Assistant Statistics Export Add-on

This add-on exports historical Home Assistant statistics data into Parquet files.

The exported files can be copied to a PC or NAS and used for long-term archiving, analysis, or further processing.

## Features

* Exports data from Home Assistant `statistics_short_term`
* Stores data as Parquet files
* Runs automatically on a daily schedule
* Configurable export time
* Automatically removes old export files
* Stores output outside the add-on container using `/share`

## Installation in Home Assistant

### 1. Add the Add-on Repository

In Home Assistant:

1. Open:

   ```
   Settings → Apps → App Store
   ```

2. Click:

   ```
   ⋮ → Repositories
   ```

3. Add the repository URL:

   ```
   https://github.com/JPT77/JPTs-Homeassist-Addons/
   ```

4. Click:

   ```
   Add
   ```

5. Reload the App Store.

The add-on will now appear as:

```
Statistics Export
```

under available apps.

---

## 2. Install the Add-on

1. Open:

   ```
   Settings → Apps → Statistics Export
   ```

2. Click:

   ```
   Install
   ```

3. After installation completes:

   ```
   Start
   ```

4. Enable if desired:

   ```
   Start on boot
   ```

---

## 3. Configuration

The add-on can be configured from the **Configuration** tab.

Example:

```yaml
export_hour: 1
export_minute: 0
keep_days: 28
output_dir: export
```

Configuration options:

| Option          | Description                                 | Default  |
| --------------- | ------------------------------------------- | -------- |
| `export_hour`   | Hour when the daily export runs             | `1`      |
| `export_minute` | Minute when the daily export runs           | `0`      |
| `keep_days`     | Number of days to keep exported files       | `28`     |
| `output_dir`    | Directory below `/share` for exported files | `export` |

---

## 4. Export Files

The generated files are stored in:

```
/share/export/
```

Example:

```
statistics_2026-08-02.parquet
statistics_2026-08-03.parquet
```

The files are stored outside the add-on container and will survive:

* add-on updates
* container rebuilds
* Home Assistant restarts

---

## 5. Check Logs

Execution status can be monitored here:

```
Settings
→ Apps
→ Statistics Export
→ Logs
```

Example output:

```
Statistics Export started
Schedule: every day at 01:00
Next run scheduled at 2026-08-04 01:00:00
Successfully exported 12345 rows
```

---

## Updates

Updates are distributed through the normal Home Assistant add-on update mechanism.

To check for updates:

1. Reload repositories:

   ```
   Settings → Apps → App Store → ⋮ → Reload
   ```

2. If a new version is available:

   ```
   Statistics Export → Update
   ```

Exported files in:

```
/share/export
```

will not be affected.

---

## Uninstall

To remove the add-on:

```
Statistics Export
→ Uninstall
```

The exported files remain in:

```
/share/export/
```

and can be removed manually if no longer required.

---

## Requirements

* Home Assistant OS or a compatible Home Assistant installation
* Access to the Home Assistant database
* Enabled statistics collection for the desired sensors

## License

For personal use and self-hosted Home Assistant installations.
