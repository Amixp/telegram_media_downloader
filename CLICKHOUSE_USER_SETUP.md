# ClickHouse User Setup

## Creating a Dedicated User for telegram_downloader Database

### 1. Create the User

```bash
docker exec clickhouse-tgd-server clickhouse-client --query "CREATE USER IF NOT EXISTS telegram_downloader IDENTIFIED BY 'telegram_downloader_pass'"
```

### 2. Grant Permissions

```bash
docker exec clickhouse-tgd-server clickhouse-client --query "GRANT ALL ON telegram_downloader.* TO telegram_downloader"
```

### 3. Update config.yaml

Edit the `clickhouse` section in `config.yaml`:

```yaml
clickhouse:
  database: telegram_downloader
  user: telegram_downloader
  password: telegram_downloader_pass
  host: localhost
  port: 9000
  enabled: true
```

### 4. Verify User Creation

```bash
docker exec clickhouse-tgd-server clickhouse-client --query "SELECT name, host_ip FROM system.users WHERE name = 'telegram_downloader'"
```

Expected output:
```
telegram_downloader	['::/0']
```

### 5. Test Connection

```bash
docker exec clickhouse-tgd-server clickhouse-client --user telegram_downloader --password telegram_downloader_pass --query "SELECT user(), currentDatabase()"
```

## Security Notes

- Change the default password `telegram_downloader_pass` to a strong password in production
- Consider restricting user access to specific hosts if needed
- Review and limit permissions based on actual requirements
